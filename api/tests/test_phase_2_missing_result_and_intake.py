"""Phase 2.3 and 2.4 — the missing-result path and manual intake.

2.3's premise is worth restating, because it is the part teams get wrong:

    The lab must be flagged too, not just the doctor. **A missing result is a
    lab-side failure.**

So the tests below care that a fired ``result_due`` blames the lab as well as
chasing the doctor, that the case does **not** move out of
``awaiting_result`` (the investigation is still outstanding), and that the
24-hour re-check stops at seven days by escalating rather than looping
forever.

2.4's endpoint is the permanent intake path — Phases 6-7 add automatic callers
for this same function — so it is tested as a production endpoint: replay,
late arrival, an order with no case, and validation.
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
from app.db.models.lab import LAB_RECHECK_MAX
from app.services.lab_flags import (
    LabFlagNotFoundError,
    next_recheck_at,
    raise_lab_flag,
    resolve_lab_flag,
)
from app.services.timers import claim_timer_for_firing, create_timer
from tests._phase5 import authenticate_as
from worker.consumers.sla_timers import handle_sla_timer

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


async def _case(session: AsyncSession, *, with_case: bool = True) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:10]
    ids = {
        k: str(uuid.uuid4())
        for k in ("dept", "user", "head", "pat", "enc", "order", "contract", "case")
    }
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role) VALUES "
            "(:u, :uc, 'Dr Owner', 'doctor'), (:h, :hc, 'Dr Head', 'unit_head')"
        ),
        {"u": ids["user"], "uc": f"E{tag}", "h": ids["head"], "hc": f"H{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO departments (id, code, name, unit_head_user_id) "
            "VALUES (:i, :c, 'D', :h)"
        ),
        {"i": ids["dept"], "c": f"D{tag}", "h": ids["head"]},
    )
    await session.execute(
        text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, 'P')"),
        {"i": ids["pat"], "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            "status, department_id) "
            "VALUES (:i, :p, :n, 'ipd', now(), 'discharged', :d)"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"ENC{tag}", "d": ids["dept"]},
    )
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            "category, ordered_at, status) "
            "VALUES (:i, :e, :p, 'URC', 'Urine Culture', 'micro', now(), 'in_lab')"
        ),
        {"i": ids["order"], "e": ids["enc"], "p": ids["pat"]},
    )
    if with_case:
        await session.execute(
            text(
                "INSERT INTO discharge_contracts "
                "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
                "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
            ),
            {
                "i": ids["contract"],
                "e": ids["enc"],
                "o": ids["order"],
                "u": ids["user"],
            },
        )
        await session.execute(
            text(
                "INSERT INTO pending_cases "
                "(id, order_id, encounter_id, patient_id, contract_id, "
                " current_owner_id) VALUES (:i, :o, :e, :p, :c, :u)"
            ),
            {
                "i": ids["case"],
                "o": ids["order"],
                "e": ids["enc"],
                "p": ids["pat"],
                "c": ids["contract"],
                "u": ids["user"],
            },
        )
    await session.commit()
    return ids


def _ago(hours: float) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)


async def _fire_result_due(session: AsyncSession, ids: dict[str, Any]) -> uuid.UUID:
    """Create an overdue result_due and put it through the real handler."""
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(1))
    await handle_sla_timer(session, {"timer_id": str(created.timer_id)})
    return created.timer_id


async def _events(session: AsyncSession, case_id: str) -> list[str]:
    return list(
        (
            await session.execute(
                text(
                    "SELECT event_type FROM case_events WHERE case_id = :c "
                    " ORDER BY occurred_at"
                ),
                {"c": case_id},
            )
        )
        .scalars()
        .all()
    )


# ── 2.3: what a fired result_due does ─────────────────────────────────


async def test_a_fired_result_due_flags_the_lab(session: AsyncSession) -> None:
    ids = await _case(session)
    await _fire_result_due(session, ids)

    flag = (
        await session.execute(
            text("SELECT flag_type, resolved_at FROM lab_flags WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).one()
    assert flag.flag_type == "report_delayed"
    assert flag.resolved_at is None


async def test_the_case_stays_awaiting_result(session: AsyncSession) -> None:
    """The plan is explicit that the state *stays* awaiting_result. Moving it
    to some "overdue" state would make the case vanish from the one query
    that matters."""
    ids = await _case(session)
    await _fire_result_due(session, ids)

    state = (
        await session.execute(
            text("SELECT state FROM pending_cases WHERE id = :i"), {"i": ids["case"]}
        )
    ).scalar_one()
    assert state == "awaiting_result"


async def test_both_the_lab_and_the_doctor_are_notified(
    session: AsyncSession,
) -> None:
    """ "Notify: lab department queue **+** responsible doctor." Both."""
    ids = await _case(session)
    await _fire_result_due(session, ids)

    audiences = set(
        (
            await session.execute(
                text(
                    "SELECT message->>'audience' FROM pgmq.q_notifications "
                    " WHERE message->>'case_id' = :c"
                ),
                {"c": ids["case"]},
            )
        )
        .scalars()
        .all()
    )
    assert audiences == {"lab_department", "responsible_doctor"}


async def test_a_recheck_is_scheduled_24h_out(session: AsyncSession) -> None:
    ids = await _case(session)
    await _fire_result_due(session, ids)

    row = (
        await session.execute(
            text(
                "SELECT fire_at FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'owner_reminder'"
            ),
            {"c": ids["case"]},
        )
    ).one()
    hours = (row.fire_at - dt.datetime.now(dt.UTC)).total_seconds() / 3600
    assert 23 < hours < 25, hours


async def test_firing_the_same_timer_three_times_raises_one_flag(
    session: AsyncSession,
) -> None:
    """Phase 2.5: "Timer fires **once** even if the queue delivers the message
    3 times." The clinical effect is the flag, so that is what is counted."""
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(1))

    for _ in range(3):
        await handle_sla_timer(session, {"timer_id": str(created.timer_id)})

    flags = (
        await session.execute(
            text("SELECT count(*) FROM lab_flags WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert flags == 1

    fired_events = [
        e for e in await _events(session, ids["case"]) if e == "result_due_fired"
    ]
    assert len(fired_events) == 1


async def test_a_cancelled_timer_in_flight_does_nothing(
    session: AsyncSession,
) -> None:
    """Phase 2.5: "Cancelled timer that is already in flight does nothing when
    consumed." """
    from app.services.timers import cancel_case_timers

    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(1))
    await cancel_case_timers(session, uuid.UUID(ids["case"]))

    await handle_sla_timer(session, {"timer_id": str(created.timer_id)})

    flags = (
        await session.execute(
            text("SELECT count(*) FROM lab_flags WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert flags == 0


async def test_a_timer_whose_result_already_arrived_raises_no_flag(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    await session.execute(
        text("UPDATE pending_cases SET state = 'result_received' WHERE id = :i"),
        {"i": ids["case"]},
    )
    await _fire_result_due(session, ids)

    flags = (
        await session.execute(
            text("SELECT count(*) FROM lab_flags WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert flags == 0
    assert "result_due_fired" in await _events(session, ids["case"])


async def test_a_message_without_a_timer_id_is_ignored(
    session: AsyncSession,
) -> None:
    """Phase 1.3 enqueued wake-ups before timer rows existed. Firing on a guess
    would be worse than dropping it -- the sweep re-enqueues from timer truth
    with the id included."""
    ids = await _case(session)
    await handle_sla_timer(
        session, {"case_id": ids["case"], "timer_type": "result_due"}
    )

    flags = (
        await session.execute(
            text("SELECT count(*) FROM lab_flags WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert flags == 0


# ── 2.3: the 24h re-check and the 7-day ceiling ───────────────────────


def test_recheck_returns_none_at_the_seven_day_ceiling() -> None:
    raised = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
    assert next_recheck_at(raised, raised + dt.timedelta(hours=1)) is not None
    assert next_recheck_at(raised, raised + LAB_RECHECK_MAX) is None
    assert next_recheck_at(raised, raised + dt.timedelta(days=8)) is None


def test_a_recheck_is_never_scheduled_past_the_ceiling() -> None:
    raised = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
    almost = raised + dt.timedelta(days=6, hours=12)
    when = next_recheck_at(raised, almost)
    assert when is not None
    assert when <= raised + LAB_RECHECK_MAX


async def test_the_recheck_escalates_to_the_unit_head_after_seven_days(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    await _fire_result_due(session, ids)

    # Age the flag past the ceiling, then run the re-check.
    await session.execute(
        text(
            "UPDATE lab_flags SET raised_at = now() - interval '8 days' "
            " WHERE case_id = :c"
        ),
        {"c": ids["case"]},
    )
    recheck = (
        await session.execute(
            text(
                "SELECT id FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'owner_reminder'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    await handle_sla_timer(session, {"timer_id": str(recheck)})

    escalation = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'unit_head_escalation'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert escalation == 1


async def test_the_unit_head_escalation_names_the_unit_head(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    created = await create_timer(
        session, uuid.UUID(ids["case"]), "unit_head_escalation", _ago(1)
    )
    await handle_sla_timer(session, {"timer_id": str(created.timer_id)})

    row = (
        await session.execute(
            text(
                "SELECT payload->>'unit_head_user_id' AS head FROM case_events "
                " WHERE case_id = :c AND event_type = 'escalated_to_unit_head'"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert row.head == ids["head"]


async def test_the_recheck_stops_once_the_flag_is_resolved(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    await _fire_result_due(session, ids)

    flag_id = (
        await session.execute(
            text("SELECT id FROM lab_flags WHERE case_id = :c"), {"c": ids["case"]}
        )
    ).scalar_one()
    await resolve_lab_flag(session, flag_id, uuid.UUID(ids["user"]), "found it")

    before = (
        await session.execute(
            text("SELECT count(*) FROM sla_timers WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).scalar_one()

    recheck = (
        await session.execute(
            text(
                "SELECT id FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'owner_reminder'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    await handle_sla_timer(session, {"timer_id": str(recheck)})

    after = (
        await session.execute(
            text("SELECT count(*) FROM sla_timers WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert after == before, "a resolved flag kept scheduling re-checks"


# ── 2.3: lab flags themselves ─────────────────────────────────────────


async def test_only_one_open_flag_of_a_type_per_case(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    first = await raise_lab_flag(session, uuid.UUID(ids["case"]))
    second = await raise_lab_flag(session, uuid.UUID(ids["case"]))
    assert first.created is True
    assert second.created is False
    assert second.flag_id == first.flag_id


async def test_a_recurrence_after_resolution_raises_again(
    session: AsyncSession,
) -> None:
    """A resolved problem that comes back is a second failure, not a duplicate
    of the first."""
    ids = await _case(session)
    first = await raise_lab_flag(session, uuid.UUID(ids["case"]))
    await resolve_lab_flag(session, first.flag_id, uuid.UUID(ids["user"]))
    second = await raise_lab_flag(session, uuid.UUID(ids["case"]))
    assert second.created is True
    assert second.flag_id != first.flag_id


async def test_resolving_twice_is_refused(session: AsyncSession) -> None:
    ids = await _case(session)
    flag = await raise_lab_flag(session, uuid.UUID(ids["case"]))
    await resolve_lab_flag(session, flag.flag_id, uuid.UUID(ids["user"]))
    with pytest.raises(LabFlagNotFoundError):
        await resolve_lab_flag(session, flag.flag_id, uuid.UUID(ids["user"]))


async def test_the_metric_buckets_open_flags_by_age(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _case(session)
    await raise_lab_flag(session, uuid.UUID(ids["case"]))
    await session.commit()

    body = (await client.get("/api/lab-flags/metrics")).json()
    assert body["open_total"] >= 1
    assert [b["bucket"] for b in body["by_age"]] == [
        "under_24h",
        "1_to_3_days",
        "3_to_7_days",
        "over_7_days",
    ]
    assert body["by_type"]["report_delayed"] >= 1


async def test_the_metric_exposes_no_patient_identifiers(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """It is read by people who have no business seeing who the patients are."""
    ids = await _case(session)
    await raise_lab_flag(session, uuid.UUID(ids["case"]))
    await session.commit()

    raw = (await client.get("/api/lab-flags/metrics")).text
    assert ids["pat"] not in raw
    assert "mrn" not in raw.lower()


async def test_the_lab_queue_lists_oldest_first(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _case(session)
    await raise_lab_flag(session, uuid.UUID(ids["case"]))
    await session.commit()

    rows = (await client.get("/api/lab-flags", params={"open_only": "true"})).json()
    raised = [r["raised_at"] for r in rows]
    assert raised == sorted(raised)


async def test_resolve_endpoint_closes_the_flag(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _case(session)
    flag = await raise_lab_flag(session, uuid.UUID(ids["case"]))
    await session.commit()

    response = await client.post(
        f"/api/lab-flags/{flag.flag_id}/resolve",
        json={"resolved_by": ids["user"], "resolution_note": "sample located"},
    )
    assert response.status_code == 200
    assert response.json()["resolved_at"] is not None

    again = await client.post(
        f"/api/lab-flags/{flag.flag_id}/resolve", json={"resolved_by": ids["user"]}
    )
    assert again.status_code == 404


async def test_an_unknown_flag_type_filter_is_refused(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/lab-flags", params={"flag_type": "made_up"})
    assert response.status_code == 422


# ── 2.4: manual result intake ─────────────────────────────────────────


async def test_intake_records_the_result_and_moves_the_case(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _case(session)
    await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(-48))
    await session.commit()

    response = await client.post(
        f"/api/orders/{ids['order']}/results",
        json={
            "report_status": "final",
            "source": "manual",
            "source_ref": f"ACC-{uuid.uuid4().hex[:8]}",
            "raw_payload": {"organism": "E. coli"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["case_state"] == "result_received"
    assert body["classification_enqueued"] is True
    assert len(body["superseded_timer_ids"]) == 1


async def test_intake_supersedes_the_result_due_timer(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The deadline the result answers must never fire afterwards."""
    ids = await _case(session)
    created = await create_timer(
        session, uuid.UUID(ids["case"]), "result_due", _ago(-48)
    )
    await session.commit()

    await client.post(
        f"/api/orders/{ids['order']}/results", json={"report_status": "final"}
    )

    status = (
        await session.execute(
            text("SELECT status FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).scalar_one()
    assert status == "superseded"
    assert await claim_timer_for_firing(session, created.timer_id) is None


async def test_a_preliminary_report_creates_a_stale_preliminary_timer(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """2.2's "create classification timers": a preliminary report still owes a
    final one, so a deadline genuinely remains."""
    ids = await _case(session)
    await session.commit()

    await client.post(
        f"/api/orders/{ids['order']}/results", json={"report_status": "preliminary"}
    )

    count = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'stale_preliminary'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert count == 1


async def test_a_final_report_creates_no_further_timer(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _case(session)
    await session.commit()

    await client.post(
        f"/api/orders/{ids['order']}/results", json={"report_status": "final"}
    )

    count = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'stale_preliminary'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert count == 0, "a final report was given a deadline nobody asked for"


async def test_replaying_the_same_report_is_refused(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _case(session)
    await session.commit()
    ref = f"ACC-{uuid.uuid4().hex[:8]}"
    body = {"report_status": "final", "source": "manual", "source_ref": ref}

    first = await client.post(f"/api/orders/{ids['order']}/results", json=body)
    assert first.status_code == 201

    second = await client.post(f"/api/orders/{ids['order']}/results", json=body)
    assert second.status_code == 409
    assert second.json()["existing_result_id"] == first.json()["result_id"]

    count = (
        await session.execute(
            text("SELECT count(*) FROM results WHERE order_id = :o"),
            {"o": ids["order"]},
        )
    ).scalar_one()
    assert count == 1


async def test_a_report_without_a_reference_can_be_entered_twice(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A clerk typing off a paper printout may genuinely have no accession
    number. Replay protection keys on source_ref, so its absence means no
    protection -- which is honest, and better than refusing the row."""
    ids = await _case(session)
    await session.commit()

    for _ in range(2):
        response = await client.post(
            f"/api/orders/{ids['order']}/results", json={"report_status": "final"}
        )
        assert response.status_code == 201


async def test_a_result_for_an_order_with_no_case_is_still_stored(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """A result can arrive for an investigation that never went through a
    discharge. Refusing it would lose it; Phase 7.5 routes these onward."""
    ids = await _case(session, with_case=False)
    await session.commit()

    response = await client.post(
        f"/api/orders/{ids['order']}/results", json={"report_status": "final"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["case_id"] is None
    assert body["classification_enqueued"] is False


async def test_a_late_result_is_recorded_without_reopening_the_case(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Reopening is a Phase 5.3 decision with its own reason codes. An intake
    endpoint that quietly did it would bypass that."""
    ids = await _case(session)
    await session.execute(
        text(
            "UPDATE pending_cases SET state = 'closed', closed_at = now() "
            " WHERE id = :i"
        ),
        {"i": ids["case"]},
    )
    await session.commit()

    response = await client.post(
        f"/api/orders/{ids['order']}/results", json={"report_status": "final"}
    )
    assert response.status_code == 201
    assert response.json()["late"] is True
    assert response.json()["case_state"] == "closed"


async def test_intake_records_an_event(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _case(session)
    await session.commit()
    await client.post(
        f"/api/orders/{ids['order']}/results", json={"report_status": "final"}
    )
    assert "result_received" in await _events(session, ids["case"])


async def test_intake_404s_for_an_unknown_order(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"/api/orders/{uuid.uuid4()}/results", json={"report_status": "final"}
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_intake_validates_its_payload(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ids = await _case(session)
    await session.commit()
    url = f"/api/orders/{ids['order']}/results"

    # Unknown report status, unknown source, naive timestamp, unknown field.
    for body in (
        {"report_status": "made_up"},
        {"report_status": "final", "source": "carrier_pigeon"},
        {"report_status": "final", "reported_at": "2026-03-14T10:00:00"},
        {"report_status": "final", "severity": "critical"},
        {},
    ):
        response = await client.post(url, json=body)
        assert response.status_code == 422, body


async def test_intake_does_not_interpret_the_payload(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """RULE 1: the tracking guarantee must not wait on interpretation. The
    payload is stored verbatim and no severity is assigned."""
    ids = await _case(session)
    await session.commit()

    payload = {"potassium": 7.9, "flag": "CRITICAL"}
    await client.post(
        f"/api/orders/{ids['order']}/results",
        json={"report_status": "final", "raw_payload": payload},
    )

    row = (
        await session.execute(
            text(
                "SELECT r.raw_payload, pc.severity FROM results r "
                "  JOIN pending_cases pc ON pc.id = r.case_id "
                " WHERE r.order_id = :o"
            ),
            {"o": ids["order"]},
        )
    ).one()
    assert row.raw_payload == payload
    assert row.severity is None, "Phase 2 classified a result"
