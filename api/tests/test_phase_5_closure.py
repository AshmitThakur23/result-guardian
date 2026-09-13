"""Phase 5.3 — acknowledgement and closure.

    ``POST /api/cases/{id}/acknowledge`` — requires ``closure_reason`` from enum
    Transaction: set state ``closed``, stop timers, cancel escalations, write
    audit, write ``case_events``
    Reopen path: amended result or manual reopen with reason
    **Bulk acknowledge is not available for CRITICAL cases — deliberate
    friction**

The Phase 4 guarantee must survive: *"Acknowledging at **any** rung stops
everything."* THE ONE RULE says a later phase may not break an earlier one, so
that is asserted here as well as in Phase 4's own suite.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cases import CLINICAL_CLOSURE_REASONS, CLOSURE_REASONS
from app.services import closure
from tests._phase5 import bearer, build_world, extra_case

pytestmark = pytest.mark.integration

GOOD_NOTE = "Patient recalled, seen in OPD, antibiotic changed to nitrofurantoin"


# ── the vocabulary ─────────────────────────────────────────────────────


def test_the_five_clinical_reasons_are_exactly_the_plans_five() -> None:
    assert set(CLINICAL_CLOSURE_REASONS) == {
        "action_taken",
        "already_handled",
        "not_clinically_relevant",
        "duplicate_report",
        "patient_uncontactable",
    }


def test_the_auto_close_reason_is_not_a_clinician_choice() -> None:
    """Counting an auto-close as a clinician's judgement would flatter every
    metric in 5.6."""
    assert "auto_closed_normal" in CLOSURE_REASONS
    assert "auto_closed_normal" not in CLINICAL_CLOSURE_REASONS
    with pytest.raises(closure.ClosureValidationError):
        closure.validate_closure("auto_closed_normal", GOOD_NOTE)


@pytest.mark.parametrize("reason", CLINICAL_CLOSURE_REASONS)
def test_every_clinical_reason_requires_a_note(reason: str) -> None:
    """*"a closure reason with no explanation is the thing an auditor cannot
    do anything with."*"""
    with pytest.raises(closure.ClosureValidationError):
        closure.validate_closure(reason, None)
    with pytest.raises(closure.ClosureValidationError):
        closure.validate_closure(reason, "ok")
    with pytest.raises(closure.ClosureValidationError):
        closure.validate_closure(reason, "        ")


def test_each_reason_asks_for_what_the_plan_attaches_to_it() -> None:
    """A generic "a note is required" gets a generic note back."""
    prompts = {}
    for reason in CLINICAL_CLOSURE_REASONS:
        with pytest.raises(closure.ClosureValidationError) as caught:
            closure.validate_closure(reason, None)
        prompts[reason] = str(caught.value)

    assert "action taken" in prompts["action_taken"]
    assert "where and when" in prompts["already_handled"]
    assert "not clinically relevant" in prompts["not_clinically_relevant"]
    assert "original case" in prompts["duplicate_report"]
    assert "contact attempts" in prompts["patient_uncontactable"]


def test_a_duplicate_must_name_the_original() -> None:
    with pytest.raises(closure.ClosureValidationError) as caught:
        closure.validate_closure("duplicate_report", GOOD_NOTE)
    assert "link to the original case" in str(caught.value)

    cleaned = closure.validate_closure(
        "duplicate_report", GOOD_NOTE, duplicate_of_case_id=uuid.uuid4()
    )
    assert cleaned == GOOD_NOTE


def test_an_invented_reason_is_refused() -> None:
    with pytest.raises(closure.ClosureValidationError) as caught:
        closure.validate_closure("seemed_fine", GOOD_NOTE)
    assert "not a closure reason" in str(caught.value)


def test_the_database_refuses_an_unknown_reason_too() -> None:
    """The CHECK constraint is the backstop for anything bypassing the API."""
    # Asserted against the live schema in the integration test below; this
    # placeholder keeps the unit block readable.
    assert "auto_closed_normal" in CLOSURE_REASONS


@pytest.mark.asyncio
async def test_the_check_constraint_rejects_an_unknown_reason(
    session: AsyncSession,
) -> None:
    from sqlalchemy.exc import IntegrityError

    ids = await build_world(session)
    with pytest.raises(IntegrityError):
        await session.execute(
            text("UPDATE pending_cases SET closure_reason = 'made_up' WHERE id = :c"),
            {"c": ids["case"]},
        )
    await session.rollback()


# ── closing a case ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_closing_sets_the_state_and_records_the_reason(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")

    response = await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": GOOD_NOTE},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    row = (
        await session.execute(
            text(
                "SELECT state, closed_at, acknowledged_at, closure_reason, "
                "       closure_note, updated_by FROM pending_cases WHERE id = :c"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert row.state == "closed"
    assert row.closed_at is not None
    assert row.acknowledged_at is not None
    assert row.closure_reason == "action_taken"
    assert row.closure_note == GOOD_NOTE
    assert str(row.updated_by) == ids["doctor"]


@pytest.mark.asyncio
async def test_a_closure_reason_is_mandatory(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        f"/api/cases/{ids['case']}/close", json={}, headers=headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_missing_note_is_refused_at_the_api(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "not_clinically_relevant"},
        headers=headers,
    )
    assert response.status_code == 422

    still_open = (
        await session.execute(
            text("SELECT closed_at FROM pending_cases WHERE id = :c"),
            {"c": ids["case"]},
        )
    ).scalar()
    assert still_open is None, "a refused closure must not half-close the case"


@pytest.mark.asyncio
async def test_the_closer_is_the_authenticated_user_not_a_body_field(
    client: Any, session: AsyncSession
) -> None:
    """Nobody may sign a closure in a colleague's name."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        f"/api/cases/{ids['case']}/close",
        json={
            "closure_reason": "action_taken",
            "closure_note": GOOD_NOTE,
            "acknowledged_by": ids["head"],
        },
        headers=headers,
    )
    # `extra="forbid"` -- the field does not exist and cannot be smuggled in.
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_closing_is_idempotent_and_keeps_the_first_reason(
    client: Any, session: AsyncSession
) -> None:
    """A double-click must not be an error, and must not overwrite."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")

    first = await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": GOOD_NOTE},
        headers=headers,
    )
    second = await client.post(
        f"/api/cases/{ids['case']}/close",
        json={
            "closure_reason": "not_clinically_relevant",
            "closure_note": "Actually I think this one is fine",
        },
        headers=headers,
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["already_closed"] is False
    assert second.json()["already_closed"] is True

    reason = (
        await session.execute(
            text("SELECT closure_reason FROM pending_cases WHERE id = :c"),
            {"c": ids["case"]},
        )
    ).scalar()
    assert reason == "action_taken", "the second closer must not rewrite the first"


@pytest.mark.asyncio
async def test_closing_stops_every_timer(client: Any, session: AsyncSession) -> None:
    """*"Transaction: … stop timers, cancel escalations."*

    Every timer, not only the escalation rungs: the missing-result sweep and
    the lab re-check chain must stop too.
    """
    ids = await build_world(session)
    for timer_type, level in (
        ("case_escalation", 1),
        ("result_due", None),
        ("owner_reminder", None),
    ):
        await session.execute(
            text(
                "INSERT INTO sla_timers "
                "(id, case_id, timer_type, fire_at, status, idempotency_key, "
                " escalation_level) "
                "VALUES (:i, :c, :t, now() + interval '1 hour', 'pending', :k, :l)"
            ),
            {
                "i": str(uuid.uuid4()),
                "c": ids["case"],
                "t": timer_type,
                "k": f"{ids['case']}:{timer_type}:{uuid.uuid4().hex[:6]}",
                "l": level,
            },
        )
    await session.commit()

    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": GOOD_NOTE},
        headers=headers,
    )
    assert response.status_code == 200
    assert len(response.json()["cancelled_timer_ids"]) == 3

    pending = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND status = 'pending'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert pending == 0


@pytest.mark.asyncio
async def test_phase_4s_guarantee_still_holds_at_every_rung(
    client: Any, session: AsyncSession
) -> None:
    """THE ONE RULE: *"Acknowledging at **any** rung stops everything."*

    Phase 5.3 supersedes Phase 4's bare acknowledgement. It must not weaken
    what Phase 4 guaranteed.
    """
    ids = await build_world(session)
    for level in range(5):
        await session.execute(
            text(
                "INSERT INTO sla_timers "
                "(id, case_id, timer_type, fire_at, status, idempotency_key, "
                " escalation_level) "
                "VALUES (:i, :c, 'case_escalation', "
                "        now() + make_interval(hours => :h), 'pending', :k, :l)"
            ),
            {
                "i": str(uuid.uuid4()),
                "c": ids["case"],
                "h": level + 1,
                "k": (
                    f"{ids['case']}:case_escalation:rung{level}:"
                    f"{uuid.uuid4().hex[:6]}"
                ),
                "l": level,
            },
        )
    await session.commit()

    headers = await bearer(client, ids, "doctor")
    await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": GOOD_NOTE},
        headers=headers,
    )

    remaining = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND status = 'pending'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert remaining == 0, "not one rung may survive an acknowledgement"


@pytest.mark.asyncio
async def test_closing_writes_case_events_and_an_audit_row(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": GOOD_NOTE},
        headers=headers,
    )

    events = {
        r[0]
        for r in (
            await session.execute(
                text("SELECT event_type FROM case_events WHERE case_id = :c"),
                {"c": ids["case"]},
            )
        ).all()
    }
    assert {"case_acknowledged", "case_closed"} <= events

    audit_row = (
        await session.execute(
            text(
                "SELECT action, actor_user_id, before, after FROM audit_log "
                " WHERE entity_id = :c AND action = 'case.closed' "
                " ORDER BY seq DESC LIMIT 1"
            ),
            {"c": ids["case"]},
        )
    ).first()
    assert audit_row is not None
    assert str(audit_row.actor_user_id) == ids["doctor"]
    assert audit_row.before["state"] == "flagged"
    assert audit_row.after["closure_reason"] == "action_taken"
    assert audit_row.after["closure_note"] == GOOD_NOTE


@pytest.mark.asyncio
async def test_a_failed_closure_leaves_no_audit_row(
    client: Any, session: AsyncSession
) -> None:
    """*"never fire-and-forget"* — the audit row and the change share a
    transaction, so a refused closure leaves no trace claiming otherwise."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")

    await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": "no"},
        headers=headers,
    )

    rows = (
        await session.execute(
            text(
                "SELECT count(*) FROM audit_log "
                " WHERE entity_id = :c AND action = 'case.closed'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert rows == 0


# ── bulk closure and the CRITICAL refusal ──────────────────────────────


@pytest.mark.asyncio
async def test_bulk_close_works_for_non_critical_cases(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session, severity="follow_up")
    second = await extra_case(session, ids, severity="follow_up", hours_old=2)

    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        "/api/cases/bulk-close",
        json={
            "case_ids": [ids["case"], second],
            "closure_reason": "not_clinically_relevant",
            "closure_note": "Both within expected range for this patient",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["closed"]) == 2

    closed = (
        await session.execute(
            text(
                "SELECT count(*) FROM pending_cases "
                " WHERE id = ANY(CAST(:ids AS uuid[])) AND state = 'closed'"
            ),
            {"ids": [ids["case"], second]},
        )
    ).scalar_one()
    assert closed == 2


@pytest.mark.asyncio
async def test_bulk_close_refuses_a_critical_case(
    client: Any, session: AsyncSession
) -> None:
    """*"Bulk acknowledge is not available for CRITICAL cases — deliberate
    friction."*"""
    ids = await build_world(session, severity="critical")

    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        "/api/cases/bulk-close",
        json={
            "case_ids": [ids["case"]],
            "closure_reason": "action_taken",
            "closure_note": GOOD_NOTE,
        },
        headers=headers,
    )
    assert response.status_code == 409
    body = response.json()
    assert ids["case"] in body["critical_case_ids"]


@pytest.mark.asyncio
async def test_one_critical_case_rejects_the_whole_batch(
    client: Any, session: AsyncSession
) -> None:
    """A half-succeeded batch invites the clinician to re-run it without
    reading what was skipped."""
    ids = await build_world(session, severity="follow_up")
    critical = await extra_case(session, ids, severity="critical", hours_old=2)
    harmless = await extra_case(session, ids, severity="follow_up", hours_old=3)

    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        "/api/cases/bulk-close",
        json={
            "case_ids": [ids["case"], critical, harmless],
            "closure_reason": "already_handled",
            "closure_note": "Reviewed on the ward round this morning",
        },
        headers=headers,
    )
    assert response.status_code == 409

    still_open = (
        await session.execute(
            text(
                "SELECT count(*) FROM pending_cases "
                " WHERE id = ANY(CAST(:ids AS uuid[])) AND closed_at IS NULL"
            ),
            {"ids": [ids["case"], critical, harmless]},
        )
    ).scalar_one()
    assert still_open == 3, "nothing may be closed when the batch is refused"


@pytest.mark.asyncio
async def test_a_critical_case_can_still_be_closed_one_at_a_time(
    client: Any, session: AsyncSession
) -> None:
    """The friction is on the batch, not on the case."""
    ids = await build_world(session, severity="critical")
    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": GOOD_NOTE},
        headers=headers,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_bulk_close_is_capped(client: Any, session: AsyncSession) -> None:
    """An unbounded list is a way to close a ward in one click."""
    ids = await build_world(session, severity="follow_up")
    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        "/api/cases/bulk-close",
        json={
            "case_ids": [str(uuid.uuid4()) for _ in range(101)],
            "closure_reason": "already_handled",
            "closure_note": "Reviewed on the ward round this morning",
        },
        headers=headers,
    )
    assert response.status_code == 422


# ── reopening ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reopening_restores_the_case_and_counts_the_reopen(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session, severity="follow_up")
    headers = await bearer(client, ids, "doctor")

    await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": GOOD_NOTE},
        headers=headers,
    )
    response = await client.post(
        f"/api/cases/{ids['case']}/reopen",
        json={"reason": "Amended report arrived showing resistance"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["reopened_count"] == 1

    row = (
        await session.execute(
            text(
                "SELECT state, closed_at, closure_reason, reopened_count "
                "  FROM pending_cases WHERE id = :c"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert row.state == "reopened"
    assert row.closed_at is None
    assert row.closure_reason is None
    assert row.reopened_count == 1


@pytest.mark.asyncio
async def test_reopening_preserves_the_original_closure_in_the_history(
    client: Any, session: AsyncSession
) -> None:
    """*"A reopen is a new chapter, not an erasure."*"""
    ids = await build_world(session, severity="follow_up")
    headers = await bearer(client, ids, "doctor")

    await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "not_clinically_relevant", "closure_note": GOOD_NOTE},
        headers=headers,
    )
    await client.post(
        f"/api/cases/{ids['case']}/reopen",
        json={"reason": "Amended report arrived showing resistance"},
        headers=headers,
    )

    closed_event = (
        await session.execute(
            text(
                "SELECT payload FROM case_events "
                " WHERE case_id = :c AND event_type = 'case_closed' "
                " ORDER BY occurred_at DESC LIMIT 1"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert closed_event["closure_reason"] == "not_clinically_relevant"
    assert closed_event["closure_note"] == GOOD_NOTE

    # And the audit chain still carries both, in order.
    actions = [
        r[0]
        for r in (
            await session.execute(
                text(
                    "SELECT action FROM audit_log WHERE entity_id = :c " " ORDER BY seq"
                ),
                {"c": ids["case"]},
            )
        ).all()
    ]
    assert actions == ["case.closed", "case.reopened"]


@pytest.mark.asyncio
async def test_reopening_requires_a_reason(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session, severity="follow_up")
    headers = await bearer(client, ids, "doctor")
    await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": GOOD_NOTE},
        headers=headers,
    )
    response = await client.post(
        f"/api/cases/{ids['case']}/reopen", json={"reason": "oops"}, headers=headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_an_open_case_cannot_be_reopened(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        f"/api/cases/{ids['case']}/reopen",
        json={"reason": "This case is already open, so this makes no sense"},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reopening_does_not_restart_the_escalation_ladder(
    client: Any, session: AsyncSession
) -> None:
    """A reopened case that immediately paged the on-call would punish the
    clinician who reopened it honestly."""
    ids = await build_world(session, severity="follow_up")
    headers = await bearer(client, ids, "doctor")

    await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": GOOD_NOTE},
        headers=headers,
    )
    await client.post(
        f"/api/cases/{ids['case']}/reopen",
        json={"reason": "Amended report arrived showing resistance"},
        headers=headers,
    )

    pending = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND status = 'pending'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert pending == 0


@pytest.mark.asyncio
async def test_a_reopened_case_returns_to_the_worklist(
    client: Any, session: AsyncSession
) -> None:
    """Which is the thing a human actually needs."""
    ids = await build_world(session, severity="follow_up")
    headers = await bearer(client, ids, "doctor")

    await client.post(
        f"/api/cases/{ids['case']}/close",
        json={"closure_reason": "action_taken", "closure_note": GOOD_NOTE},
        headers=headers,
    )
    gone = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    assert ids["case"] not in {r["case_id"] for r in gone}

    await client.post(
        f"/api/cases/{ids['case']}/reopen",
        json={"reason": "Amended report arrived showing resistance"},
        headers=headers,
    )
    back = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    row = next(r for r in back if r["case_id"] == ids["case"])
    assert row["reopened_count"] == 1


# ── notes ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adding_a_note_appends_to_the_timeline_and_changes_nothing_else(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")

    response = await client.post(
        f"/api/cases/{ids['case']}/notes",
        json={"note": "Rang the patient, no answer. Will try again this evening."},
        headers=headers,
    )
    assert response.status_code == 201

    state = (
        await session.execute(
            text("SELECT state, closed_at FROM pending_cases WHERE id = :c"),
            {"c": ids["case"]},
        )
    ).one()
    assert state.state == "flagged"
    assert state.closed_at is None

    note_event = (
        await session.execute(
            text(
                "SELECT payload FROM case_events "
                " WHERE case_id = :c AND event_type = 'case_note_added'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert "Rang the patient" in note_event["note"]


@pytest.mark.asyncio
async def test_an_empty_note_is_refused(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        f"/api/cases/{ids['case']}/notes", json={"note": "   "}, headers=headers
    )
    assert response.status_code == 422
