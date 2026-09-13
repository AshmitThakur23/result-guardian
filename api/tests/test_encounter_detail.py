"""Phase 1.5 — the encounter detail read, against a real PostgreSQL.

One payload, assembled in one transaction. The tests care that it tells the
truth about the gate without re-implementing it, and that it does not widen
what the product exposes about a patient.
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


async def _fixture(
    session: AsyncSession, enc_type: str = "ipd", status: str = "active"
) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:10]
    ids = {k: str(uuid.uuid4()) for k in ("user", "pat", "enc")}
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role) "
            "VALUES (:i, :e, :n, 'doctor')"
        ),
        {"i": ids["user"], "e": f"E{tag}", "n": f"Asha Menon {tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO patients (id, mrn, name, address_line, phone_primary_e164) "
            "VALUES (:i, :m, 'Detail Patient', '12 Secret Street', '+919999999999')"
        ),
        {"i": ids["pat"], "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, "
            "admitted_at, status, attending_doctor_id, ward, bed) "
            "VALUES (:i, :p, :n, :t, now(), :s, :u, 'Ward 3', 'B12')"
        ),
        {
            "i": ids["enc"],
            "p": ids["pat"],
            "n": f"ENC{tag}",
            "t": enc_type,
            "s": status,
            "u": ids["user"],
        },
    )
    await session.commit()
    ids["tag"] = tag
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
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, "
            "test_name, category, ordered_at, status, expected_tat_hours) "
            "VALUES (:i, :e, :p, 'T', :n, 'micro', now(), :s, 48)"
        ),
        {
            "i": order_id,
            "e": ids["enc"],
            "p": ids["pat"],
            "n": name,
            "s": status,
        },
    )
    await session.commit()
    return order_id


# ── what it shows ─────────────────────────────────────────────────────


async def test_shows_the_encounter_patient_and_doctor(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    body = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    assert body["patient"]["id"] == ids["pat"]
    assert body["attending_doctor"]["id"] == ids["user"]
    assert body["ward"] == "Ward 3"
    assert body["status"] == "active"


async def test_lists_orders_with_their_status(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    await _order(session, ids, status="in_lab", name="Urine Culture")
    await _order(session, ids, status="final", name="Chest X-Ray")

    body = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    by_name = {o["test_name"]: o for o in body["orders"]}
    assert by_name["Urine Culture"]["is_outstanding"] is True
    assert by_name["Chest X-Ray"]["is_outstanding"] is False


async def test_shows_the_contract_on_a_contracted_order(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The detail screen has to explain *why* an encounter is dischargeable,
    which means naming who owns each outstanding result."""
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    contract_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": contract_id, "e": ids["enc"], "o": order_id, "u": ids["user"]},
    )
    await session.commit()

    body = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    order = body["orders"][0]
    assert order["contract_id"] == contract_id
    assert order["responsible_doctor_id"] == ids["user"]
    assert order["responsible_doctor_name"] == f"Asha Menon {ids['tag']}"
    assert order["expected_by"] is not None


async def test_includes_discharge_medications(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    await client.post(
        f"/api/encounters/{ids['enc']}/discharge-medications",
        json={"drug_name": "Amoxicillin", "is_antibiotic": True},
    )
    body = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    assert [m["drug_name"] for m in body["medications"]] == ["Amoxicillin"]


async def test_reports_the_gate_without_reimplementing_it(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The detail screen's answer must be the same one the gate gives. A
    second copy of the blocking rule is a second thing that can drift."""
    ids = await _fixture(session)
    await _order(session, ids, status="in_lab")

    detail = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    readiness = (
        await client.get(f"/api/encounters/{ids['enc']}/discharge-readiness")
    ).json()

    assert detail["can_discharge"] == readiness["can_discharge"] is False
    assert detail["blocking_order_count"] == len(readiness["blocking_orders"]) == 1
    assert detail["gate_applies"] == readiness["gate_applies"] is True


async def test_gate_does_not_apply_to_opd(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """ADR 0003: OPD has no reliable closure event to hang the gate on."""
    ids = await _fixture(session, enc_type="opd")
    body = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    assert body["gate_applies"] is False


# ── empty, closed and missing ─────────────────────────────────────────


async def test_an_encounter_with_no_orders_is_not_an_error(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    body = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    assert body["orders"] == []
    assert body["medications"] == []
    assert body["can_discharge"] is True


async def test_a_discharged_encounter_stops_accepting_orders(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session, status="discharged")
    body = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    assert body["can_add_orders"] is False

    # And the flag agrees with what the server actually does.
    refused = await client.post(
        f"/api/encounters/{ids['enc']}/orders",
        json={"test_code": "X", "test_name": "Y", "category": "lab"},
    )
    assert refused.status_code == 409


async def test_an_active_encounter_accepts_orders(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    body = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    assert body["can_add_orders"] is True


async def test_404_for_an_unknown_encounter(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/encounters/{uuid.uuid4()}/detail")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_404_for_a_soft_deleted_encounter(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    await session.execute(
        text("UPDATE encounters SET deleted_at = now() WHERE id = :i"),
        {"i": ids["enc"]},
    )
    await session.commit()
    assert (await client.get(f"/api/encounters/{ids['enc']}/detail")).status_code == 404


async def test_soft_deleted_orders_are_hidden(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    await session.execute(
        text("UPDATE orders SET deleted_at = now() WHERE id = :i"), {"i": order_id}
    )
    await session.commit()
    body = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    assert body["orders"] == []


# ── what it must not expose ───────────────────────────────────────────


async def test_exposes_no_address_or_consent_state(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A discharge workflow has no business rendering a patient's address."""
    ids = await _fixture(session)
    body = (await client.get(f"/api/encounters/{ids['enc']}/detail")).json()
    assert set(body["patient"]) == {"id", "mrn", "name"}
    assert "12 Secret Street" not in str(body)
    assert "+919999999999" not in str(body)


async def test_the_detail_read_does_not_mutate(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    await _order(session, ids)
    before = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM orders), "
                "(SELECT count(*) FROM discharge_contracts), "
                "(SELECT count(*) FROM pending_cases)"
            )
        )
    ).first()
    for _ in range(3):
        await client.get(f"/api/encounters/{ids['enc']}/detail")
    after = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM orders), "
                "(SELECT count(*) FROM discharge_contracts), "
                "(SELECT count(*) FROM pending_cases)"
            )
        )
    ).first()
    assert before == after


async def test_the_gate_header_endpoint_is_unchanged(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Phase 1.4's gate reads GET /encounters/{id}. Phase 1.5 added a
    separate /detail rather than widening it, so the gate is untouched."""
    ids = await _fixture(session)
    body = (await client.get(f"/api/encounters/{ids['enc']}")).json()
    assert "orders" not in body
    assert "medications" not in body
    assert body["patient"]["id"] == ids["pat"]
