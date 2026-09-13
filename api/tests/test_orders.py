"""Phase 1.5 — manual order creation, against a real PostgreSQL.

The interesting tests here are the last two. Everything else is validation;
those two are the safety property.

Manual order creation is the first thing in the product that can add work to
an encounter the discharge gate has already cleared. If an order can be
inserted while a discharge is deciding, the encounter ends up discharged with
an outstanding investigation nobody owns -- the exact state the product exists
to prevent, reached without the gate ever being wrong.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from tests._phase5 import authenticate_as

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        conn = await engine.connect()
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    trans = await conn.begin()
    maker = async_sessionmaker(
        bind=conn,
        expire_on_commit=False,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with maker() as s:
            yield s
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from app.db.session import get_session
    from app.main import create_app

    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    # Phase 5.1 put every endpoint behind RBAC. These tests check
    # their own behaviour, not authentication -- see the helper.
    authenticate_as(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _fixture(session: AsyncSession, status: str = "active") -> dict[str, Any]:
    tag = uuid.uuid4().hex[:10]
    ids = {k: str(uuid.uuid4()) for k in ("dept", "user", "pat", "enc")}
    await session.execute(
        text("INSERT INTO departments (id, code, name) VALUES (:i, :c, 'D')"),
        {"i": ids["dept"], "c": f"D{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role) "
            "VALUES (:i, :e, 'Dr Test', 'doctor')"
        ),
        {"i": ids["user"], "e": f"E{tag}"},
    )
    await session.execute(
        text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, 'P')"),
        {"i": ids["pat"], "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, "
            "admitted_at, status, attending_doctor_id) "
            "VALUES (:i, :p, :n, 'ipd', now(), :s, :u)"
        ),
        {
            "i": ids["enc"],
            "p": ids["pat"],
            "n": f"ENC{tag}",
            "s": status,
            "u": ids["user"],
        },
    )
    await session.commit()
    ids["tag"] = tag
    return ids


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "test_code": "URC",
        "test_name": "Urine Culture",
        "category": "micro",
        "expected_tat_hours": "48",
    }
    body.update(overrides)
    return body


# ── the happy path ────────────────────────────────────────────────────


async def test_creates_an_order_on_an_active_encounter(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    response = await client.post(
        f"/api/encounters/{ids['enc']}/orders", json=_payload()
    )
    assert response.status_code == 201
    body = response.json()
    assert body["order"]["test_name"] == "Urine Culture"
    assert body["order"]["is_outstanding"] is True


async def test_readiness_reflects_the_new_order_immediately(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """An encounter that was dischargeable must stop being dischargeable the
    moment an outstanding order is added to it."""
    ids = await _fixture(session)

    before = (
        await client.get(f"/api/encounters/{ids['enc']}/discharge-readiness")
    ).json()
    assert before["can_discharge"] is True

    created = (
        await client.post(f"/api/encounters/{ids['enc']}/orders", json=_payload())
    ).json()
    # The create response carries the gate's re-derived answer...
    assert created["encounter_can_discharge"] is False
    assert created["blocking_order_count"] == 1

    # ...and a fresh read agrees with it.
    after = (
        await client.get(f"/api/encounters/{ids['enc']}/discharge-readiness")
    ).json()
    assert after["can_discharge"] is False
    assert [o["test_name"] for o in after["blocking_orders"]] == ["Urine Culture"]


async def test_a_final_result_does_not_block(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    await client.post(
        f"/api/encounters/{ids['enc']}/orders", json=_payload(status="collected")
    )
    readiness = (
        await client.get(f"/api/encounters/{ids['enc']}/discharge-readiness")
    ).json()
    assert readiness["can_discharge"] is False


async def test_patient_id_is_taken_from_the_encounter_not_the_client(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Phase 7.5 scores candidate result matches on orders.patient_id. A
    client-supplied value there would be a wrong-patient hazard by
    construction, so the field is not accepted at all."""
    ids = await _fixture(session)
    response = await client.post(
        f"/api/encounters/{ids['enc']}/orders",
        json=_payload(patient_id=str(uuid.uuid4())),
    )
    assert response.status_code == 422  # extra="forbid"

    created = (
        await client.post(f"/api/encounters/{ids['enc']}/orders", json=_payload())
    ).json()
    stored = (
        await session.execute(
            text("SELECT patient_id FROM orders WHERE id = :i"),
            {"i": created["order"]["id"]},
        )
    ).scalar_one()
    assert str(stored) == ids["pat"]


# ── validation ────────────────────────────────────────────────────────


async def test_unknown_encounter_is_404(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"/api/encounters/{uuid.uuid4()}/orders", json=_payload()
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_rejects_an_unknown_category(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    response = await client.post(
        f"/api/encounters/{ids['enc']}/orders", json=_payload(category="astrology")
    )
    assert response.status_code == 422


async def test_rejects_a_terminal_status_at_creation(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Creating an order that is already `final` would mean recording a
    result, which is Phase 6/7 -- and it would be a gate bypass: an order
    born final never blocks anything."""
    ids = await _fixture(session)
    for status in ("final", "cancelled", "rejected"):
        response = await client.post(
            f"/api/encounters/{ids['enc']}/orders", json=_payload(status=status)
        )
        assert response.status_code == 422, status


async def test_rejects_a_non_positive_tat(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Mirrors ck_orders_expected_tat_hours_positive, so the caller gets a
    field error rather than an IntegrityError."""
    ids = await _fixture(session)
    for bad in ("0", "-5"):
        response = await client.post(
            f"/api/encounters/{ids['enc']}/orders",
            json=_payload(expected_tat_hours=bad),
        )
        assert response.status_code == 422, bad


async def test_rejects_a_future_ordered_at(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    response = await client.post(
        f"/api/encounters/{ids['enc']}/orders",
        json=_payload(ordered_at="2099-01-01T00:00:00Z"),
    )
    assert response.status_code == 422


async def test_rejects_a_naive_ordered_at(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The gate derives its suggested deadline from this column. A naive
    value read against the server's locale is a deadline at the wrong hour."""
    ids = await _fixture(session)
    response = await client.post(
        f"/api/encounters/{ids['enc']}/orders",
        json=_payload(ordered_at="2026-03-14T10:00:00"),
    )
    assert response.status_code == 422


async def test_rejects_blank_required_fields(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    for field in ("test_code", "test_name"):
        response = await client.post(
            f"/api/encounters/{ids['enc']}/orders", json=_payload(**{field: "   "})
        )
        assert response.status_code == 422, field


async def test_rejects_an_unknown_ordering_user(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    response = await client.post(
        f"/api/encounters/{ids['enc']}/orders",
        json=_payload(ordered_by_user_id=str(uuid.uuid4())),
    )
    assert response.status_code == 422


async def test_rejects_a_duplicate_external_order_id(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Phase 7.5 matches results on the accession number first. Two orders
    sharing one is a wrong-patient hazard."""
    ids = await _fixture(session)
    accession = f"ACC-{ids['tag']}"
    first = await client.post(
        f"/api/encounters/{ids['enc']}/orders",
        json=_payload(external_order_id=accession),
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/encounters/{ids['enc']}/orders",
        json=_payload(test_code="CBC", external_order_id=accession),
    )
    assert second.status_code == 409


async def test_a_rejected_order_writes_nothing(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    await client.post(
        f"/api/encounters/{ids['enc']}/orders", json=_payload(category="astrology")
    )
    count = (
        await session.execute(
            text("SELECT count(*) FROM orders WHERE encounter_id = :e"),
            {"e": ids["enc"]},
        )
    ).scalar_one()
    assert count == 0


# ── CASE B: an order against an encounter that is already closed ──────


async def test_refuses_an_order_on_a_discharged_encounter(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session, status="discharged")
    response = await client.post(
        f"/api/encounters/{ids['enc']}/orders", json=_payload()
    )
    assert response.status_code == 409
    assert "no longer accepts new orders" in response.json()["title"]

    count = (
        await session.execute(
            text("SELECT count(*) FROM orders WHERE encounter_id = :e"),
            {"e": ids["enc"]},
        )
    ).scalar_one()
    assert count == 0


async def test_refuses_an_order_on_every_closed_status(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Not just `discharged`. lama, transferred and deceased all mean the
    episode of care is over, and an investigation ordered against one of them
    can never be tracked to a discharge."""
    for status in ("discharged", "lama", "transferred", "deceased"):
        ids = await _fixture(session, status=status)
        response = await client.post(
            f"/api/encounters/{ids['enc']}/orders", json=_payload()
        )
        assert response.status_code == 409, status


# ── CASE A: the race ──────────────────────────────────────────────────


async def test_order_creation_cannot_race_a_discharge(monkeypatch: Any) -> None:
    """Two independent connections: one discharges, one adds an order.

    **This is the test that matters in Phase 1.5.** A savepoint-scoped session
    cannot express it -- both halves would share a connection and serialise
    for the wrong reason. Real connections, real locks, real commits.

    The unsafe outcome is *discharged AND a new uncontracted outstanding
    order*. Both safe orderings are accepted, because which one wins is a
    matter of timing and neither is wrong:

    * discharge wins -> the order is refused with 409
    * order wins     -> the discharge is refused, blocked by the new order

    What is asserted is the invariant that holds in either case: **an
    encounter is never discharged while an outstanding order has no
    contract.** That is the product's entire safety claim, stated as an
    assertion over the database after the dust settles.
    """
    from app.schemas.orders import OrderCreate
    from app.services.discharge_action import (
        DischargeBlockedError,
        EncounterNotDischargeableError,
        discharge_encounter,
    )
    from app.services.orders import EncounterNotOrderableError, create_manual_order

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.connect():
            pass
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    ids: dict[str, Any] = {}

    try:
        async with maker() as setup:
            ids = await _fixture(setup)

        enc = uuid.UUID(ids["enc"])

        async def discharge() -> str:
            async with maker() as s:
                try:
                    await discharge_encounter(s, enc)
                except DischargeBlockedError:
                    return "discharge_blocked"
                except EncounterNotDischargeableError:
                    return "discharge_refused"
                except Exception as exc:  # surfaced, never swallowed
                    return f"discharge_error:{type(exc).__name__}"
                return "discharged"

        async def add_order() -> str:
            async with maker() as s:
                try:
                    await create_manual_order(
                        s,
                        enc,
                        OrderCreate(
                            test_code="RACE",
                            test_name="Race Order",
                            category="lab",
                        ),
                    )
                except EncounterNotOrderableError:
                    return "order_refused"
                except Exception as exc:
                    return f"order_error:{type(exc).__name__}"
                return "order_created"

        results = await asyncio.gather(discharge(), add_order())
        assert not any(r.endswith("_error") or "_error:" in r for r in results), results

        # Exactly one of the two safe outcomes, never a mix of both succeeding
        # in a way that leaves the gate bypassed.
        assert sorted(results) in (
            ["discharged", "order_refused"],
            ["discharge_blocked", "order_created"],
        ), results

        # ── the invariant, read back out of the database ──────────
        async with maker() as check:
            row = (
                await check.execute(
                    text(
                        "SELECT e.status, "
                        "  (SELECT count(*) FROM orders o "
                        "     LEFT JOIN discharge_contracts c "
                        "       ON c.order_id = o.id AND c.deleted_at IS NULL "
                        "    WHERE o.encounter_id = e.id "
                        "      AND o.deleted_at IS NULL "
                        "      AND o.status NOT IN ('final','cancelled','rejected') "
                        "      AND c.id IS NULL) AS uncontracted_outstanding "
                        "FROM encounters e WHERE e.id = :e"
                    ),
                    {"e": ids["enc"]},
                )
            ).first()
            assert row is not None
            status, uncontracted = row

            if status == "discharged":
                assert uncontracted == 0, (
                    "SAFETY VIOLATION: encounter discharged with "
                    f"{uncontracted} uncontracted outstanding order(s)"
                )
    finally:
        # Hard delete, because this test committed for real and these rows are
        # test scaffolding rather than clinical data. Children first.
        if ids:
            async with maker() as cleanup:
                # case_events is append-only and its trigger correctly refuses
                # a DELETE. Bypassed for cleanup only; session_replication_role
                # is connection-scoped, so unlike DISABLE TRIGGER a crash here
                # cannot leave the production guard off for everyone.
                await cleanup.execute(text("SET session_replication_role = replica"))
                for sql in (
                    "DELETE FROM case_events WHERE case_id IN "
                    "(SELECT id FROM pending_cases WHERE encounter_id = :e)",
                    "DELETE FROM sla_timers WHERE case_id IN "
                    "(SELECT id FROM pending_cases WHERE encounter_id = :e)",
                    "DELETE FROM lab_flags WHERE case_id IN "
                    "(SELECT id FROM pending_cases WHERE encounter_id = :e)",
                    "DELETE FROM results WHERE case_id IN "
                    "(SELECT id FROM pending_cases WHERE encounter_id = :e)",
                    "DELETE FROM pending_cases WHERE encounter_id = :e",
                    "DELETE FROM discharge_contracts WHERE encounter_id = :e",
                    "DELETE FROM orders WHERE encounter_id = :e",
                    "DELETE FROM encounters WHERE id = :e",
                    "DELETE FROM patients WHERE id = :p",
                    "DELETE FROM users WHERE id = :u",
                    "DELETE FROM departments WHERE id = :d",
                ):
                    await cleanup.execute(
                        text(sql),
                        {
                            "e": ids["enc"],
                            "p": ids["pat"],
                            "u": ids["user"],
                            "d": ids["dept"],
                        },
                    )
                await cleanup.execute(text("SET session_replication_role = origin"))
                await cleanup.commit()
        await engine.dispose()


async def test_order_after_a_committed_discharge_is_always_refused(
    monkeypatch: Any,
) -> None:
    """CASE B on real connections: discharge commits first, then an order is
    attempted. There is no timing subtlety here -- it must always be refused,
    and the encounter must be left exactly as the discharge left it."""
    from app.schemas.orders import OrderCreate
    from app.services.discharge_action import discharge_encounter
    from app.services.orders import EncounterNotOrderableError, create_manual_order

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.connect():
            pass
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    ids: dict[str, Any] = {}

    try:
        async with maker() as setup:
            ids = await _fixture(setup)

        enc = uuid.UUID(ids["enc"])

        async with maker() as s:
            await discharge_encounter(s, enc)

        async with maker() as s:
            with pytest.raises(EncounterNotOrderableError):
                await create_manual_order(
                    s,
                    enc,
                    OrderCreate(
                        test_code="LATE", test_name="Late Order", category="lab"
                    ),
                )

        async with maker() as check:
            count = (
                await check.execute(
                    text("SELECT count(*) FROM orders WHERE encounter_id = :e"),
                    {"e": ids["enc"]},
                )
            ).scalar_one()
            assert count == 0
    finally:
        if ids:
            async with maker() as cleanup:
                # case_events is append-only and its trigger correctly refuses
                # a DELETE. Bypassed for cleanup only; session_replication_role
                # is connection-scoped, so unlike DISABLE TRIGGER a crash here
                # cannot leave the production guard off for everyone.
                await cleanup.execute(text("SET session_replication_role = replica"))
                for sql in (
                    "DELETE FROM case_events WHERE case_id IN "
                    "(SELECT id FROM pending_cases WHERE encounter_id = :e)",
                    "DELETE FROM sla_timers WHERE case_id IN "
                    "(SELECT id FROM pending_cases WHERE encounter_id = :e)",
                    "DELETE FROM lab_flags WHERE case_id IN "
                    "(SELECT id FROM pending_cases WHERE encounter_id = :e)",
                    "DELETE FROM results WHERE case_id IN "
                    "(SELECT id FROM pending_cases WHERE encounter_id = :e)",
                    "DELETE FROM pending_cases WHERE encounter_id = :e",
                    "DELETE FROM discharge_contracts WHERE encounter_id = :e",
                    "DELETE FROM orders WHERE encounter_id = :e",
                    "DELETE FROM encounters WHERE id = :e",
                    "DELETE FROM patients WHERE id = :p",
                    "DELETE FROM users WHERE id = :u",
                    "DELETE FROM departments WHERE id = :d",
                ):
                    await cleanup.execute(
                        text(sql),
                        {
                            "e": ids["enc"],
                            "p": ids["pat"],
                            "u": ids["user"],
                            "d": ids["dept"],
                        },
                    )
                await cleanup.execute(text("SET session_replication_role = origin"))
                await cleanup.commit()
        await engine.dispose()
