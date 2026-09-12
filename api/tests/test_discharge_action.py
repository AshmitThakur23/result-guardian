"""Phase 1.3 — the discharge action, against a real PostgreSQL.

This is the endpoint the whole product exists to protect, so the tests are
about the invariant rather than the happy path: an encounter must not become
discharged while an investigation has no owner, and the five writes must land
together or not at all.

Most tests run inside an outer transaction that is rolled back. The two that
genuinely need independent connections -- concurrency, and the pgmq enqueue
surviving a real commit -- commit and clean up after themselves.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _fixture(session: AsyncSession, enc_type: str = "ipd") -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10]
    ids = {k: str(uuid.uuid4()) for k in ("dept", "user", "pat", "enc")}
    await session.execute(
        text("INSERT INTO departments (id, code, name) VALUES (:i, :c, 'D')"),
        {"i": ids["dept"], "c": f"D{suffix}"},
    )
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role) "
            "VALUES (:i, :e, 'Dr Test', 'doctor')"
        ),
        {"i": ids["user"], "e": f"E{suffix}"},
    )
    await session.execute(
        text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, 'P')"),
        {"i": ids["pat"], "m": f"MRN{suffix}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at) "
            "VALUES (:i, :p, :n, :t, now())"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"ENC{suffix}", "t": enc_type},
    )
    return ids


async def _order(
    session: AsyncSession,
    ids: dict[str, Any],
    status: str = "ordered",
    name: str = "Urine Culture",
) -> str:
    order_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            "category, ordered_at, status) "
            "VALUES (:i, :e, :p, 'T', :n, 'micro', now(), :s)"
        ),
        {"i": order_id, "e": ids["enc"], "p": ids["pat"], "n": name, "s": status},
    )
    return order_id


async def _contract(
    session: AsyncSession, ids: dict[str, Any], order_id: str, days: int = 2
) -> str:
    contract_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + make_interval(days => :d))"
        ),
        {
            "i": contract_id,
            "e": ids["enc"],
            "o": order_id,
            "u": ids["user"],
            "d": days,
        },
    )
    return contract_id


async def _state(session: AsyncSession, ids: dict[str, Any]) -> dict[str, Any]:
    enc = (
        await session.execute(
            text("SELECT status, discharged_at FROM encounters WHERE id = :i"),
            {"i": ids["enc"]},
        )
    ).first()
    cases = (
        await session.execute(
            text("SELECT count(*) FROM pending_cases WHERE encounter_id = :e"),
            {"e": ids["enc"]},
        )
    ).scalar()
    events = (
        await session.execute(
            text(
                "SELECT count(*) FROM case_events ce JOIN pending_cases pc "
                "ON pc.id = ce.case_id WHERE pc.encounter_id = :e"
            ),
            {"e": ids["enc"]},
        )
    ).scalar()
    return {
        "status": enc[0] if enc else None,
        "discharged_at": enc[1] if enc else None,
        "cases": int(cases or 0),
        "events": int(events or 0),
    }


async def _discharge(client: httpx.AsyncClient, encounter_id: str) -> httpx.Response:
    return await client.post(f"/api/encounters/{encounter_id}/discharge")


# ── success ───────────────────────────────────────────────────────────


async def test_discharge_with_no_outstanding_investigations(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    response = await _discharge(client, ids["enc"])

    assert response.status_code == 200, response.text
    assert response.json()["opened_cases"] == []

    state = await _state(session, ids)
    assert state["status"] == "discharged"
    assert state["discharged_at"] is not None
    assert state["cases"] == 0


async def test_discharge_with_one_contracted_investigation(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The full Exit Gate 1 shape: a case, its owner, its event, its timer."""
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    contract_id = await _contract(session, ids, order_id)

    response = await _discharge(client, ids["enc"])
    assert response.status_code == 200, response.text

    opened = response.json()["opened_cases"]
    assert len(opened) == 1
    assert opened[0]["order_id"] == order_id
    assert opened[0]["contract_id"] == contract_id
    assert opened[0]["current_owner_id"] == ids["user"]
    assert opened[0]["timer_msg_id"] > 0

    state = await _state(session, ids)
    assert state["status"] == "discharged"
    assert state["cases"] == 1
    assert state["events"] == 1

    row = (
        await session.execute(
            text(
                "SELECT state, current_owner_id, contract_id, patient_id "
                "FROM pending_cases WHERE encounter_id = :e"
            ),
            {"e": ids["enc"]},
        )
    ).first()
    assert row is not None
    assert row[0] == "awaiting_result"
    assert str(row[1]) == ids["user"]
    assert str(row[2]) == contract_id
    assert str(row[3]) == ids["pat"]

    event = (
        await session.execute(
            text(
                "SELECT ce.event_type, ce.payload FROM case_events ce "
                "JOIN pending_cases pc ON pc.id = ce.case_id "
                "WHERE pc.encounter_id = :e"
            ),
            {"e": ids["enc"]},
        )
    ).first()
    assert event is not None
    assert event[0] == "case_opened"
    assert event[1]["order_id"] == order_id


async def test_discharge_with_multiple_contracted_investigations(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    orders = []
    for i in range(3):
        oid = await _order(session, ids, name=f"Test {i}")
        await _contract(session, ids, oid)
        orders.append(oid)

    response = await _discharge(client, ids["enc"])
    assert response.status_code == 200, response.text
    assert {c["order_id"] for c in response.json()["opened_cases"]} == set(orders)

    state = await _state(session, ids)
    assert state["cases"] == 3
    assert state["events"] == 3

    # One case per order, no duplicates.
    distinct = (
        await session.execute(
            text(
                "SELECT count(DISTINCT order_id) FROM pending_cases "
                "WHERE encounter_id = :e"
            ),
            {"e": ids["enc"]},
        )
    ).scalar()
    assert distinct == 3


async def test_mixed_encounter_only_outstanding_orders_become_cases(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Resolved orders have nothing left to track."""
    ids = await _fixture(session)
    for status in ("final", "cancelled", "rejected"):
        await _order(session, ids, status, name=f"Done {status}")
    tracked = await _order(session, ids, "in_lab", name="Blood Culture")
    await _contract(session, ids, tracked)

    response = await _discharge(client, ids["enc"])
    assert response.status_code == 200, response.text

    rows = (
        await session.execute(
            text("SELECT order_id FROM pending_cases WHERE encounter_id = :e"),
            {"e": ids["enc"]},
        )
    ).scalars()
    assert [str(r) for r in rows] == [tracked]


# ── refusal ───────────────────────────────────────────────────────────


async def test_uncontracted_order_blocks_and_changes_nothing(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The core invariant. A refusal must leave the encounter untouched."""
    ids = await _fixture(session)
    order_id = await _order(session, ids)

    response = await _discharge(client, ids["enc"])

    assert response.status_code == 409
    body = response.json()
    assert body["blocking_orders"][0]["order_id"] == order_id

    state = await _state(session, ids)
    assert state["status"] == "active"
    assert state["discharged_at"] is None
    assert state["cases"] == 0
    assert state["events"] == 0

    # Scoped to this encounter, not a global queue count: the queue is shared
    # and another test's message would make a global assertion flap.
    queued = (
        await session.execute(
            text(
                "SELECT count(*) FROM pgmq.q_sla_timers "
                "WHERE message->>'encounter_id' = :e"
            ),
            {"e": ids["enc"]},
        )
    ).scalar()
    assert queued == 0


async def test_partially_contracted_still_blocks(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    owned = await _order(session, ids, name="Owned")
    await _contract(session, ids, owned)
    await _order(session, ids, name="Orphan")

    assert (await _discharge(client, ids["enc"])).status_code == 409
    state = await _state(session, ids)
    assert state["status"] == "active"
    assert state["cases"] == 0


async def test_already_discharged_is_a_conflict_not_a_duplicate(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A second POST must not open a second set of cases, events and timers."""
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    await _contract(session, ids, order_id)

    assert (await _discharge(client, ids["enc"])).status_code == 200
    first = await _state(session, ids)

    second = await _discharge(client, ids["enc"])
    assert second.status_code == 409
    assert "already" in second.json()["title"].lower()

    assert await _state(session, ids) == first


async def test_opd_has_no_discharge_action(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session, enc_type="opd")
    response = await _discharge(client, ids["enc"])
    assert response.status_code == 409
    assert (await _state(session, ids))["status"] == "active"


async def test_unknown_encounter_404(client: httpx.AsyncClient) -> None:
    assert (await _discharge(client, str(uuid.uuid4()))).status_code == 404


async def test_malformed_uuid_422(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/encounters/not-a-uuid/discharge")
    assert response.status_code == 422


# ── the client cannot influence the decision ──────────────────────────


async def test_stale_readiness_does_not_authorise_discharge(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The scenario the build plan means by "never trust the client".

    A doctor loads the gate screen while everything is contracted, a new
    investigation is ordered behind them, and they click Discharge. The server
    must re-derive readiness and refuse.
    """
    ids = await _fixture(session)
    owned = await _order(session, ids)
    await _contract(session, ids, owned)

    readiness = (
        await client.get(f"/api/encounters/{ids['enc']}/discharge-readiness")
    ).json()
    assert readiness["can_discharge"] is True  # what the frontend still holds

    # The world moves on.
    await _order(session, ids, name="Ordered after the screen loaded")

    response = await _discharge(client, ids["enc"])
    assert response.status_code == 409
    assert (await _state(session, ids))["status"] == "active"


async def test_body_cannot_assert_readiness(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """There is no field a caller can set to force a discharge through."""
    ids = await _fixture(session)
    await _order(session, ids)

    response = await client.post(
        f"/api/encounters/{ids['enc']}/discharge",
        json={"can_discharge": True, "force": True},
    )
    assert response.status_code == 409
    assert (await _state(session, ids))["status"] == "active"


async def test_readiness_after_discharge_is_consistent(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    await _contract(session, ids, order_id)
    assert (await _discharge(client, ids["enc"])).status_code == 200

    after = (
        await client.get(f"/api/encounters/{ids['enc']}/discharge-readiness")
    ).json()
    assert after["blocking_orders"] == []
    assert [c["order_id"] for c in after["already_contracted"]] == [order_id]


# ── rollback ──────────────────────────────────────────────────────────


async def test_failure_mid_transaction_rolls_everything_back(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Force the pgmq enqueue to fail after the encounter and cases are
    written. Nothing may survive -- a discharged encounter whose timers were
    never queued is exactly the silent failure this product exists to prevent.
    """
    import app.services.discharge_action as action

    ids = await _fixture(session)
    order_id = await _order(session, ids)
    await _contract(session, ids, order_id)
    # Release the savepoint so the fixture survives the rollback below and the
    # test isolates what the *discharge* wrote, not the setup.
    await session.commit()

    original = action.SLA_TIMER_QUEUE
    action.SLA_TIMER_QUEUE = "queue_that_does_not_exist"
    try:
        with pytest.raises(Exception, match=r"(?i)does not exist|undefined"):
            await action.discharge_encounter(session, uuid.UUID(ids["enc"]))
    finally:
        action.SLA_TIMER_QUEUE = original

    await session.rollback()

    # The fixture is still here; everything the discharge wrote is not.
    state = await _state(session, ids)
    assert state["status"] == "active", "encounter was left discharged"
    assert state["discharged_at"] is None
    assert state["cases"] == 0, "a pending case survived a failed discharge"
    assert state["events"] == 0, "a case event survived a failed discharge"


# ── the real race ─────────────────────────────────────────────────────


async def test_concurrent_discharges_produce_exactly_one(monkeypatch: Any) -> None:
    """Two independent connections discharge the same encounter at once.

    A savepoint-scoped session cannot test this: both would share a
    connection. The guarantee under test is the ``SELECT ... FOR UPDATE`` on
    the encounter row -- the loser blocks until the winner commits, then sees
    ``status = 'discharged'`` and refuses. Without that lock both would read
    "ready" and both would open a case set.

    Commits for real, then cleans up.
    """
    import asyncio

    from app.services.discharge_action import (
        EncounterNotDischargeableError,
        discharge_encounter,
    )

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
    order_id = ""

    try:
        async with maker() as setup:
            ids = await _fixture(setup)
            order_id = await _order(setup, ids)
            # Far future so the queued timer cannot become visible to the
            # worker while this test runs.
            await _contract(setup, ids, order_id, days=29)
            await setup.commit()

        async def attempt() -> str:
            async with maker() as s:
                try:
                    await discharge_encounter(s, uuid.UUID(ids["enc"]))
                except EncounterNotDischargeableError:
                    return "refused"
                except Exception as exc:  # surfaced, not swallowed
                    return f"error:{type(exc).__name__}"
                return "discharged"

        results = await asyncio.gather(attempt(), attempt())
        assert sorted(results) == ["discharged", "refused"], results

        async with maker() as check:
            cases = (
                await check.execute(
                    text("SELECT count(*) FROM pending_cases WHERE encounter_id = :e"),
                    {"e": ids["enc"]},
                )
            ).scalar()
            events = (
                await check.execute(
                    text(
                        "SELECT count(*) FROM case_events ce JOIN pending_cases pc "
                        "ON pc.id = ce.case_id WHERE pc.encounter_id = :e"
                    ),
                    {"e": ids["enc"]},
                )
            ).scalar()
            timers = (
                await check.execute(
                    text(
                        "SELECT count(*) FROM pgmq.q_sla_timers "
                        "WHERE message->>'encounter_id' = :e"
                    ),
                    {"e": ids["enc"]},
                )
            ).scalar()

        assert cases == 1, f"expected exactly one pending case, got {cases}"
        assert events == 1, f"expected exactly one case event, got {events}"
        assert timers == 1, f"expected exactly one queued timer, got {timers}"

    finally:
        async with maker() as cleanup:
            if ids:
                # case_events is append-only and the trigger correctly refuses
                # a DELETE -- which is the whole point of it. Bypass it for
                # this cleanup only. session_replication_role is scoped to this
                # connection and resets when it closes, so unlike
                # ALTER TABLE ... DISABLE TRIGGER a crash here cannot leave the
                # guard switched off for everyone.
                await cleanup.execute(text("SET session_replication_role = replica"))
                await cleanup.execute(
                    text(
                        "DELETE FROM pgmq.q_sla_timers "
                        "WHERE message->>'encounter_id' = :e"
                    ),
                    {"e": ids["enc"]},
                )
                await cleanup.execute(
                    text(
                        "DELETE FROM case_events WHERE case_id IN "
                        "(SELECT id FROM pending_cases WHERE encounter_id = :e)"
                    ),
                    {"e": ids["enc"]},
                )
                await cleanup.execute(
                    text("DELETE FROM pending_cases WHERE encounter_id = :e"),
                    {"e": ids["enc"]},
                )
                await cleanup.execute(
                    text("DELETE FROM discharge_contracts WHERE encounter_id = :e"),
                    {"e": ids["enc"]},
                )
                await cleanup.execute(
                    text("DELETE FROM orders WHERE encounter_id = :e"),
                    {"e": ids["enc"]},
                )
                await cleanup.execute(
                    text("DELETE FROM encounters WHERE id = :i"), {"i": ids["enc"]}
                )
                await cleanup.execute(
                    text("DELETE FROM patients WHERE id = :i"), {"i": ids["pat"]}
                )
                await cleanup.execute(
                    text("DELETE FROM users WHERE id = :i"), {"i": ids["user"]}
                )
                await cleanup.execute(
                    text("DELETE FROM departments WHERE id = :i"), {"i": ids["dept"]}
                )
                await cleanup.execute(text("SET session_replication_role = origin"))
            await cleanup.commit()
        await engine.dispose()
