"""Phase 1.5 — discharge medication entry, against a real PostgreSQL.

Rule B (Phase 3) is the only consumer, and it compares a culture's
sensitivity grid against what the patient is actually taking. So the tests
care about two things: that the row lands with the flags Rule B reads, and
that nothing here quietly interprets a drug.
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


async def _encounter(session: AsyncSession, status: str = "active") -> str:
    tag = uuid.uuid4().hex[:10]
    patient_id = str(uuid.uuid4())
    encounter_id = str(uuid.uuid4())
    await session.execute(
        text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, 'P')"),
        {"i": patient_id, "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, "
            "admitted_at, status) VALUES (:i, :p, :n, 'ipd', now(), :s)"
        ),
        {"i": encounter_id, "p": patient_id, "n": f"ENC{tag}", "s": status},
    )
    await session.commit()
    return encounter_id


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "drug_name": "Amoxicillin",
        "atc_code": "J01CA04",
        "dose": "500 mg",
        "route": "oral",
        "frequency": "TDS",
        "duration_days": "5",
        "is_antibiotic": True,
    }
    body.update(overrides)
    return body


# ── the happy path ────────────────────────────────────────────────────


async def test_records_a_medication(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    encounter_id = await _encounter(session)
    response = await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications", json=_payload()
    )
    assert response.status_code == 201
    body = response.json()
    assert body["drug_name"] == "Amoxicillin"
    assert body["is_antibiotic"] is True
    assert body["encounter_id"] == encounter_id


async def test_stores_every_field_rule_b_will_read(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    encounter_id = await _encounter(session)
    await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications", json=_payload()
    )
    row = (
        await session.execute(
            text(
                "SELECT drug_name, atc_code, dose, route, frequency, "
                "duration_days, is_antibiotic FROM discharge_medications "
                "WHERE encounter_id = :e"
            ),
            {"e": encounter_id},
        )
    ).first()
    assert row is not None
    assert row.drug_name == "Amoxicillin"
    assert row.atc_code == "J01CA04"
    assert row.route == "oral"
    assert float(row.duration_days) == 5.0
    assert row.is_antibiotic is True


async def test_only_the_drug_name_is_required(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A clerk copying a handwritten summary often has the drug and nothing
    else. Refusing the row means Rule B gets nothing rather than something."""
    encounter_id = await _encounter(session)
    response = await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications",
        json={"drug_name": "Paracetamol"},
    )
    assert response.status_code == 201
    assert response.json()["is_antibiotic"] is False


async def test_lists_antibiotics_first(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Rule B only considers antibiotics, so they lead the list."""
    encounter_id = await _encounter(session)
    await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications",
        json=_payload(drug_name="Metformin", is_antibiotic=False, atc_code="A10BA02"),
    )
    await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications",
        json=_payload(drug_name="Azithromycin", is_antibiotic=True),
    )
    rows = (
        await client.get(f"/api/encounters/{encounter_id}/discharge-medications")
    ).json()
    assert [r["drug_name"] for r in rows] == ["Azithromycin", "Metformin"]


async def test_accepted_after_discharge(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Deliberate. A discharge summary is often typed up after the patient
    has left, and a medication row changes nothing the gate reads. Refusing
    it would starve Rule B for exactly the busiest encounters."""
    encounter_id = await _encounter(session, status="discharged")
    response = await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications", json=_payload()
    )
    assert response.status_code == 201


# ── validation ────────────────────────────────────────────────────────


async def test_unknown_encounter_is_404(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"/api/encounters/{uuid.uuid4()}/discharge-medications", json=_payload()
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_rejects_a_non_positive_duration(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Mirrors ck_discharge_medications_duration_positive, so the caller sees
    a field error rather than an IntegrityError."""
    encounter_id = await _encounter(session)
    for bad in ("0", "-3"):
        response = await client.post(
            f"/api/encounters/{encounter_id}/discharge-medications",
            json=_payload(duration_days=bad),
        )
        assert response.status_code == 422, bad


async def test_rejects_an_implausible_duration(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    encounter_id = await _encounter(session)
    response = await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications",
        json=_payload(duration_days="5000"),
    )
    assert response.status_code == 422


async def test_rejects_a_missing_or_blank_drug_name(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    encounter_id = await _encounter(session)
    for body in ({}, {"drug_name": ""}, {"drug_name": "   "}):
        response = await client.post(
            f"/api/encounters/{encounter_id}/discharge-medications", json=body
        )
        assert response.status_code == 422, body


async def test_rejects_unknown_fields(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """extra='forbid'. A typo'd field name must not be silently dropped --
    Rule B would then compare against a drug list missing what was meant."""
    encounter_id = await _encounter(session)
    response = await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications",
        json=_payload(antibiotic=True),
    )
    assert response.status_code == 422


async def test_the_database_check_still_holds(session: AsyncSession) -> None:
    """RULE 1: the safety property is database constraints, not application
    code. Prove the CHECK refuses a bad duration even bypassing the API."""
    from sqlalchemy.exc import IntegrityError

    encounter_id = await _encounter(session)
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO discharge_medications "
                "(id, encounter_id, drug_name, duration_days) "
                "VALUES (:i, :e, 'Bad', -1)"
            ),
            {"i": str(uuid.uuid4()), "e": encounter_id},
        )
    await session.rollback()


# ── duplicates ────────────────────────────────────────────────────────


async def test_refuses_an_exact_duplicate(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Same drug, dose and frequency twice is a double-entry, and storing it
    would double that drug's weight in Rule B."""
    encounter_id = await _encounter(session)
    first = await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications", json=_payload()
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications", json=_payload()
    )
    assert second.status_code == 409


async def test_allows_the_same_drug_at_a_different_dose(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A patient can legitimately go home on the same drug twice -- a loading
    dose and a maintenance course -- so UNIQUE on the table would be wrong."""
    encounter_id = await _encounter(session)
    await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications", json=_payload()
    )
    second = await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications",
        json=_payload(dose="250 mg"),
    )
    assert second.status_code == 201


async def test_the_same_drug_on_another_encounter_is_fine(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    one = await _encounter(session)
    two = await _encounter(session)
    assert (
        await client.post(
            f"/api/encounters/{one}/discharge-medications", json=_payload()
        )
    ).status_code == 201
    assert (
        await client.post(
            f"/api/encounters/{two}/discharge-medications", json=_payload()
        )
    ).status_code == 201


async def test_medications_do_not_affect_the_gate(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A medication is captured for Phase 3, not read by Phase 1's gate.
    Adding one must not change readiness in either direction."""
    encounter_id = await _encounter(session)
    before = (
        await client.get(f"/api/encounters/{encounter_id}/discharge-readiness")
    ).json()
    await client.post(
        f"/api/encounters/{encounter_id}/discharge-medications", json=_payload()
    )
    after = (
        await client.get(f"/api/encounters/{encounter_id}/discharge-readiness")
    ).json()
    assert before == after
