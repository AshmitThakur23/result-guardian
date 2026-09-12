"""Phase 1.3 — discharge readiness, against a real PostgreSQL.

The gate is the product. These tests exercise it through the actual HTTP
endpoint against real data rather than mocking the database, because the rule
being tested *is* a database query.

Every row is created inside a transaction and rolled back, so the development
database is left exactly as found.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models.orders import ORDER_STATUSES, ORDER_STATUSES_NOT_BLOCKING

pytestmark = pytest.mark.integration

BLOCKING_STATUSES = tuple(
    s for s in ORDER_STATUSES if s not in ORDER_STATUSES_NOT_BLOCKING
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            ready = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name = 'discharge_contracts'"
                    )
                )
            ).scalar()
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    if not ready:
        await engine.dispose()
        pytest.skip("Phase 1.1 schema not applied — skipped")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as s:
            yield s
            await s.rollback()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """The real app, sharing the test's transaction so rows stay uncommitted."""
    from app.db.session import get_session
    from app.main import create_app

    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _encounter(session: AsyncSession, enc_type: str = "ipd") -> dict[str, Any]:
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
    status: str,
    name: str = "Urine Culture",
    tat: float | None = 48,
) -> str:
    order_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            "category, ordered_at, status, expected_tat_hours) "
            "VALUES (:i, :e, :p, 'T', :n, 'micro', now(), :s, :tat)"
        ),
        {
            "i": order_id,
            "e": ids["enc"],
            "p": ids["pat"],
            "n": name,
            "s": status,
            "tat": tat,
        },
    )
    return order_id


async def _contract(session: AsyncSession, ids: dict[str, Any], order_id: str) -> str:
    contract_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": contract_id, "e": ids["enc"], "o": order_id, "u": ids["user"]},
    )
    return contract_id


async def _readiness(client: httpx.AsyncClient, encounter_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/encounters/{encounter_id}/discharge-readiness")
    assert response.status_code == 200, response.text
    return dict(response.json())


# ── the core rule ─────────────────────────────────────────────────────


async def test_no_orders_can_discharge(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _encounter(session)
    body = await _readiness(client, ids["enc"])
    assert body["can_discharge"] is True
    assert body["blocking_orders"] == []
    assert body["already_contracted"] == []


@pytest.mark.parametrize("status", ORDER_STATUSES_NOT_BLOCKING)
async def test_terminal_statuses_never_block(
    session: AsyncSession, client: httpx.AsyncClient, status: str
) -> None:
    """final, cancelled and rejected are resolved -- nothing left to own."""
    ids = await _encounter(session)
    await _order(session, ids, status)
    body = await _readiness(client, ids["enc"])
    assert body["can_discharge"] is True
    assert body["blocking_orders"] == []


@pytest.mark.parametrize("status", BLOCKING_STATUSES)
async def test_every_non_terminal_status_blocks(
    session: AsyncSession, client: httpx.AsyncClient, status: str
) -> None:
    """Covers the NOT-IN rule across every status the schema defines, so a new
    status added later defaults to blocking rather than silently passing."""
    ids = await _encounter(session)
    await _order(session, ids, status)
    body = await _readiness(client, ids["enc"])
    assert body["can_discharge"] is False
    assert len(body["blocking_orders"]) == 1
    assert body["blocking_orders"][0]["status"] == status


async def test_all_terminal_statuses_together(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _encounter(session)
    for status in ORDER_STATUSES_NOT_BLOCKING:
        await _order(session, ids, status)
    body = await _readiness(client, ids["enc"])
    assert body["can_discharge"] is True


async def test_mixed_only_the_blocking_order_is_listed(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _encounter(session)
    for status in ORDER_STATUSES_NOT_BLOCKING:
        await _order(session, ids, status, name=f"Resolved {status}")
    blocking_id = await _order(session, ids, "in_lab", name="Blood Culture")

    body = await _readiness(client, ids["enc"])
    assert body["can_discharge"] is False
    assert len(body["blocking_orders"]) == 1
    assert body["blocking_orders"][0]["order_id"] == blocking_id
    assert body["blocking_orders"][0]["test_name"] == "Blood Culture"


async def test_multiple_blocking_orders_all_appear(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _encounter(session)
    expected = {
        await _order(session, ids, "ordered", name="A"),
        await _order(session, ids, "collected", name="B"),
        await _order(session, ids, "preliminary", name="C"),
    }
    body = await _readiness(client, ids["enc"])
    assert body["can_discharge"] is False
    assert {o["order_id"] for o in body["blocking_orders"]} == expected


# ── contracts ─────────────────────────────────────────────────────────


async def test_contracted_order_moves_out_of_blocking(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Exit Gate 1: blocked -> assign owners and dates -> discharge succeeds.

    If a contracted order kept blocking, the gate could never be passed and
    the contract mechanism would be pointless.
    """
    ids = await _encounter(session)
    order_id = await _order(session, ids, "ordered")

    before = await _readiness(client, ids["enc"])
    assert before["can_discharge"] is False
    assert before["already_contracted"] == []

    contract_id = await _contract(session, ids, order_id)

    after = await _readiness(client, ids["enc"])
    assert after["can_discharge"] is True
    assert after["blocking_orders"] == []
    assert len(after["already_contracted"]) == 1
    assert after["already_contracted"][0]["order_id"] == order_id
    assert after["already_contracted"][0]["contract_id"] == contract_id


async def test_partially_contracted_still_blocks(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _encounter(session)
    contracted = await _order(session, ids, "ordered", name="Owned")
    await _contract(session, ids, contracted)
    uncontracted = await _order(session, ids, "ordered", name="Orphan")

    body = await _readiness(client, ids["enc"])
    assert body["can_discharge"] is False
    assert [o["order_id"] for o in body["blocking_orders"]] == [uncontracted]
    assert [o["order_id"] for o in body["already_contracted"]] == [contracted]


async def test_no_contract_reported_as_empty(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _encounter(session)
    await _order(session, ids, "ordered")
    body = await _readiness(client, ids["enc"])
    assert body["already_contracted"] == []


# ── suggested expected_by ─────────────────────────────────────────────


async def test_suggested_expected_by_is_ordered_at_plus_tat(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _encounter(session)
    await _order(session, ids, "ordered", tat=48)
    body = await _readiness(client, ids["enc"])
    row = body["blocking_orders"][0]

    ordered_at = dt.datetime.fromisoformat(row["ordered_at"])
    suggested = dt.datetime.fromisoformat(row["suggested_expected_by"])
    assert suggested - ordered_at == dt.timedelta(hours=48)


async def test_no_tat_gives_no_suggestion_rather_than_a_guess(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Better that the doctor picks a date than that we invent a deadline."""
    ids = await _encounter(session)
    await _order(session, ids, "ordered", tat=None)
    body = await _readiness(client, ids["enc"])
    assert body["blocking_orders"][0]["suggested_expected_by"] is None


# ── isolation and errors ──────────────────────────────────────────────


async def test_other_encounters_orders_are_ignored(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A blocking order on someone else's encounter must not gate this one."""
    mine = await _encounter(session)
    theirs = await _encounter(session)
    await _order(session, theirs, "ordered", name="Not mine")

    body = await _readiness(client, mine["enc"])
    assert body["can_discharge"] is True
    assert body["blocking_orders"] == []


async def test_soft_deleted_order_does_not_block(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _encounter(session)
    order_id = await _order(session, ids, "ordered")
    await session.execute(
        text("UPDATE orders SET deleted_at = now() WHERE id = :i"), {"i": order_id}
    )
    body = await _readiness(client, ids["enc"])
    assert body["can_discharge"] is True


async def test_unknown_encounter_returns_404_problem_json(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(f"/api/encounters/{uuid.uuid4()}/discharge-readiness")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 404


async def test_malformed_encounter_id_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/encounters/not-a-uuid/discharge-readiness")
    assert response.status_code == 422


# ── ADR 0003: the gate binds to ipd | emergency | daycare ─────────────


@pytest.mark.parametrize("enc_type", ["ipd", "emergency", "daycare"])
async def test_gate_applies_to_inpatient_types(
    session: AsyncSession, client: httpx.AsyncClient, enc_type: str
) -> None:
    ids = await _encounter(session, enc_type)
    await _order(session, ids, "ordered")
    body = await _readiness(client, ids["enc"])
    assert body["gate_applies"] is True
    assert body["can_discharge"] is False


async def test_opd_is_not_gated_but_orders_are_still_reported(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """ADR 0003: OPD is out of scope for v1 -- no reliable visit-closure event
    to bind the gate to. The outstanding orders are still surfaced rather than
    hidden, because the data is true whether or not the gate binds."""
    ids = await _encounter(session, "opd")
    await _order(session, ids, "ordered")
    body = await _readiness(client, ids["enc"])
    assert body["gate_applies"] is False
    assert body["can_discharge"] is True
    assert len(body["blocking_orders"]) == 1


# ── the GET must not touch anything ───────────────────────────────────


async def test_get_has_no_side_effects(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """No contract created, no pending case opened, no order mutated."""
    ids = await _encounter(session)
    order_id = await _order(session, ids, "ordered")

    async def counts() -> tuple[int, int, str]:
        contracts = (
            await session.execute(
                text(
                    "SELECT count(*) FROM discharge_contracts WHERE encounter_id = :e"
                ),
                {"e": ids["enc"]},
            )
        ).scalar()
        cases = (
            await session.execute(
                text("SELECT count(*) FROM pending_cases WHERE encounter_id = :e"),
                {"e": ids["enc"]},
            )
        ).scalar()
        status = (
            await session.execute(
                text("SELECT status FROM orders WHERE id = :i"), {"i": order_id}
            )
        ).scalar()
        return int(contracts or 0), int(cases or 0), str(status)

    before = await counts()
    for _ in range(3):
        await _readiness(client, ids["enc"])
    after = await counts()

    assert before == after == (0, 0, "ordered")

    # The encounter itself is untouched too -- no discharged_at, still active.
    row = (
        await session.execute(
            text("SELECT status, discharged_at FROM encounters WHERE id = :i"),
            {"i": ids["enc"]},
        )
    ).first()
    assert row is not None
    assert row[0] == "active"
    assert row[1] is None


async def test_readiness_is_recomputed_not_cached(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Safety-critical: the answer must follow the database, every call."""
    ids = await _encounter(session)
    order_id = await _order(session, ids, "ordered")
    assert (await _readiness(client, ids["enc"]))["can_discharge"] is False

    await session.execute(
        text("UPDATE orders SET status = 'final' WHERE id = :i"), {"i": order_id}
    )
    assert (await _readiness(client, ids["enc"]))["can_discharge"] is True

    await session.execute(
        text("UPDATE orders SET status = 'in_lab' WHERE id = :i"), {"i": order_id}
    )
    assert (await _readiness(client, ids["enc"]))["can_discharge"] is False
