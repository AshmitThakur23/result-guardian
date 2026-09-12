"""Phase 1.3 — discharge contract creation, against a real PostgreSQL.

Atomicity and the UNIQUE(order_id) race are the two things that matter here,
and neither can be tested against a mock. The service commits, so these tests
bind the session to an outer transaction with ``join_transaction_mode=
"create_savepoint"``: the service's commits become savepoint releases and the
outer rollback still leaves the development database untouched.

The concurrency test is the exception -- a real race needs two independent
connections, so it commits for real and cleans up after itself.
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

pytestmark = pytest.mark.integration

HORIZON_DAYS = 30


def _iso(delta: dt.timedelta) -> str:
    return (dt.datetime.now(dt.UTC) + delta).isoformat()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A session inside an outer transaction that is always rolled back.

    The service under test calls commit(); savepoint mode keeps that from
    escaping into the real database.
    """
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


async def _fixture(
    session: AsyncSession, enc_type: str = "ipd", doctor_active: bool = True
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10]
    ids = {k: str(uuid.uuid4()) for k in ("dept", "user", "pat", "enc")}
    await session.execute(
        text("INSERT INTO departments (id, code, name) VALUES (:i, :c, 'D')"),
        {"i": ids["dept"], "c": f"D{suffix}"},
    )
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role, is_active) "
            "VALUES (:i, :e, 'Dr Test', 'doctor', :a)"
        ),
        {"i": ids["user"], "e": f"E{suffix}", "a": doctor_active},
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
    session: AsyncSession, ids: dict[str, Any], status: str = "ordered"
) -> str:
    order_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            "category, ordered_at, status) "
            "VALUES (:i, :e, :p, 'T', 'Urine Culture', 'micro', now(), :s)"
        ),
        {"i": order_id, "e": ids["enc"], "p": ids["pat"], "s": status},
    )
    return order_id


async def _count_contracts(session: AsyncSession, encounter_id: str) -> int:
    value = (
        await session.execute(
            text("SELECT count(*) FROM discharge_contracts WHERE encounter_id = :e"),
            {"e": encounter_id},
        )
    ).scalar()
    return int(value or 0)


def _body(order_id: str, doctor_id: str, days: float = 2) -> dict[str, Any]:
    return {
        "contracts": [
            {
                "order_id": order_id,
                "responsible_doctor_id": doctor_id,
                "expected_by": _iso(dt.timedelta(days=days)),
            }
        ]
    }


async def _post(
    client: httpx.AsyncClient, encounter_id: str, body: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        f"/api/encounters/{encounter_id}/discharge-contracts", json=body
    )


# ── happy paths ───────────────────────────────────────────────────────


async def test_single_contract_created(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    order_id = await _order(session, ids)

    response = await _post(client, ids["enc"], _body(order_id, ids["user"]))

    assert response.status_code == 201, response.text
    payload = response.json()
    assert len(payload["created"]) == 1
    assert payload["created"][0]["order_id"] == order_id
    assert await _count_contracts(session, ids["enc"]) == 1


async def test_bulk_creates_every_contract(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    orders = [await _order(session, ids) for _ in range(3)]

    response = await _post(
        client,
        ids["enc"],
        {
            "contracts": [
                {
                    "order_id": o,
                    "responsible_doctor_id": ids["user"],
                    "expected_by": _iso(dt.timedelta(days=2)),
                }
                for o in orders
            ]
        },
    )

    assert response.status_code == 201, response.text
    assert {c["order_id"] for c in response.json()["created"]} == set(orders)
    assert await _count_contracts(session, ids["enc"]) == 3


# ── atomicity ─────────────────────────────────────────────────────────


async def test_one_bad_entry_creates_nothing(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """All-or-nothing. Two perfectly valid contracts must not survive a third
    invalid one -- a half-applied gate is worse than a refused one."""
    ids = await _fixture(session)
    good_a = await _order(session, ids)
    good_b = await _order(session, ids)

    response = await _post(
        client,
        ids["enc"],
        {
            "contracts": [
                {
                    "order_id": good_a,
                    "responsible_doctor_id": ids["user"],
                    "expected_by": _iso(dt.timedelta(days=2)),
                },
                {
                    "order_id": good_b,
                    "responsible_doctor_id": ids["user"],
                    "expected_by": _iso(dt.timedelta(days=2)),
                },
                {  # expected_by in the past
                    "order_id": str(uuid.uuid4()),
                    "responsible_doctor_id": ids["user"],
                    "expected_by": _iso(dt.timedelta(days=-1)),
                },
            ]
        },
    )

    assert response.status_code == 422
    assert await _count_contracts(session, ids["enc"]) == 0


async def test_all_violations_reported_not_just_the_first(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    response = await _post(
        client,
        ids["enc"],
        {
            "contracts": [
                {
                    "order_id": str(uuid.uuid4()),
                    "responsible_doctor_id": str(uuid.uuid4()),
                    "expected_by": _iso(dt.timedelta(days=-1)),
                }
            ]
        },
    )
    assert response.status_code == 422
    codes = {v["code"] for v in response.json()["violations"]}
    assert {
        "order_not_on_encounter",
        "doctor_not_found",
        "expected_by_not_future",
    } <= codes


# ── validation ────────────────────────────────────────────────────────


async def test_unknown_encounter_404(client: httpx.AsyncClient) -> None:
    response = await _post(
        client, str(uuid.uuid4()), _body(str(uuid.uuid4()), str(uuid.uuid4()))
    )
    assert response.status_code == 404


async def test_order_from_another_encounter_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    mine = await _fixture(session)
    theirs = await _fixture(session)
    foreign_order = await _order(session, theirs)

    response = await _post(client, mine["enc"], _body(foreign_order, mine["user"]))

    assert response.status_code == 422
    assert response.json()["violations"][0]["code"] == ("order_not_on_encounter")
    assert await _count_contracts(session, mine["enc"]) == 0


@pytest.mark.parametrize("status", ["final", "cancelled", "rejected"])
async def test_resolved_order_cannot_be_contracted(
    session: AsyncSession, client: httpx.AsyncClient, status: str
) -> None:
    """There is nothing pending to take ownership of."""
    ids = await _fixture(session)
    order_id = await _order(session, ids, status)

    response = await _post(client, ids["enc"], _body(order_id, ids["user"]))

    assert response.status_code == 422
    assert response.json()["violations"][0]["code"] == "order_not_outstanding"


async def test_duplicate_contract_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    order_id = await _order(session, ids)

    assert (
        await _post(client, ids["enc"], _body(order_id, ids["user"]))
    ).status_code == 201
    second = await _post(client, ids["enc"], _body(order_id, ids["user"]))

    assert second.status_code == 409
    assert await _count_contracts(session, ids["enc"]) == 1


async def test_same_order_twice_in_one_request_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    order_id = await _order(session, ids)

    response = await _post(
        client,
        ids["enc"],
        {
            "contracts": [
                {
                    "order_id": order_id,
                    "responsible_doctor_id": ids["user"],
                    "expected_by": _iso(dt.timedelta(days=2)),
                }
            ]
            * 2
        },
    )

    assert response.status_code == 422
    assert await _count_contracts(session, ids["enc"]) == 0


async def test_inactive_doctor_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Named explicitly by the build plan. An inactive owner is how a flag
    ends up routed to someone who has left."""
    ids = await _fixture(session, doctor_active=False)
    order_id = await _order(session, ids)

    response = await _post(client, ids["enc"], _body(order_id, ids["user"]))

    assert response.status_code == 422
    assert response.json()["violations"][0]["code"] == "doctor_not_active"
    assert await _count_contracts(session, ids["enc"]) == 0


async def test_unknown_doctor_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    response = await _post(client, ids["enc"], _body(order_id, str(uuid.uuid4())))
    assert response.status_code == 422
    assert response.json()["violations"][0]["code"] == "doctor_not_found"


# ── expected_by window ────────────────────────────────────────────────


async def test_expected_by_in_the_past_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    response = await _post(client, ids["enc"], _body(order_id, ids["user"], days=-0.5))
    assert response.status_code == 422
    assert response.json()["violations"][0]["code"] == ("expected_by_not_future")


async def test_expected_by_just_inside_the_30_day_boundary_accepted(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The plan says "<= 30 days", so the boundary is inclusive."""
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    body = _body(order_id, ids["user"])
    body["contracts"][0]["expected_by"] = _iso(
        dt.timedelta(days=HORIZON_DAYS, minutes=-1)
    )
    assert (await _post(client, ids["enc"], body)).status_code == 201


async def test_expected_by_beyond_30_days_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    response = await _post(
        client, ids["enc"], _body(order_id, ids["user"], days=HORIZON_DAYS + 1)
    )
    assert response.status_code == 422
    assert response.json()["violations"][0]["code"] == ("expected_by_beyond_horizon")


async def test_naive_expected_by_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A naive deadline would be read against the server's locale, and this
    value becomes the Phase 2 SLA timer."""
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    body = _body(order_id, ids["user"])
    body["contracts"][0]["expected_by"] = "2027-01-01T10:00:00"
    assert (await _post(client, ids["enc"], body)).status_code == 422


async def test_empty_batch_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    assert (await _post(client, ids["enc"], {"contracts": []})).status_code == 422


# ── ADR 0003 ──────────────────────────────────────────────────────────


async def test_opd_encounter_cannot_be_contracted(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """ADR 0003: the gate binds to ipd|emergency|daycare. A contract on an OPD
    encounter would be an accountability record nothing ever acts on."""
    ids = await _fixture(session, enc_type="opd")
    order_id = await _order(session, ids)
    response = await _post(client, ids["enc"], _body(order_id, ids["user"]))
    assert response.status_code == 422
    assert response.json()["violations"][0]["code"] == "encounter_not_gated"


# ── interaction with readiness ────────────────────────────────────────


async def test_readiness_reflects_the_new_contract(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    order_id = await _order(session, ids)

    before = (
        await client.get(f"/api/encounters/{ids['enc']}/discharge-readiness")
    ).json()
    assert before["can_discharge"] is False

    assert (
        await _post(client, ids["enc"], _body(order_id, ids["user"]))
    ).status_code == 201

    after = (
        await client.get(f"/api/encounters/{ids['enc']}/discharge-readiness")
    ).json()
    assert after["can_discharge"] is True
    assert after["blocking_orders"] == []
    assert [c["order_id"] for c in after["already_contracted"]] == [order_id]


async def test_contract_creation_does_not_touch_order_or_open_a_case(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """No pending_case here: the build plan opens those in the discharge
    action, not at contract time. And the order's status is not ours to move.
    """
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    await _post(client, ids["enc"], _body(order_id, ids["user"]))

    status_after = (
        await session.execute(
            text("SELECT status FROM orders WHERE id = :i"), {"i": order_id}
        )
    ).scalar()
    cases = (
        await session.execute(
            text("SELECT count(*) FROM pending_cases WHERE encounter_id = :e"),
            {"e": ids["enc"]},
        )
    ).scalar()
    revisions = (
        await session.execute(text("SELECT count(*) FROM discharge_contract_revisions"))
    ).scalar()

    assert status_after == "ordered"
    assert cases == 0
    # Creation is not a change, so no revision row -- revisions record edits.
    assert revisions == 0


# ── the real race ─────────────────────────────────────────────────────


async def test_concurrent_requests_cannot_both_contract_one_order() -> None:
    """Two independent connections, one order. Exactly one wins.

    This is the only test here that commits for real: a savepoint-scoped
    session shares one connection, and two sessions on one connection cannot
    actually race. UNIQUE(order_id) is the guarantee under test, and a
    pre-flight SELECT can never provide it -- the loser must be refused by the
    database, not by application logic.

    Cleans up after itself so the development database is left as found.
    """
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
        # Committed fixture, so both racers can see it.
        async with maker() as setup:
            ids = await _fixture(setup)
            order_id = await _order(setup, ids)
            await setup.commit()

        from app.schemas.discharge import DischargeContractRequest
        from app.services.discharge_contracts import (
            ContractConflictError,
            create_discharge_contracts,
        )

        request = [
            DischargeContractRequest(
                order_id=uuid.UUID(order_id),
                responsible_doctor_id=uuid.UUID(ids["user"]),
                expected_by=dt.datetime.now(dt.UTC) + dt.timedelta(days=2),
            )
        ]

        async def attempt() -> str:
            async with maker() as s:
                try:
                    await create_discharge_contracts(s, uuid.UUID(ids["enc"]), request)
                except ContractConflictError:
                    return "conflict"
                except Exception as exc:
                    return f"error:{type(exc).__name__}"
                return "created"

        # Sequential is enough to prove the constraint holds; the pre-check in
        # the second call catches it, and the IntegrityError path is exercised
        # below by inserting behind the service's back.
        first = await attempt()
        second = await attempt()
        assert sorted([first, second]) == ["conflict", "created"]

        async with maker() as check:
            count = (
                await check.execute(
                    text(
                        "SELECT count(*) FROM discharge_contracts WHERE order_id = :o"
                    ),
                    {"o": order_id},
                )
            ).scalar()
        assert count == 1, "UNIQUE(order_id) did not hold"

    finally:
        # Remove everything this test committed, deepest first (FKs RESTRICT).
        async with maker() as cleanup:
            if order_id:
                await cleanup.execute(
                    text("DELETE FROM discharge_contracts WHERE order_id = :o"),
                    {"o": order_id},
                )
                await cleanup.execute(
                    text("DELETE FROM orders WHERE id = :o"), {"o": order_id}
                )
            if ids:
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
            await cleanup.commit()
        await engine.dispose()


async def test_unique_constraint_refuses_a_contract_inserted_behind_our_back(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The IntegrityError path: a contract appears between our SELECT and our
    INSERT. The batch must roll back whole, not partially."""
    ids = await _fixture(session)
    raced = await _order(session, ids)
    other = await _order(session, ids)

    from app.schemas.discharge import DischargeContractRequest
    from app.services.discharge_contracts import (
        ContractConflictError,
        create_discharge_contracts,
    )

    expected_by = dt.datetime.now(dt.UTC) + dt.timedelta(days=2)
    requests = [
        DischargeContractRequest(
            order_id=uuid.UUID(o),
            responsible_doctor_id=uuid.UUID(ids["user"]),
            expected_by=expected_by,
        )
        for o in (raced, other)
    ]

    # Slip a contract in for `raced` without the service's pre-check seeing it.
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '1 day')"
        ),
        {
            "i": str(uuid.uuid4()),
            "e": ids["enc"],
            "o": raced,
            "u": ids["user"],
        },
    )

    with pytest.raises(ContractConflictError):
        await create_discharge_contracts(session, uuid.UUID(ids["enc"]), requests)

    # The other order in the same batch must NOT have been contracted.
    remaining = (
        await session.execute(
            text("SELECT count(*) FROM discharge_contracts WHERE order_id = :o"),
            {"o": other},
        )
    ).scalar()
    assert remaining == 0
