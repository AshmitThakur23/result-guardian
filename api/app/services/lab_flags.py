"""Lab-side accountability. Phase 2.3.

    The lab must be flagged too, not just the doctor. **A missing result is a
    lab-side failure.**

When a ``result_due`` timer fires and nothing has arrived, two things are true
at once: the doctor needs to know, and *the lab did not produce a report*.
Chasing only the doctor teaches a hospital that the product blames the wrong
people, and that is how a pilot dies.

So this raises a flag against the lab, notifies both sides, and keeps
re-checking: *"Re-check timer every 24h until resolved, max 7 days, then
escalate to unit head."*

**Notification boundary.** Phase 2 enqueues an *intent* onto the existing
``notifications`` queue and stops there. Which channel, which template, quiet
hours, the escalation ladder, and the deceased/transferred suppression rules
are all Phase 4 and are deliberately absent -- the durable record that someone
must be told is Phase 2's job; deciding how belongs to the phase that owns it.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.lab import (
    LAB_FLAG_TYPES,
    LAB_RECHECK_INTERVAL,
    LAB_RECHECK_MAX,
)
from app.db.types import uuid7

NOTIFICATIONS_QUEUE = "notifications"

# The flag a fired result_due raises. The other two types are raised by a
# human working the lab queue, not by a timer.
FLAG_REPORT_DELAYED = "report_delayed"

# Phase 1.1's case_events vocabulary, extended for Phase 2's lifecycle.
EVENT_RESULT_DUE_FIRED = "result_due_fired"
EVENT_LAB_FLAG_RAISED = "lab_flag_raised"
EVENT_LAB_FLAG_RESOLVED = "lab_flag_resolved"
EVENT_LAB_RECHECK_SCHEDULED = "lab_recheck_scheduled"
EVENT_ESCALATED_TO_UNIT_HEAD = "escalated_to_unit_head"


class LabFlagNotFoundError(LookupError):
    """No such open flag. The router turns this into a 404."""


@dataclass(frozen=True)
class FlagRaised:
    flag_id: uuid.UUID
    created: bool
    """False when this case already had an open flag of that type."""


async def record_event(
    session: AsyncSession,
    case_id: uuid.UUID,
    event_type: str,
    payload: dict[str, object],
    *,
    actor_user_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> uuid.UUID:
    """Append one row to the case's history.

    INSERT only -- ``case_events`` is append-only and its trigger refuses
    UPDATE and DELETE. Phase 2 adds event types to the existing log rather
    than building a second audit mechanism beside it.
    """
    event_id = uuid7()
    await session.execute(
        text(
            "INSERT INTO case_events "
            "(id, case_id, event_type, actor_user_id, payload, occurred_at) "
            "VALUES (:i, :c, :t, :a, CAST(:p AS jsonb), :o)"
        ),
        {
            "i": str(event_id),
            "c": str(case_id),
            "t": event_type,
            "a": str(actor_user_id) if actor_user_id else None,
            "p": json.dumps(payload),
            "o": now or dt.datetime.now(dt.UTC),
        },
    )
    return event_id


async def enqueue_notification(
    session: AsyncSession,
    case_id: uuid.UUID,
    template_key: str,
    payload: dict[str, object],
) -> int:
    """Record that somebody must be told, without deciding how.

    Phase 4.3 builds the dispatcher that consumes this. Until then the message
    sits durably on the queue, which is the correct half-built state: the
    obligation is recorded and survives a restart, and no channel is guessed
    at.
    """
    msg_id = (
        await session.execute(
            text("SELECT pgmq.send(:q, CAST(:p AS jsonb))"),
            {
                "q": NOTIFICATIONS_QUEUE,
                "p": json.dumps(
                    {"case_id": str(case_id), "template_key": template_key, **payload}
                ),
            },
        )
    ).scalar()
    return int(msg_id or 0)


async def raise_lab_flag(
    session: AsyncSession,
    case_id: uuid.UUID,
    flag_type: str = FLAG_REPORT_DELAYED,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> FlagRaised:
    """Raise a lab-side flag, once.

    Idempotent through a partial unique index rather than a check-then-insert:
    ``uq_lab_flags_open_per_case_type`` allows only one *open* flag of a given
    type per case, so a duplicate timer delivery or a repeated 24h re-check
    cannot fill the lab's queue with copies of one problem. A flag that was
    resolved and recurs *does* raise again, which is right -- that is a second
    failure, not a duplicate of the first.
    """
    if flag_type not in LAB_FLAG_TYPES:
        raise ValueError(f"unknown lab flag type: {flag_type}")

    flag_id = uuid7()
    inserted = (
        await session.execute(
            text(
                "INSERT INTO lab_flags "
                "(id, case_id, flag_type, raised_at, created_by, updated_by) "
                "VALUES (:i, :c, :t, now(), :a, :a) "
                "ON CONFLICT (case_id, flag_type) "
                "  WHERE resolved_at IS NULL AND deleted_at IS NULL "
                "DO NOTHING RETURNING id"
            ),
            {
                "i": str(flag_id),
                "c": str(case_id),
                "t": flag_type,
                "a": str(actor_user_id) if actor_user_id else None,
            },
        )
    ).first()

    if inserted is None:
        existing = (
            await session.execute(
                text(
                    "SELECT id FROM lab_flags "
                    " WHERE case_id = :c AND flag_type = :t "
                    "   AND resolved_at IS NULL AND deleted_at IS NULL"
                ),
                {"c": str(case_id), "t": flag_type},
            )
        ).scalar_one()
        return FlagRaised(flag_id=existing, created=False)

    await record_event(
        session,
        case_id,
        EVENT_LAB_FLAG_RAISED,
        {"flag_id": str(flag_id), "flag_type": flag_type},
        actor_user_id=actor_user_id,
    )
    return FlagRaised(flag_id=flag_id, created=True)


async def resolve_lab_flag(
    session: AsyncSession,
    flag_id: uuid.UUID,
    resolved_by: uuid.UUID,
    note: str | None = None,
) -> uuid.UUID:
    """Close a flag. The lab found the sample, or the report arrived.

    ``WHERE resolved_at IS NULL`` makes a double-resolve a no-op rather than a
    rewrite of who resolved it first.
    """
    row = (
        await session.execute(
            text(
                "UPDATE lab_flags "
                "   SET resolved_at = now(), resolved_by = :u, "
                "       resolution_note = :n, updated_by = :u, updated_at = now() "
                " WHERE id = :i AND resolved_at IS NULL AND deleted_at IS NULL "
                " RETURNING case_id"
            ),
            {"i": str(flag_id), "u": str(resolved_by), "n": note},
        )
    ).first()

    if row is None:
        raise LabFlagNotFoundError(str(flag_id))

    await record_event(
        session,
        row.case_id,
        EVENT_LAB_FLAG_RESOLVED,
        {"flag_id": str(flag_id), "note": note},
        actor_user_id=resolved_by,
    )
    return uuid.UUID(str(row.case_id))


def next_recheck_at(
    first_raised_at: dt.datetime, now: dt.datetime
) -> dt.datetime | None:
    """When to look again, or ``None`` once the 7-day ceiling is reached.

    Phase 2.3: *"Re-check timer every 24h until resolved, max 7 days, then
    escalate to unit head."* Returning ``None`` is what tells the caller to
    escalate instead of scheduling yet another look.
    """
    elapsed = now - first_raised_at
    if elapsed >= LAB_RECHECK_MAX:
        return None
    candidate = now + LAB_RECHECK_INTERVAL
    ceiling = first_raised_at + LAB_RECHECK_MAX
    # Never schedule a re-check past the ceiling: the escalation is due then,
    # and a timer beyond it would fire into a case that has already moved on.
    return min(candidate, ceiling)
