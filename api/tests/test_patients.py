"""Phase 1.5 — patient search, against a real PostgreSQL.

Trigram similarity cannot be tested without pg_trgm and the Phase 1.2 GIN
index, so none of this runs against a stub. The tests care most about the two
things a search endpoint gets wrong: ranking the obvious match below a fuzzy
one, and letting a wildcard dump the patient index.
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


async def _patients(session: AsyncSession) -> dict[str, Any]:
    """Three patients whose names and identifiers overlap on purpose."""
    tag = uuid.uuid4().hex[:8]
    # Digits only, and unique per run: the phone search strips non-digits from
    # both sides, so a hex tag here could reduce to fewer than the seven
    # digits the search requires and silently skip the phone branch.
    digits = f"{uuid.uuid4().int % 10**8:08d}"
    ids = {k: str(uuid.uuid4()) for k in ("a", "b", "c", "enc")}

    await session.execute(
        text(
            "INSERT INTO patients (id, mrn, name, phone_primary_e164) VALUES "
            "(:a, :amrn, :aname, :aphone), "
            "(:b, :bmrn, :bname, :bphone), "
            "(:c, :cmrn, :cname, NULL)"
        ),
        {
            "a": ids["a"],
            "amrn": f"MRN-{tag}-1",
            "aname": f"Sunita Rao {tag}",
            "aphone": f"+91555{digits}",
            "b": ids["b"],
            "bmrn": f"MRN-{tag}-2",
            "bname": f"Sunil Rao {tag}",
            "bphone": f"+91556{digits}",
            "c": ids["c"],
            "cmrn": f"OTHER-{tag}",
            "cname": f"Wholly Different {tag}",
        },
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, "
            "admitted_at, status) VALUES (:i, :p, :n, 'ipd', now(), 'active')"
        ),
        {"i": ids["enc"], "p": ids["a"], "n": f"ENC-{tag}"},
    )
    await session.commit()
    ids["tag"] = tag
    ids["digits"] = digits
    return ids


# ── the three search modes ────────────────────────────────────────────


async def test_finds_by_exact_mrn(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _patients(session)
    rows = (
        await client.get("/api/patients", params={"q": f"MRN-{ids['tag']}-1"})
    ).json()
    assert rows[0]["id"] == ids["a"]


async def test_exact_mrn_outranks_a_fuzzy_name_hit(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A clerk who typed a full MRN must not have to scroll past a name."""
    ids = await _patients(session)
    rows = (
        await client.get("/api/patients", params={"q": f"MRN-{ids['tag']}-2"})
    ).json()
    assert rows[0]["id"] == ids["b"]


async def test_finds_by_partial_name(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _patients(session)
    rows = (
        await client.get("/api/patients", params={"q": f"Sunita Rao {ids['tag']}"})
    ).json()
    assert ids["a"] in {r["id"] for r in rows}


async def test_finds_a_misspelt_name_via_trigram(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The point of the Phase 1.2 GIN trigram index: a clerk mistyping a name
    still finds the patient, rather than creating a duplicate record."""
    ids = await _patients(session)
    rows = (
        await client.get("/api/patients", params={"q": f"Sunta Rao {ids['tag']}"})
    ).json()
    assert ids["a"] in {r["id"] for r in rows}


async def test_finds_by_phone_ignoring_formatting(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Stored E.164, typed however it is written on the file."""
    ids = await _patients(session)
    # Typed the way it is written on the file, not the way it is stored.
    typed = f"555 {ids['digits'][:4]}-{ids['digits'][4:]}"
    rows = (await client.get("/api/patients", params={"q": typed})).json()
    assert ids["a"] in {r["id"] for r in rows}


async def test_reports_open_encounters(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _patients(session)
    rows = (
        await client.get("/api/patients", params={"q": f"MRN-{ids['tag']}-1"})
    ).json()
    assert rows[0]["active_encounter_count"] == 1


# ── what it must refuse ───────────────────────────────────────────────


async def test_a_wildcard_does_not_dump_the_patient_index(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """`%` is one character. Unescaped, it matches every patient in the
    hospital -- a patient-index dump through a search box."""
    await _patients(session)
    for probe in ("%", "_", "%%", "\\"):
        rows = (await client.get("/api/patients", params={"q": probe})).json()
        assert rows == [], f"{probe!r} returned {len(rows)} rows"


async def test_empty_query_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/patients", params={"q": ""})).status_code == 422


async def test_whitespace_query_returns_nothing(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await _patients(session)
    assert (await client.get("/api/patients", params={"q": "   "})).json() == []


async def test_limit_is_capped(client: httpx.AsyncClient) -> None:
    assert (
        await client.get("/api/patients", params={"q": "a", "limit": 500})
    ).status_code == 422
    assert (
        await client.get("/api/patients", params={"q": "a", "limit": 0})
    ).status_code == 422


async def test_no_match_is_an_empty_list_not_an_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/api/patients", params={"q": f"nobody-{uuid.uuid4().hex}"}
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_never_leaks_address_or_consent_state(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A search result list is the most widely read screen in the product."""
    ids = await _patients(session)
    rows = (
        await client.get("/api/patients", params={"q": f"MRN-{ids['tag']}-1"})
    ).json()
    allowed = {
        "id",
        "mrn",
        "name",
        "dob",
        "sex",
        "phone_primary_e164",
        "active_encounter_count",
    }
    assert set(rows[0]) == allowed, set(rows[0]) - allowed


async def test_soft_deleted_patients_are_hidden(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _patients(session)
    await session.execute(
        text("UPDATE patients SET deleted_at = now() WHERE id = :i"), {"i": ids["a"]}
    )
    await session.commit()
    rows = (
        await client.get("/api/patients", params={"q": f"MRN-{ids['tag']}-1"})
    ).json()
    assert ids["a"] not in {r["id"] for r in rows}


# ── patient detail ────────────────────────────────────────────────────


async def test_patient_detail_lists_encounters(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _patients(session)
    body = (await client.get(f"/api/patients/{ids['a']}")).json()
    assert body["patient"]["id"] == ids["a"]
    assert [e["id"] for e in body["encounters"]] == [ids["enc"]]


async def test_patient_detail_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/patients/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_search_does_not_mutate(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _patients(session)
    before = (await session.execute(text("SELECT count(*) FROM patients"))).scalar_one()
    for _ in range(3):
        await client.get("/api/patients", params={"q": ids["tag"]})
    after = (await session.execute(text("SELECT count(*) FROM patients"))).scalar_one()
    assert before == after
