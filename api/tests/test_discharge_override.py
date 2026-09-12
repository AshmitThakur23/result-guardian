"""Phase 1.3 — the discharge override, against a real PostgreSQL.

The thing worth testing here is not that the override works, but that it is
not a bypass: every investigation it lets through must still end up tracked,
flagged, and owned by a named human. A test suite that only checked the
override succeeded would miss the entire point of the feature.
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
from app.schemas.discharge import OVERRIDE_REASON_CODES

pytestmark = pytest.mark.integration

GOOD_REASON = "Patient left against medical advice before the culture resulted"


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
    from app.services.llm_probe import LlmProbe

    app = create_app()
    # create_app() does not run the lifespan under ASGITransport, so the probe
    # has to be attached by hand for /api/health to answer. It points at a
    # dead port: NODE B is irrelevant to this endpoint and must stay that way.
    app.state.llm_probe = LlmProbe(
        get_settings().model_copy(
            update={"llm_base_url": "http://127.0.0.1:59999", "llm_enabled": True}
        )
    )

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _fixture(
    session: AsyncSession, enc_type: str = "ipd", with_unit_head: bool = True
) -> dict[str, Any]:
    """Doctor, unit head, department, patient, encounter."""
    suffix = uuid.uuid4().hex[:10]
    ids = {k: str(uuid.uuid4()) for k in ("dept", "user", "head", "pat", "enc")}

    for key, role in (("user", "doctor"), ("head", "unit_head")):
        await session.execute(
            text(
                "INSERT INTO users (id, employee_code, full_name, role) "
                "VALUES (:i, :e, :n, :r)"
            ),
            {
                "i": ids[key],
                "e": f"{key}{suffix}",
                "n": f"Dr {key}",
                "r": role,
            },
        )

    await session.execute(
        text(
            "INSERT INTO departments (id, code, name, unit_head_user_id) "
            "VALUES (:i, :c, 'Medicine', :h)"
        ),
        {
            "i": ids["dept"],
            "c": f"D{suffix}",
            "h": ids["head"] if with_unit_head else None,
        },
    )
    await session.execute(
        text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, 'P')"),
        {"i": ids["pat"], "m": f"MRN{suffix}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, "
            "admitted_at, department_id) VALUES (:i, :p, :n, :t, now(), :d)"
        ),
        {
            "i": ids["enc"],
            "p": ids["pat"],
            "n": f"ENC{suffix}",
            "t": enc_type,
            "d": ids["dept"],
        },
    )
    return ids


async def _order(
    session: AsyncSession, ids: dict[str, Any], name: str = "Urine Culture"
) -> str:
    order_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, "
            "test_name, category, ordered_at, status) "
            "VALUES (:i, :e, :p, 'T', :n, 'micro', now(), 'ordered')"
        ),
        {"i": order_id, "e": ids["enc"], "p": ids["pat"], "n": name},
    )
    return order_id


async def _contract(session: AsyncSession, ids: dict[str, Any], order_id: str) -> str:
    contract_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO discharge_contracts (id, encounter_id, order_id, "
            "responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": contract_id, "e": ids["enc"], "o": order_id, "u": ids["user"]},
    )
    return contract_id


def _body(ids: dict[str, Any], **kw: Any) -> dict[str, Any]:
    body = {
        "reason_code": "patient_lama",
        "reason_text": GOOD_REASON,
        "overridden_by": ids["user"],
    }
    body.update(kw)
    return body


async def _post(
    client: httpx.AsyncClient, encounter_id: str, body: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        f"/api/encounters/{encounter_id}/discharge-overrides", json=body
    )


# ── the point of the feature ──────────────────────────────────────────


async def test_override_still_tracks_the_investigation(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """An override is not a bypass. The investigation stays tracked, flagged
    and owned -- that is the whole safety claim."""
    ids = await _fixture(session)
    order_id = await _order(session, ids)

    response = await _post(client, ids["enc"], _body(ids))
    assert response.status_code == 201, response.text

    row = (
        await session.execute(
            text(
                "SELECT state, current_owner_id, flagged_at, contract_id, order_id "
                "FROM pending_cases WHERE encounter_id = :e"
            ),
            {"e": ids["enc"]},
        )
    ).first()
    assert row is not None, "the override dropped the investigation entirely"
    assert row[0] == "flagged", "case must be flagged immediately, not merely opened"
    assert str(row[1]) == ids["head"], "case must be owned by the unit head"
    assert row[2] is not None, "flagged_at starts the Phase 4 escalation clock"
    assert row[3] is None, "an overridden order has no contract by definition"
    assert str(row[4]) == order_id


async def test_override_records_who_and_why(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    order_id = await _order(session, ids)
    await _post(client, ids["enc"], _body(ids, reason_code="deceased"))

    row = (
        await session.execute(
            text(
                "SELECT reason_code, reason_text, overridden_by, approved_by, "
                "order_id FROM discharge_overrides WHERE encounter_id = :e"
            ),
            {"e": ids["enc"]},
        )
    ).first()
    assert row is not None
    assert row[0] == "deceased"
    assert row[1] == GOOD_REASON
    assert str(row[2]) == ids["user"]
    assert row[3] is None  # no approval workflow is required
    assert str(row[4]) == order_id


async def test_override_writes_a_distinguishable_case_event(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Phase 5.4's overrides report has to tell a bypassed gate from a
    normal one."""
    ids = await _fixture(session)
    await _order(session, ids)
    await _post(client, ids["enc"], _body(ids))

    row = (
        await session.execute(
            text(
                "SELECT ce.event_type, ce.payload FROM case_events ce "
                "JOIN pending_cases pc ON pc.id = ce.case_id "
                "WHERE pc.encounter_id = :e"
            ),
            {"e": ids["enc"]},
        )
    ).first()
    assert row is not None
    assert row[0] == "case_opened_via_override"
    assert row[1]["reason_code"] == "patient_lama"
    assert row[1]["reason_text"] == GOOD_REASON
    assert row[1]["flagged_to_unit_head"] == ids["head"]


async def test_encounter_becomes_discharged(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    await _order(session, ids)
    await _post(client, ids["enc"], _body(ids))

    row = (
        await session.execute(
            text("SELECT status, discharged_at FROM encounters WHERE id = :i"),
            {"i": ids["enc"]},
        )
    ).first()
    assert row is not None
    assert row[0] == "discharged"
    assert row[1] is not None


async def test_one_override_row_per_uncontracted_order(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """discharge_overrides.order_id is NOT NULL, so the schema says per-order."""
    ids = await _fixture(session)
    orders = {await _order(session, ids, name=f"T{i}") for i in range(3)}

    response = await _post(client, ids["enc"], _body(ids))
    assert response.status_code == 201
    assert {o["order_id"] for o in response.json()["overridden"]} == orders

    counts = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM discharge_overrides WHERE "
                "encounter_id = :e), (SELECT count(*) FROM pending_cases WHERE "
                "encounter_id = :e AND state = 'flagged')"
            ),
            {"e": ids["enc"]},
        )
    ).first()
    assert counts == (3, 3)


async def test_contracted_orders_keep_their_owner_and_timer(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """An override covers only what was bypassed. An investigation somebody
    already accepted must not lose its deadline."""
    ids = await _fixture(session)
    owned = await _order(session, ids, name="Owned")
    await _contract(session, ids, owned)
    bypassed = await _order(session, ids, name="Bypassed")

    response = await _post(client, ids["enc"], _body(ids))
    assert response.status_code == 201

    body = response.json()
    assert [o["order_id"] for o in body["overridden"]] == [bypassed]
    assert [c["order_id"] for c in body["opened_cases"]] == [owned]
    assert body["opened_cases"][0]["current_owner_id"] == ids["user"]
    assert body["opened_cases"][0]["timer_msg_id"] > 0

    states = dict(
        (
            await session.execute(
                text(
                    "SELECT order_id::text, state FROM pending_cases "
                    "WHERE encounter_id = :e"
                ),
                {"e": ids["enc"]},
            )
        ).all()
    )
    assert states[bypassed] == "flagged"
    assert states[owned] == "awaiting_result"


# ── validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", OVERRIDE_REASON_CODES)
async def test_every_permitted_reason_code_accepted(
    session: AsyncSession, client: httpx.AsyncClient, code: str
) -> None:
    ids = await _fixture(session)
    await _order(session, ids)
    response = await _post(client, ids["enc"], _body(ids, reason_code=code))
    assert response.status_code == 201, response.text
    assert response.json()["reason_code"] == code


async def test_invalid_reason_code_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    await _order(session, ids)
    response = await _post(client, ids["enc"], _body(ids, reason_code="because"))
    assert response.status_code == 422


async def test_reason_text_exactly_twenty_characters_accepted(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The plan says ">= 20 chars", so the boundary is inclusive."""
    ids = await _fixture(session)
    await _order(session, ids)
    text_20 = "X" * 20
    response = await _post(client, ids["enc"], _body(ids, reason_text=text_20))
    assert response.status_code == 201, response.text


async def test_reason_text_nineteen_characters_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    await _order(session, ids)
    response = await _post(client, ids["enc"], _body(ids, reason_text="X" * 19))
    assert response.status_code == 422


async def test_whitespace_padded_reason_rejected(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Twenty spaces is not an audit trail. Mirrors the database CHECK, which
    trims before counting."""
    ids = await _fixture(session)
    await _order(session, ids)
    response = await _post(client, ids["enc"], _body(ids, reason_text="  short   " * 2))
    assert response.status_code == 422


async def test_database_check_still_enforces_the_rules_directly(
    session: AsyncSession,
) -> None:
    """RULE 1: the safety property is database constraints, not app code."""
    ids = await _fixture(session)
    order_id = await _order(session, ids)

    for column, value, pattern in (
        ("reason_code", "not_a_code", "reason_code"),
        ("reason_text", "too short", "reason_text_min_length"),
    ):
        params = {
            "i": str(uuid.uuid4()),
            "e": ids["enc"],
            "o": order_id,
            "rc": "patient_lama",
            "rt": GOOD_REASON,
            "by": ids["user"],
        }
        params["rc" if column == "reason_code" else "rt"] = value
        with pytest.raises(Exception, match=pattern):
            await session.execute(
                text(
                    "INSERT INTO discharge_overrides (id, encounter_id, "
                    "order_id, reason_code, reason_text, overridden_by) "
                    "VALUES (:i, :e, :o, :rc, :rt, :by)"
                ),
                params,
            )
        await session.rollback()


# ── refusal ───────────────────────────────────────────────────────────


async def test_unknown_encounter_404(client: httpx.AsyncClient) -> None:
    response = await _post(
        client,
        str(uuid.uuid4()),
        {
            "reason_code": "deceased",
            "reason_text": GOOD_REASON,
            "overridden_by": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404


async def test_opd_has_no_gate_to_override(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session, enc_type="opd")
    await _order(session, ids)
    assert (await _post(client, ids["enc"], _body(ids))).status_code == 409


async def test_department_without_unit_head_is_refused(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Refused loudly rather than creating a flagged case nobody owns -- the
    exact silent failure this product exists to prevent."""
    ids = await _fixture(session, with_unit_head=False)
    await _order(session, ids)

    response = await _post(client, ids["enc"], _body(ids))
    assert response.status_code == 409
    assert "unit head" in response.json()["title"].lower()

    state = (
        await session.execute(
            text("SELECT status FROM encounters WHERE id = :i"), {"i": ids["enc"]}
        )
    ).scalar()
    assert state == "active", "a refused override must not discharge"


async def test_nothing_blocking_is_refused(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Keeps the Phase 5.4 overrides report meaningful: every row is a real
    bypass, not a doctor clicking the red button out of habit."""
    ids = await _fixture(session)
    owned = await _order(session, ids)
    await _contract(session, ids, owned)

    response = await _post(client, ids["enc"], _body(ids))
    assert response.status_code == 409
    assert "not blocking" in response.json()["title"].lower()


async def test_repeat_override_is_a_conflict(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Same idempotency rule as the discharge action: no second override, no
    second case, no second event."""
    ids = await _fixture(session)
    await _order(session, ids)

    assert (await _post(client, ids["enc"], _body(ids))).status_code == 201
    second = await _post(client, ids["enc"], _body(ids))
    assert second.status_code == 409

    counts = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM discharge_overrides WHERE "
                "encounter_id = :e), (SELECT count(*) FROM pending_cases WHERE "
                "encounter_id = :e)"
            ),
            {"e": ids["enc"]},
        )
    ).first()
    assert counts == (1, 1)


async def test_client_cannot_assert_readiness(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _fixture(session)
    await _order(session, ids)
    response = await _post(
        client, ids["enc"], _body(ids, can_discharge=True, force=True)
    )
    assert response.status_code == 422  # extra="forbid"


# ── rollback ──────────────────────────────────────────────────────────


async def test_failure_rolls_back_the_whole_override(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A half-recorded override is worse than none: a discharged encounter
    with no audit row and no flagged case is invisible."""
    import app.services.discharge_override as override_mod

    ids = await _fixture(session)
    owned = await _order(session, ids, name="Owned")
    await _contract(session, ids, owned)
    await _order(session, ids, name="Bypassed")
    await session.commit()  # keep the fixture across the rollback

    from app.schemas.discharge import DischargeOverrideRequest

    original = override_mod.SLA_TIMER_QUEUE
    override_mod.SLA_TIMER_QUEUE = "queue_that_does_not_exist"
    try:
        with pytest.raises(Exception, match=r"(?i)does not exist|undefined"):
            await override_mod.override_discharge(
                session,
                uuid.UUID(ids["enc"]),
                DischargeOverrideRequest(
                    reason_code="system_outage",
                    reason_text=GOOD_REASON,
                    overridden_by=uuid.UUID(ids["user"]),
                ),
            )
    finally:
        override_mod.SLA_TIMER_QUEUE = original

    await session.rollback()

    row = (
        await session.execute(
            text(
                "SELECT (SELECT status FROM encounters WHERE id = :e), "
                "(SELECT count(*) FROM discharge_overrides WHERE encounter_id = :e), "
                "(SELECT count(*) FROM pending_cases WHERE encounter_id = :e)"
            ),
            {"e": ids["enc"]},
        )
    ).first()
    assert row == ("active", 0, 0)


async def test_append_only_protection_still_active(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The override must not have weakened the case_events guard."""
    ids = await _fixture(session)
    await _order(session, ids)
    await _post(client, ids["enc"], _body(ids))

    event_id = (
        await session.execute(
            text(
                "SELECT ce.id FROM case_events ce JOIN pending_cases pc "
                "ON pc.id = ce.case_id WHERE pc.encounter_id = :e"
            ),
            {"e": ids["enc"]},
        )
    ).scalar()

    with pytest.raises(Exception, match=r"(?i)append-only"):
        await session.execute(
            text("UPDATE case_events SET event_type = 'x' WHERE id = :i"),
            {"i": event_id},
        )
    await session.rollback()


async def test_node_b_is_irrelevant(client: httpx.AsyncClient) -> None:
    """RULE 2. The gate and its override never touch NODE B."""
    health = (await client.get("/api/health")).json()
    assert health["llm"]["reachable"] is False
    assert health["status"] == "ok"
