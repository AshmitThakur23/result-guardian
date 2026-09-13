"""The SLA timer fire handler. Phase 2.2 + 2.3.

One message on the ``sla_timers`` queue means "a timer may be due". It is a
*hint*, not an instruction: the message may be a duplicate, may be for a timer
that has since been cancelled, superseded or paused, and may arrive hours late
after a restart. Every one of those is normal, and the handler's job is to
produce exactly one effect regardless.

    pgmq guarantees at-least-once delivery, so a timer handler that fires
    twice must still produce exactly one flag.

That guarantee is bought with ``SELECT ... FOR UPDATE`` on the timer row, per
the build plan's own wording for 2.2. The lock is taken first, the status is
re-read inside it, and everything the firing does — the flag, the events, the
notifications, the next timer — happens while it is held. A second worker
blocks, then wakes to find ``status = 'fired'`` and does nothing.

The consumer runs the handler and the ``pgmq.delete`` in one transaction
(see ``worker/consumer.py``), so the work and the acknowledgement commit
together. A crash anywhere in here rolls back the work *and* leaves the
message to be redelivered — which is safe precisely because this is
idempotent.

**Nothing here touches NODE B.** A timer fires, a lab is flagged and a doctor
is queued for notification with the inference node switched off; RULE 1 means
the tracking guarantee never waits on interpretation.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.escalation import fire_rung
from app.services.lab_flags import (
    EVENT_ESCALATED_TO_UNIT_HEAD,
    EVENT_LAB_RECHECK_SCHEDULED,
    EVENT_RESULT_DUE_FIRED,
    FLAG_REPORT_DELAYED,
    enqueue_notification,
    next_recheck_at,
    raise_lab_flag,
    record_event,
)
from app.services.timers import claim_timer_for_firing, create_timer, mark_fired

log = structlog.get_logger(__name__)

# Case states in which a result is still genuinely outstanding. A timer that
# fires for a case which has already received its result has nothing to flag.
STATES_STILL_AWAITING = ("awaiting_result",)


async def handle_sla_timer(session: AsyncSession, message: dict[str, Any]) -> None:
    """Entry point for the ``sla_timers`` queue."""
    raw_timer_id = message.get("timer_id")
    if not raw_timer_id:
        # Phase 1.3 enqueued wake-ups without a timer_id before Phase 2.2
        # existed. Such a message cannot be tied to a row, and firing on a
        # guess would be worse than dropping it -- the sweep re-enqueues from
        # timer truth with the id included.
        log.warning("sla_timer_message_without_timer_id", message=message)
        return

    try:
        timer_id = uuid.UUID(str(raw_timer_id))
    except ValueError:
        log.warning("sla_timer_message_bad_timer_id", timer_id=raw_timer_id)
        return

    # ── lock ordering, before anything else ───────────────────────
    # The rest of this codebase locks **pending_cases before sla_timers**:
    # `close_case` and `record_result` both do. Phase 2's fire path never
    # locked the case at all, so no cycle existed.
    #
    # Phase 4.4's rung 0 assigns the case's owner, which UPDATEs
    # `pending_cases` while this handler holds the timer lock. Against
    # `acknowledge_case` -- which locks the case and then cancels the case's
    # timers -- that is the opposite order, and PostgreSQL detects the
    # deadlock and aborts one of them. Measured, not theorised:
    #
    #     DeadlockDetectedError: Process A waits for ShareLock on transaction
    #     ...; blocked by process B. Process B waits for ... blocked by A.
    #
    # So an escalation timer takes the case lock first, joining the order
    # everything else already uses. Only `case_escalation` does this: adding
    # the lock to Phase 2's types would serialise handlers that have no reason
    # to contend, and Phase 2's behaviour must not change.
    await _lock_case_for_escalation(session, timer_id)

    # ── the lock, and the decision, together ──────────────────────
    claim = await claim_timer_for_firing(session, timer_id)
    if claim is None:
        # Already fired, cancelled, superseded, paused or gone. A duplicate
        # delivery lands here, which is the whole point.
        log.info("sla_timer_not_eligible", timer_id=str(timer_id))
        return

    now = dt.datetime.now(dt.UTC)
    entry = log.bind(
        timer_id=str(timer_id), case_id=str(claim.case_id), timer_type=claim.timer_type
    )

    if claim.timer_type == "result_due":
        await _fire_result_due(session, claim.case_id, timer_id, now)
    elif claim.timer_type == "owner_reminder":
        await _fire_lab_recheck(session, claim.case_id, timer_id, now)
    elif claim.timer_type == "unit_head_escalation":
        await _fire_unit_head_escalation(session, claim.case_id, timer_id, now)
    elif claim.timer_type == "case_escalation":
        # Phase 4.4's acknowledgement ladder. A separate timer type from the
        # two above on purpose: those chase a *lab* for a result that has not
        # arrived, this chases a *clinician* for a result that has.
        await _fire_case_escalation(
            session, claim.case_id, timer_id, claim.escalation_level, now
        )
    else:
        # stale_preliminary and patient_notification have no Phase 2 behaviour
        # to run: the plan assigns their consequences to Phase 3 and Phase 4.
        # The timer still fires and is still recorded, so the history is
        # complete and a later phase has something to hang its handler on.
        await record_event(
            session,
            claim.case_id,
            f"{claim.timer_type}_fired",
            {"timer_id": str(timer_id), "handled": False},
            now=now,
        )

    await mark_fired(session, timer_id, now)
    entry.info("sla_timer_fired", attempts=claim.attempts)


async def _case_snapshot(session: AsyncSession, case_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text(
                "SELECT pc.id, pc.state, pc.current_owner_id, pc.order_id, "
                "       pc.encounter_id, o.test_name, e.department_id, "
                "       d.unit_head_user_id "
                "  FROM pending_cases pc "
                "  JOIN orders o ON o.id = pc.order_id "
                "  JOIN encounters e ON e.id = pc.encounter_id "
                "  LEFT JOIN departments d ON d.id = e.department_id "
                " WHERE pc.id = :c"
            ),
            {"c": str(case_id)},
        )
    ).first()


async def _fire_result_due(
    session: AsyncSession, case_id: uuid.UUID, timer_id: uuid.UUID, now: dt.datetime
) -> None:
    """Phase 2.3: *"result_due fires with no result → pending_cases.state stays
    awaiting_result, raise a lab-side flag."*

    Note what does **not** happen: the case state is not changed. The plan is
    explicit that it *stays* ``awaiting_result``, and that is correct -- the
    investigation is still outstanding, and moving it to some "overdue" state
    would make it disappear from the one query that matters.
    """
    case = await _case_snapshot(session, case_id)
    if case is None:
        log.warning("sla_timer_case_missing", case_id=str(case_id))
        return

    if case.state not in STATES_STILL_AWAITING:
        # The result arrived between the deadline and this message being
        # consumed. Supersession should have caught it; recording the fact
        # costs nothing and makes the history honest.
        await record_event(
            session,
            case_id,
            EVENT_RESULT_DUE_FIRED,
            {"timer_id": str(timer_id), "outcome": "result_already_received"},
            now=now,
        )
        return

    flag = await raise_lab_flag(session, case_id, FLAG_REPORT_DELAYED)

    await record_event(
        session,
        case_id,
        EVENT_RESULT_DUE_FIRED,
        {
            "timer_id": str(timer_id),
            "outcome": "lab_flag_raised",
            "flag_id": str(flag.flag_id),
            "flag_was_new": flag.created,
        },
        now=now,
    )

    # "Notify: lab department queue + responsible doctor." Both, as intents.
    await enqueue_notification(
        session,
        case_id,
        "lab_result_overdue",
        {
            "audience": "lab_department",
            "department_id": str(case.department_id) if case.department_id else None,
            "order_id": str(case.order_id),
            "test_name": case.test_name,
        },
    )
    await enqueue_notification(
        session,
        case_id,
        "result_overdue_owner",
        {
            "audience": "responsible_doctor",
            "user_id": str(case.current_owner_id) if case.current_owner_id else None,
            "order_id": str(case.order_id),
            "test_name": case.test_name,
        },
    )

    # "Re-check timer every 24h until resolved, max 7 days."
    await _schedule_recheck(session, case_id, first_raised_at=now, now=now)


async def _schedule_recheck(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    first_raised_at: dt.datetime,
    now: dt.datetime,
) -> None:
    """Book the next 24h look, or the unit-head escalation if 7 days are up."""
    when = next_recheck_at(first_raised_at, now)

    if when is None:
        # Ceiling reached. One escalation timer, fired immediately.
        created = await create_timer(
            session,
            case_id=case_id,
            timer_type="unit_head_escalation",
            fire_at=now,
            now=now,
        )
        await record_event(
            session,
            case_id,
            EVENT_LAB_RECHECK_SCHEDULED,
            {"outcome": "max_reached_escalating", "timer_id": str(created.timer_id)},
            now=now,
        )
        return

    created = await create_timer(
        session,
        case_id=case_id,
        timer_type="owner_reminder",
        fire_at=when,
        now=now,
    )
    await record_event(
        session,
        case_id,
        EVENT_LAB_RECHECK_SCHEDULED,
        {
            "outcome": "recheck_scheduled",
            "fire_at": when.isoformat(),
            "timer_id": str(created.timer_id),
            "timer_was_new": created.created,
        },
        now=now,
    )


async def _fire_lab_recheck(
    session: AsyncSession, case_id: uuid.UUID, timer_id: uuid.UUID, now: dt.datetime
) -> None:
    """The 24h re-check. Still missing? Look again, or escalate at 7 days."""
    case = await _case_snapshot(session, case_id)
    if case is None:
        return

    open_flag = (
        await session.execute(
            text(
                "SELECT id, raised_at FROM lab_flags "
                " WHERE case_id = :c AND resolved_at IS NULL AND deleted_at IS NULL "
                " ORDER BY raised_at LIMIT 1"
            ),
            {"c": str(case_id)},
        )
    ).first()

    if open_flag is None or case.state not in STATES_STILL_AWAITING:
        # Resolved, or the result turned up. Stop looking -- this is the
        # "until resolved" half of the requirement.
        await record_event(
            session,
            case_id,
            "lab_recheck_fired",
            {"timer_id": str(timer_id), "outcome": "resolved_no_further_recheck"},
            now=now,
        )
        return

    await enqueue_notification(
        session,
        case_id,
        "lab_result_still_overdue",
        {
            "audience": "lab_department",
            "department_id": str(case.department_id) if case.department_id else None,
            "flag_id": str(open_flag.id),
        },
    )
    await record_event(
        session,
        case_id,
        "lab_recheck_fired",
        {"timer_id": str(timer_id), "outcome": "still_overdue"},
        now=now,
    )
    await _schedule_recheck(
        session, case_id, first_raised_at=open_flag.raised_at, now=now
    )


async def _fire_unit_head_escalation(
    session: AsyncSession, case_id: uuid.UUID, timer_id: uuid.UUID, now: dt.datetime
) -> None:
    """Seven days of an unresolved lab flag. The unit head owns it now.

    Phase 2.3 ends here: the obligation is recorded and queued. *Who* the unit
    head is notified through, and what happens if they do not act, is Phase
    4's escalation ladder.
    """
    case = await _case_snapshot(session, case_id)
    if case is None:
        return

    await enqueue_notification(
        session,
        case_id,
        "lab_flag_escalated_to_unit_head",
        {
            "audience": "unit_head",
            "user_id": str(case.unit_head_user_id) if case.unit_head_user_id else None,
            "department_id": str(case.department_id) if case.department_id else None,
            "order_id": str(case.order_id),
            "test_name": case.test_name,
        },
    )
    await record_event(
        session,
        case_id,
        EVENT_ESCALATED_TO_UNIT_HEAD,
        {
            "timer_id": str(timer_id),
            "reason": "lab_flag_unresolved_for_7_days",
            "unit_head_user_id": (
                str(case.unit_head_user_id) if case.unit_head_user_id else None
            ),
        },
        now=now,
    )


async def _fire_case_escalation(
    session: AsyncSession,
    case_id: uuid.UUID,
    timer_id: uuid.UUID,
    level: int | None,
    now: dt.datetime,
) -> None:
    """Phase 4.4. One rung of the acknowledgement ladder.

    The rung number rides on the timer row rather than being re-derived from
    the clock: a rung that fires late after a restart is still *its* rung, and
    inferring it from elapsed time would silently run the wrong one.

    ``fire_rung`` never raises — a provider outage or a missing recipient comes
    back as a dispatch outcome — so the remaining rungs keep their timers. The
    plan: *"failure surfaced, ladder continues."*
    """
    if level is None:
        # The database's CHECK makes this unreachable; if it ever happens the
        # timer is malformed and guessing a rung would notify the wrong person.
        log.warning("case_escalation_timer_without_level", timer_id=str(timer_id))
        return
    await fire_rung(session, case_id, level, timer_id=timer_id, at=now)


async def _lock_case_for_escalation(session: AsyncSession, timer_id: uuid.UUID) -> None:
    """Take the case row lock before claiming a ``case_escalation`` timer.

    The unlocked read is only used to decide *which* lock to take; the
    authoritative eligibility check still happens inside
    ``claim_timer_for_firing`` under the timer's own lock.
    """
    row = (
        await session.execute(
            text(
                "SELECT case_id FROM sla_timers "
                " WHERE id = :i AND timer_type = 'case_escalation'"
            ),
            {"i": str(timer_id)},
        )
    ).first()
    if row is None:
        return
    await session.execute(
        text(
            "SELECT id FROM pending_cases "
            " WHERE id = :c AND deleted_at IS NULL FOR UPDATE"
        ),
        {"c": str(row.case_id)},
    )
