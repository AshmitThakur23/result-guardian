"""Read-only directory endpoints, against a real PostgreSQL.

These exist only to unblock the Phase 1.4 gate screen: something to search for
a responsible doctor, and the attending doctor to default that search to. The
tests therefore care most about what these endpoints must *not* leak, and that
they stay read-only.
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


async def _people(session: AsyncSession) -> dict[str, Any]:
    s = uuid.uuid4().hex[:8]
    ids = {k: str(uuid.uuid4()) for k in ("doc", "inactive", "tech", "pat", "enc")}
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role, is_active) VALUES "
            "(:d, :dc, :dn, 'doctor', true), "
            "(:i, :ic, :in, 'doctor', false), "
            "(:t, :tc, :tn, 'lab_tech', true)"
        ),
        {
            "d": ids["doc"],
            "dc": f"D{s}",
            "dn": f"Asha Menon {s}",
            "i": ids["inactive"],
            "ic": f"I{s}",
            "in": f"Retired Doctor {s}",
            "t": ids["tech"],
            "tc": f"T{s}",
            "tn": f"Lab Person {s}",
        },
    )
    await session.execute(
        text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, 'Gate Patient')"),
        {"i": ids["pat"], "m": f"MRN{s}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            "attending_doctor_id, ward, bed) "
            "VALUES (:i, :p, :n, 'ipd', now(), :d, 'Ward 3', 'B12')"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"ENC{s}", "d": ids["doc"]},
    )
    ids["suffix"] = s
    return ids


# ── GET /api/users ────────────────────────────────────────────────────


async def test_search_by_name(session: AsyncSession, client: httpx.AsyncClient) -> None:
    ids = await _people(session)
    rows = (
        await client.get("/api/users", params={"q": f"Asha Menon {ids['suffix']}"})
    ).json()
    assert [r["id"] for r in rows] == [ids["doc"]]


async def test_search_by_employee_code(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _people(session)
    rows = (await client.get("/api/users", params={"q": f"D{ids['suffix']}"})).json()
    assert ids["doc"] in [r["id"] for r in rows]


async def test_role_filter(session: AsyncSession, client: httpx.AsyncClient) -> None:
    ids = await _people(session)
    rows = (
        await client.get("/api/users", params={"role": "doctor", "q": ids["suffix"]})
    ).json()
    returned = {r["id"] for r in rows}
    assert ids["doc"] in returned
    assert ids["tech"] not in returned


async def test_inactive_hidden_by_default(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The contract endpoint refuses an inactive doctor, so offering one in the
    picker would only produce a 422 the doctor cannot explain."""
    ids = await _people(session)
    rows = (await client.get("/api/users", params={"q": ids["suffix"]})).json()
    assert ids["inactive"] not in {r["id"] for r in rows}


async def test_inactive_visible_when_asked(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _people(session)
    rows = (
        await client.get(
            "/api/users", params={"q": ids["suffix"], "active_only": "false"}
        )
    ).json()
    assert ids["inactive"] in {r["id"] for r in rows}


async def test_never_leaks_credentials_or_contact_details(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A gate screen needs a name and an id. Nothing else."""
    ids = await _people(session)
    rows = (await client.get("/api/users", params={"q": ids["suffix"]})).json()
    assert rows
    allowed = {"id", "employee_code", "full_name", "role", "is_active", "department_id"}
    for row in rows:
        assert set(row) == allowed, f"unexpected fields: {set(row) - allowed}"


async def test_limit_is_capped(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/users", params={"limit": 1000})).status_code == 422
    assert (await client.get("/api/users", params={"limit": 0})).status_code == 422


async def test_no_availability_is_reported(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Availability needs duty_roster / user_absences, which are Phase 4.1.
    The endpoint must not imply it knows who is on duty."""
    ids = await _people(session)
    rows = (await client.get("/api/users", params={"q": ids["suffix"]})).json()
    for row in rows:
        assert "available" not in row
        assert "on_duty" not in row


# ── GET /api/encounters/{id} ──────────────────────────────────────────


async def test_encounter_detail(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _people(session)
    body = (await client.get(f"/api/encounters/{ids['enc']}")).json()

    assert body["id"] == ids["enc"]
    assert body["type"] == "ipd"
    assert body["status"] == "active"
    assert body["ward"] == "Ward 3"
    assert body["patient"]["id"] == ids["pat"]
    assert body["attending_doctor"]["id"] == ids["doc"]


async def test_encounter_detail_without_attending_doctor(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """attending_doctor_id is nullable; the gate must still render."""
    ids = await _people(session)
    await session.execute(
        text("UPDATE encounters SET attending_doctor_id = NULL WHERE id = :i"),
        {"i": ids["enc"]},
    )
    body = (await client.get(f"/api/encounters/{ids['enc']}")).json()
    assert body["attending_doctor"] is None


async def test_encounter_detail_exposes_no_extra_patient_data(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A discharge screen has no business rendering address, phone or consent."""
    ids = await _people(session)
    patient = (await client.get(f"/api/encounters/{ids['enc']}")).json()["patient"]
    assert set(patient) == {"id", "mrn", "name"}


async def test_encounter_detail_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/encounters/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_directory_reads_do_not_mutate(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _people(session)
    before = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM encounters)"
            )
        )
    ).first()

    for _ in range(3):
        await client.get("/api/users", params={"q": ids["suffix"]})
        await client.get(f"/api/encounters/{ids['enc']}")

    after = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM encounters)"
            )
        )
    ).first()
    assert before == after
