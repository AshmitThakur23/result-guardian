"""Acknowledgement and closure. Phase 5.3.

    ``POST /api/cases/{id}/acknowledge`` — requires ``closure_reason`` from enum
    Transaction: set state ``closed``, stop timers, cancel escalations, write
    audit, write ``case_events``
    Reopen path: amended result or manual reopen with reason
    **Bulk acknowledge is not available for CRITICAL cases — deliberate
    friction**

This supersedes Phase 4's bare acknowledgement, which existed only to stop the
escalation ladder and said so: *"The full closure workflow — reason codes,
outcomes, reopening — is Phase 5.3 and is deliberately not built here."* Phase
4's guarantee is unchanged and still holds — acknowledging at any rung cancels
every remaining one — it now simply also records *why*.

**A reason with no explanation is not a reason.** Every one of the five
clinical closures carries a required note, because the closure-reason
distribution in 5.6 is a safety metric: a spike in
``not_clinically_relevant`` means the thresholds are wrong, and that signal is
only readable if each closure says what the clinician actually saw.

**The lock order is ``pending_cases`` then ``sla_timers``**, matching
``close_case`` and ``record_result``. Phase 4's audit found a deadlock caused
by taking these in the other order; nothing here may reverse them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cases import (
    CLINICAL_CLOSURE_REASONS,
    CLOSURE_REASONS_REQUIRING_NOTE,
)
from app.services import audit
from app.services.escalation import ESCALATION_TIMER_TYPE
from app.services.lab_flags import record_event
from app.services.timers import cancel_case_timers

log = structlog.get_logger(__name__)

SEVERITY_CRITICAL = "critical"

EVENT_CASE_ACKNOWLEDGED = "case_acknowledged"
EVENT_CASE_CLOSED = "case_closed"
EVENT_CASE_REOPENED = "case_reopened"
EVENT_NOTE_ADDED = "case_note_added"

# Enough that the closer has to describe something. Ten characters rejects
# "ok", "done" and "." without obstructing a busy clinician who writes
# "Recalled, started ciprofloxacin".
MIN_CLOSURE_NOTE = 10
MIN_REOPEN_REASON = 10


class CaseNotFoundError(LookupError):
    """No such case."""


class ClosureValidationError(ValueError):
    """The closure reason or its note is not acceptable."""


class BulkCriticalRefusedError(Exception):
    """*"Bulk acknowledge is not available for CRITICAL cases."*

    Its own exception type rather than a validation error, because the router
    returns a distinct status and the UI shows a distinct message: this is not
    a malformed request, it is a refusal on purpose.
    """

    def __init__(self, case_ids: list[uuid.UUID]) -> None:
        super().__init__(
            f"{len(case_ids)} of the selected cases are CRITICAL and must be "
            "closed one at a time."
        )
        self.case_ids = case_ids


@dataclass
class CaseClosure:
    case_id: uuid.UUID
    closure_reason: str
    already_closed: bool = False
    cancelled_timer_ids: list[uuid.UUID] = field(default_factory=list)
    audit_seq: int | None = None


def validate_closure(
    reason: str, note: str | None, *, duplicate_of_case_id: uuid.UUID | None = None
) -> str:
    """Check the reason against the enum and its note requirement.

    Returns the cleaned note. Raises rather than defaulting: a closure that
    silently loses its justification is worse than one that fails loudly at
    the moment someone is there to fix it.
    """
    if reason not in CLINICAL_CLOSURE_REASONS:
        raise ClosureValidationError(
            f"'{reason}' is not a closure reason a clinician may choose. "
            f"Expected one of: {', '.join(CLINICAL_CLOSURE_REASONS)}."
        )

    cleaned = (note or "").strip()
    if reason in CLOSURE_REASONS_REQUIRING_NOTE and len(cleaned) < MIN_CLOSURE_NOTE:
        raise ClosureValidationError(
            _NOTE_PROMPTS.get(reason, "This closure reason requires a note.")
        )

    if reason == "duplicate_report" and duplicate_of_case_id is None:
        raise ClosureValidationError(
            "Closing as a duplicate requires a link to the original case."
        )
    return cleaned


# What the clinician is actually being asked for, per reason. The plan
# attaches a specific addendum to each; a generic "a note is required" would
# get a generic note back.
_NOTE_PROMPTS = {
    "action_taken": "Describe the action taken (what was done, and when).",
    "already_handled": "Say where and when this was already handled.",
    "not_clinically_relevant": (
        "Justify why this result is not clinically relevant for this patient."
    ),
    "duplicate_report": "Identify the original case this duplicates.",
    "patient_uncontactable": "Record the contact attempts made.",
}


async def acknowledge_and_close(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    closure_reason: str,
    closure_note: str | None,
    actor_user_id: uuid.UUID,
    actor_ip: str | None = None,
    duplicate_of_case_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> CaseClosure:
    """Acknowledge, close, stop the clock and write the audit row — one
    transaction, **no commit**.

    The caller commits. That is what makes *"writes happen in the same
    transaction as the change — never fire-and-forget"* true rather than
    aspirational: if the commit fails, the case is not closed **and** there is
    no audit row claiming it was.
    """
    cleaned_note = validate_closure(
        closure_reason, closure_note, duplicate_of_case_id=duplicate_of_case_id
    )
    moment = now or dt.datetime.now(dt.UTC)

    # Lock the case first. Same order as close_case and record_result -- see
    # the module docstring; reversing it reintroduces the Phase 4 deadlock.
    row = (
        await session.execute(
            text(
                "SELECT id, state, severity, current_owner_id, acknowledged_at, "
                "       closed_at, closure_reason "
                "  FROM pending_cases WHERE id = :c AND deleted_at IS NULL "
                "   FOR UPDATE"
            ),
            {"c": str(case_id)},
        )
    ).first()
    if row is None:
        raise CaseNotFoundError(str(case_id))

    if row.closed_at is not None:
        # Idempotent: a double-click must not be an error, and must not
        # overwrite the first closer's reason with the second's.
        return CaseClosure(
            case_id=case_id,
            closure_reason=row.closure_reason or closure_reason,
            already_closed=True,
        )

    before = {
        "state": row.state,
        "acknowledged_at": row.acknowledged_at,
        "closed_at": row.closed_at,
        "closure_reason": row.closure_reason,
    }

    await session.execute(
        text(
            "UPDATE pending_cases "
            "   SET state = 'closed', "
            "       acknowledged_at = COALESCE(acknowledged_at, :t), "
            "       closed_at = :t, closure_reason = :reason, "
            "       closure_note = :note, updated_at = :t, updated_by = :a "
            " WHERE id = :c"
        ),
        {
            "c": str(case_id),
            "t": moment,
            "reason": closure_reason,
            "note": cleaned_note or None,
            "a": str(actor_user_id),
        },
    )

    # Every timer, not only the escalation rungs: the case is closed, so the
    # missing-result sweep and the lab re-check chain must stop too.
    cancelled = await cancel_case_timers(session, case_id)

    payload: dict[str, object] = {
        "closure_reason": closure_reason,
        "closure_note": cleaned_note or None,
        "closed_by": str(actor_user_id),
        "cancelled_timer_ids": [str(t) for t in cancelled],
    }
    if duplicate_of_case_id is not None:
        payload["duplicate_of_case_id"] = str(duplicate_of_case_id)

    await record_event(
        session,
        case_id,
        EVENT_CASE_ACKNOWLEDGED,
        {"acknowledged_by": str(actor_user_id), "closure_reason": closure_reason},
        actor_user_id=actor_user_id,
        now=moment,
    )
    await record_event(
        session,
        case_id,
        EVENT_CASE_CLOSED,
        payload,
        actor_user_id=actor_user_id,
        now=moment,
    )

    entry = await audit.append(
        session,
        action=audit.ACTION_CASE_CLOSED,
        entity_type="pending_case",
        entity_id=case_id,
        actor_user_id=actor_user_id,
        actor_ip=actor_ip,
        before=before,
        after={
            "state": "closed",
            "closed_at": moment,
            "closure_reason": closure_reason,
            "closure_note": cleaned_note or None,
            "duplicate_of_case_id": duplicate_of_case_id,
            "cancelled_timers": len(cancelled),
        },
        occurred_at=moment,
    )

    return CaseClosure(
        case_id=case_id,
        closure_reason=closure_reason,
        cancelled_timer_ids=cancelled,
        audit_seq=entry.seq,
    )


async def bulk_acknowledge(
    session: AsyncSession,
    case_ids: list[uuid.UUID],
    *,
    closure_reason: str,
    closure_note: str | None,
    actor_user_id: uuid.UUID,
    actor_ip: str | None = None,
    now: dt.datetime | None = None,
) -> list[CaseClosure]:
    """Close several cases at once — **never a CRITICAL one**.

    The check runs over the whole selection *before* anything is closed, and
    a single CRITICAL case rejects the entire batch. Closing the others and
    reporting the failures afterwards would be friendlier and wrong: the point
    of the friction is that a CRITICAL result gets its own deliberate act, and
    a batch that half-succeeded invites the clinician to re-run it without
    reading what was skipped.
    """
    if not case_ids:
        return []

    unique_ids = list(dict.fromkeys(case_ids))
    critical = (
        (
            await session.execute(
                text(
                    "SELECT id FROM pending_cases "
                    " WHERE id = ANY(CAST(:ids AS uuid[])) AND severity = :sev "
                    "   AND deleted_at IS NULL AND closed_at IS NULL "
                    " ORDER BY id"
                ),
                {"ids": [str(c) for c in unique_ids], "sev": SEVERITY_CRITICAL},
            )
        )
        .scalars()
        .all()
    )

    if critical:
        log.info(
            "bulk_acknowledge_refused_critical",
            critical_count=len(critical),
            selected=len(unique_ids),
        )
        raise BulkCriticalRefusedError([uuid.UUID(str(c)) for c in critical])

    return [
        await acknowledge_and_close(
            session,
            case_id,
            closure_reason=closure_reason,
            closure_note=closure_note,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            now=now,
        )
        for case_id in unique_ids
    ]


@dataclass
class CaseReopened:
    case_id: uuid.UUID
    reopened_count: int
    reason: str


async def reopen_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    reason: str,
    actor_user_id: uuid.UUID | None,
    actor_ip: str | None = None,
    amended_result_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> CaseReopened:
    """*"Reopen path: amended result or manual reopen with reason."*

    Reopening clears ``closed_at`` and the closure reason but **increments
    ``reopened_count`` and leaves the whole history**: the original closure,
    its reason and its note stay in ``case_events`` and in the audit chain. A
    reopen is a new chapter, not an erasure — and ``reopened_count`` is what
    makes "this case has been closed and reopened three times" visible to 5.6.

    It deliberately does **not** restart the escalation ladder. Phase 4 starts
    the ladder from classification, and a reopened case that immediately began
    paging the on-call at rung 1 would punish the clinician who reopened it
    honestly. The case returns to the worklist, which is what a human needs.

    ``actor_user_id`` is nullable for exactly one caller: an amended result
    arriving from the lab reopens the case with no human actor.
    """
    cleaned = (reason or "").strip()
    if len(cleaned) < MIN_REOPEN_REASON:
        raise ClosureValidationError(
            "Reopening a case requires a reason of at least "
            f"{MIN_REOPEN_REASON} characters."
        )
    moment = now or dt.datetime.now(dt.UTC)

    row = (
        await session.execute(
            text(
                "SELECT id, state, closed_at, closure_reason, reopened_count "
                "  FROM pending_cases WHERE id = :c AND deleted_at IS NULL "
                "   FOR UPDATE"
            ),
            {"c": str(case_id)},
        )
    ).first()
    if row is None:
        raise CaseNotFoundError(str(case_id))
    if row.closed_at is None:
        raise ClosureValidationError(
            "This case is not closed, so it cannot be reopened."
        )

    before = {
        "state": row.state,
        "closed_at": row.closed_at,
        "closure_reason": row.closure_reason,
        "reopened_count": row.reopened_count,
    }
    new_count = int(row.reopened_count) + 1

    await session.execute(
        text(
            "UPDATE pending_cases "
            "   SET state = 'reopened', closed_at = NULL, closure_reason = NULL, "
            "       closure_note = NULL, acknowledged_at = NULL, "
            "       reopened_count = :n, updated_at = :t, updated_by = :a "
            " WHERE id = :c"
        ),
        {
            "c": str(case_id),
            "n": new_count,
            "t": moment,
            "a": str(actor_user_id) if actor_user_id else None,
        },
    )

    payload: dict[str, object] = {"reason": cleaned, "reopened_count": new_count}
    if amended_result_id is not None:
        payload["amended_result_id"] = str(amended_result_id)
        payload["trigger"] = "amended_result"
    else:
        payload["trigger"] = "manual"

    await record_event(
        session,
        case_id,
        EVENT_CASE_REOPENED,
        payload,
        actor_user_id=actor_user_id,
        now=moment,
    )
    await audit.append(
        session,
        action=audit.ACTION_CASE_REOPENED,
        entity_type="pending_case",
        entity_id=case_id,
        actor_user_id=actor_user_id,
        actor_ip=actor_ip,
        before=before,
        after={"state": "reopened", "reason": cleaned, "reopened_count": new_count},
        occurred_at=moment,
    )
    log.info(
        "case_reopened",
        case_id=str(case_id),
        reopened_count=new_count,
        trigger=payload["trigger"],
    )
    return CaseReopened(case_id=case_id, reopened_count=new_count, reason=cleaned)


async def add_note(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    note: str,
    actor_user_id: uuid.UUID,
    actor_ip: str | None = None,
    now: dt.datetime | None = None,
) -> uuid.UUID:
    """The action bar's third button. Adds to the timeline, changes nothing."""
    cleaned = (note or "").strip()
    if not cleaned:
        raise ClosureValidationError("A note cannot be empty.")
    moment = now or dt.datetime.now(dt.UTC)

    exists = (
        await session.execute(
            text("SELECT id FROM pending_cases WHERE id = :c AND deleted_at IS NULL"),
            {"c": str(case_id)},
        )
    ).first()
    if exists is None:
        raise CaseNotFoundError(str(case_id))

    await record_event(
        session,
        case_id,
        EVENT_NOTE_ADDED,
        {"note": cleaned, "author_id": str(actor_user_id)},
        actor_user_id=actor_user_id,
        now=moment,
    )
    await audit.append(
        session,
        action=audit.ACTION_CASE_NOTE_ADDED,
        entity_type="pending_case",
        entity_id=case_id,
        actor_user_id=actor_user_id,
        actor_ip=actor_ip,
        after={"note": cleaned},
        occurred_at=moment,
    )
    return case_id


async def cancel_remaining_escalations(
    session: AsyncSession, case_id: uuid.UUID
) -> list[uuid.UUID]:
    """Escalation rungs only — used where the case itself stays open."""
    return await cancel_case_timers(
        session, case_id, only_types=(ESCALATION_TIMER_TYPE,)
    )
