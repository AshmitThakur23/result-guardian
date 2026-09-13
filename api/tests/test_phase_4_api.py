"""Phase 4's HTTP surface.

Every endpoint the plan names, tested through the real app: reassignment with
its mandatory reason, acknowledgement, the roster a unit head maintains,
absences, the delivery-receipt webhook, the patient-contact record, and the
three metrics.

The reassignment tests get the most attention because the endpoint carries the
phase's sharpest rule:

    **Reassignment does not reset the escalation clock**, otherwise it becomes
    a dodge.

So one test reads the timers back afterwards and asserts they are untouched.
An endpoint that quietly reset the clock would pass every other test here.
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
from app.notifications.adapters import NullAdapter
from app.services import notifications as notify
from app.services.escalation import start_ladder
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


@pytest_asyncio.fixture(autouse=True)
async def adapters() -> AsyncIterator[dict[str, NullAdapter]]:
    registry = notify.registry()
    saved = dict(registry._adapters)
    for channel in ("email", "sms", "whatsapp"):
        registry.register(NullAdapter(channel))
    try:
        yield {}
    finally:
        registry._adapters = saved


async def _world(
    session: AsyncSession, *, severity: str = "critical"
) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:8]
    ids = {
        k: str(uuid.uuid4())
        for k in (
            "dept",
            "doctor",
            "other",
            "head",
            "pat",
            "enc",
            "order",
            "ct",
            "case",
        )
    }
    for key, role, code in (
        ("doctor", "doctor", "DOC"),
        ("other", "doctor", "OTH"),
        ("head", "unit_head", "HED"),
    ):
        await session.execute(
            text(
                "INSERT INTO users (id, employee_code, full_name, role, is_active, "
                " email) VALUES (:i, :c, :n, :r, true, :e)"
            ),
            {
                "i": ids[key],
                "c": f"{code}{tag}",
                "n": f"Dr {code}",
                "r": role,
                "e": f"{code.lower()}{tag}@example.test",
            },
        )
    await session.execute(
        text(
            "INSERT INTO departments (id, code, name, unit_head_user_id, active) "
            "VALUES (:i, :c, 'Medicine', :h, true)"
        ),
        {"i": ids["dept"], "c": f"D{tag}", "h": ids["head"]},
    )
    await session.execute(
        text(
            "INSERT INTO patients (id, mrn, name, phone_primary_e164, "
            " phone_verified_at) VALUES (:i, :m, 'P', '+915550000099', now())"
        ),
        {"i": ids["pat"], "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            "discharged_at, status, department_id) "
            "VALUES (:i, :p, :n, 'ipd', now(), now(), 'discharged', :d)"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"E{tag}", "d": ids["dept"]},
    )
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            "category, ordered_at, status) "
            "VALUES (:i, :e, :p, 'URC', 'Urine Culture', 'micro', now(), 'in_lab')"
        ),
        {"i": ids["order"], "e": ids["enc"], "p": ids["pat"]},
    )
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": ids["ct"], "e": ids["enc"], "o": ids["order"], "u": ids["doctor"]},
    )
    await session.execute(
        text(
            "INSERT INTO pending_cases "
            "(id, order_id, encounter_id, patient_id, contract_id, current_owner_id, "
            " state, severity, flagged_at) "
            "VALUES (:i, :o, :e, :p, :c, :u, 'flagged', :sev, now())"
        ),
        {
            "i": ids["case"],
            "o": ids["order"],
            "e": ids["enc"],
            "p": ids["pat"],
            "c": ids["ct"],
            "u": ids["doctor"],
            "sev": severity,
        },
    )
    await session.commit()
    return ids


# ── reassignment ──────────────────────────────────────────────────────


async def test_reassign_moves_the_owner(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    response = await client.post(
        f"/api/cases/{ids['case']}/reassign",
        json={
            "new_owner_id": ids["other"],
            "reason": "Dr DOC is off shift; Dr OTH is covering tonight.",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["previous_owner_id"] == ids["doctor"]
    assert body["new_owner_id"] == ids["other"]
    assert body["escalation_clock_reset"] is False


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
async def test_reassign_refuses_a_blank_reason(
    session: AsyncSession, client: httpx.AsyncClient, reason: str
) -> None:
    """*"Manual reassign with **mandatory reason**."*"""
    ids = await _world(session)
    response = await client.post(
        f"/api/cases/{ids['case']}/reassign",
        json={"new_owner_id": ids["other"], "reason": reason},
    )
    assert response.status_code == 422


async def test_reassign_does_not_reset_the_escalation_clock(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The rule the endpoint exists to enforce. A case handed round three
    doctors escalates on the schedule set when it was flagged."""
    ids = await _world(session)
    await start_ladder(session, uuid.UUID(ids["case"]))

    before = (
        await session.execute(
            text(
                "SELECT id, fire_at, status FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'case_escalation' "
                " ORDER BY escalation_level"
            ),
            {"c": ids["case"]},
        )
    ).all()

    await client.post(
        f"/api/cases/{ids['case']}/reassign",
        json={"new_owner_id": ids["other"], "reason": "covering"},
    )

    after = (
        await session.execute(
            text(
                "SELECT id, fire_at, status FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'case_escalation' "
                " ORDER BY escalation_level"
            ),
            {"c": ids["case"]},
        )
    ).all()

    assert [(r.id, r.fire_at, r.status) for r in before] == [
        (r.id, r.fire_at, r.status) for r in after
    ], "reassignment moved the escalation clock"


async def test_reassign_to_an_unknown_user_is_422(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    response = await client.post(
        f"/api/cases/{ids['case']}/reassign",
        json={"new_owner_id": str(uuid.uuid4()), "reason": "x"},
    )
    assert response.status_code == 422


async def test_reassign_of_an_unknown_case_is_404(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    response = await client.post(
        f"/api/cases/{uuid.uuid4()}/reassign",
        json={"new_owner_id": ids["other"], "reason": "x"},
    )
    assert response.status_code == 404


# ── acknowledgement ───────────────────────────────────────────────────


async def test_acknowledge_stops_the_ladder(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    await start_ladder(session, uuid.UUID(ids["case"]))

    response = await client.post(
        f"/api/cases/{ids['case']}/acknowledge",
        json={"acknowledged_by": ids["doctor"], "note": "Seen, patient recalled."},
    )
    assert response.status_code == 200
    assert len(response.json()["cancelled_timer_ids"]) == 5

    rows = (
        await session.execute(
            text(
                "SELECT status FROM sla_timers WHERE case_id = :c "
                "   AND timer_type = 'case_escalation'"
            ),
            {"c": ids["case"]},
        )
    ).all()
    assert all(r.status == "cancelled" for r in rows)


async def test_acknowledging_twice_is_idempotent(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    await start_ladder(session, uuid.UUID(ids["case"]))
    payload = {"acknowledged_by": ids["doctor"]}

    first = await client.post(f"/api/cases/{ids['case']}/acknowledge", json=payload)
    second = await client.post(f"/api/cases/{ids['case']}/acknowledge", json=payload)

    assert first.json()["already_acknowledged"] is False
    assert second.json()["already_acknowledged"] is True


# ── owner resolution, read-only ───────────────────────────────────────


async def test_owner_resolution_is_read_only(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    before = (
        await session.execute(
            text("SELECT count(*) AS n FROM case_events WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).one()

    response = await client.get(f"/api/cases/{ids['case']}/owner-resolution")
    assert response.status_code == 200
    assert response.json()["step"] == "contract_responsible_doctor"
    assert response.json()["level"] == 1

    after = (
        await session.execute(
            text("SELECT count(*) AS n FROM case_events WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).one()
    assert after.n == before.n, "a read appended to the append-only log"


# ── the roster a unit head maintains ──────────────────────────────────


async def test_a_unit_head_can_add_and_read_a_shift(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    start = dt.datetime.now(dt.UTC)
    response = await client.post(
        "/api/duty-roster",
        json={
            "user_id": ids["doctor"],
            "department_id": ids["dept"],
            "shift_start": start.isoformat(),
            "shift_end": (start + dt.timedelta(hours=8)).isoformat(),
            "role_on_duty": "primary",
        },
    )
    assert response.status_code == 201
    assert response.json()["user_name"] == "Dr DOC"

    listed = await client.get(f"/api/departments/{ids['dept']}/duty-roster")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


async def test_a_shift_that_ends_before_it_starts_is_refused(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A backwards shift never matches ``now()``, so the roster would look
    populated while resolving nobody — ADR 0004's failure wearing a hat."""
    ids = await _world(session)
    start = dt.datetime.now(dt.UTC)
    response = await client.post(
        "/api/duty-roster",
        json={
            "user_id": ids["doctor"],
            "department_id": ids["dept"],
            "shift_start": start.isoformat(),
            "shift_end": (start - dt.timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 422


async def test_an_absence_can_be_recorded_with_a_delegate(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    response = await client.post(
        "/api/user-absences",
        json={
            "user_id": ids["doctor"],
            "absence_type": "leave",
            "starts_at": dt.datetime.now(dt.UTC).isoformat(),
            "delegate_user_id": ids["other"],
        },
    )
    assert response.status_code == 201
    assert response.json()["ends_at"] is None  # indefinite


async def test_a_user_cannot_be_their_own_delegate(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    response = await client.post(
        "/api/user-absences",
        json={
            "user_id": ids["doctor"],
            "absence_type": "leave",
            "starts_at": dt.datetime.now(dt.UTC).isoformat(),
            "delegate_user_id": ids["doctor"],
        },
    )
    assert response.status_code == 422


# ── ADR 0004's guards, as endpoints ───────────────────────────────────


async def test_the_stale_roster_endpoint_lists_uncovered_departments(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    response = await client.get("/api/duty-roster/stale")
    assert response.status_code == 200
    assert ids["dept"] in {d["department_id"] for d in response.json()}


async def test_the_fallthrough_metric_endpoint_answers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/metrics/owner-fallthrough?days=7")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


async def test_the_flag_rate_metric_carries_the_retune_threshold(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """*"If this exceeds ~15% you must retune thresholds… put this number on
    the wall."* The threshold travels with the number so a dashboard cannot
    render one without the other."""
    await _world(session)
    response = await client.get("/api/metrics/flag-rate?days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["retune_threshold_per_100"] == 15.0
    assert "flags_per_100_discharges" in body
    assert isinstance(body["exceeds_retune_threshold"], bool)


# ── 4.3 · the delivery receipt webhook ────────────────────────────────


async def test_a_delivery_receipt_marks_the_message_delivered(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """``sent`` means the gateway took it; ``delivered`` means the handset
    acknowledged it. Only the second is evidence the patient got anything."""
    ids = await _world(session)
    await session.execute(
        text(
            "INSERT INTO notifications "
            "(id, case_id, user_id, channel, template_key, locale, status, sent_at, "
            " attempts, provider_msg_id, dedupe_key) "
            "VALUES (:i, :c, :u, 'sms', 't', 'en', 'sent', now(), 1, :m, :k)"
        ),
        {
            "i": str(uuid.uuid4()),
            "c": ids["case"],
            "u": ids["doctor"],
            "m": "PROV-123",
            "k": f"dr-{uuid.uuid4()}",
        },
    )

    response = await client.post(
        "/api/notifications/delivery-receipt",
        json={"provider_msg_id": "PROV-123", "delivered": True},
    )
    assert response.status_code == 200
    assert response.json()["matched"] is True

    row = (
        await session.execute(
            text("SELECT status FROM notifications WHERE provider_msg_id = 'PROV-123'")
        )
    ).one()
    assert row.status == "delivered"


async def test_an_unknown_receipt_is_accepted_not_404(
    client: httpx.AsyncClient,
) -> None:
    """A provider retrying a receipt for a message we no longer hold should
    not be told to keep retrying."""
    response = await client.post(
        "/api/notifications/delivery-receipt",
        json={"provider_msg_id": "NOPE", "delivered": True},
    )
    assert response.status_code == 200
    assert response.json()["matched"] is False


async def test_failed_notifications_are_listed_for_the_admin(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """*"mark ``failed`` → alert admin dashboard."*"""
    ids = await _world(session)
    await session.execute(
        text(
            "INSERT INTO notifications "
            "(id, case_id, user_id, channel, template_key, locale, status, attempts, "
            " error, dedupe_key) "
            "VALUES (:i, :c, :u, 'sms', 't', 'en', 'failed', 3, 'provider 500', :k)"
        ),
        {
            "i": str(uuid.uuid4()),
            "c": ids["case"],
            "u": ids["doctor"],
            "k": f"fail-{uuid.uuid4()}",
        },
    )
    response = await client.get("/api/notifications/failed?days=7")
    assert response.status_code == 200
    assert any(n["error"] == "provider 500" for n in response.json())


# ── 4.6 · the inbound half ────────────────────────────────────────────


async def test_the_front_desk_can_record_that_the_patient_called_back(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _world(session)
    response = await client.post(
        f"/api/cases/{ids['case']}/patient-contacts",
        json={
            "patient_id": ids["pat"],
            "direction": "inbound",
            "note": "Patient called, OPD appointment booked for Tuesday.",
        },
    )
    assert response.status_code == 201
    assert response.json()["direction"] == "inbound"

    events = (
        await session.execute(
            text(
                "SELECT count(*) AS n FROM case_events "
                " WHERE case_id = :c AND event_type = 'patient_contact_recorded'"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert int(events.n) == 1


async def test_a_contact_for_the_wrong_patient_is_refused(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Recording a callback against somebody else's case would put one
    patient's response on another patient's record."""
    ids = await _world(session)
    response = await client.post(
        f"/api/cases/{ids['case']}/patient-contacts",
        json={"patient_id": str(uuid.uuid4()), "direction": "inbound"},
    )
    assert response.status_code == 422
