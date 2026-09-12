"""Case lifecycle as it touches timers. Phase 2.2.

Two of 2.2's five bullets are really statements about the *case*, not the
timer:

* *"Cancel all case timers atomically on case closure."*
* *"Pause capability for patient ``deceased`` / ``transferred`` states."*

So they live here, next to the case, rather than being bolted onto the timer
service. ``app/services/timers.py`` owns the mechanics; this owns when they
are invoked and what gets written to the case's history.

**Scope.** This is not Phase 5.3's closure workflow. There is no reason-code
vocabulary, no acknowledgement flow and no reopening — those are Phase 5's,
and ``pending_cases.closure_reason`` is deliberately still unconstrained
pending that phase. What is here is only the part Phase 2 owns: when a case
stops being open, its timers must stop firing, atomically.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.timers import PAUSE_REASONS
from app.services.lab_flags import record_event
from app.services.timers import (
    cancel_case_timers,
    pause_case_timers,
    resume_case_timers,
)

EVENT_CASE_CLOSED = "case_closed"
EVENT_TIMERS_CANCELLED = "timers_cancelled"
EVENT_TIMERS_PAUSED = "timers_paused"
EVENT_TIMERS_RESUMED = "timers_resumed"

# The encounter statuses Phase 2.2 names for pausing. Read from the encounter,
# never supplied by a caller: a client that could assert "this patient died"
# could silence a case's timers at will.
PAUSING_ENCOUNTER_STATUSES = PAUSE_REASONS


class CaseNotFoundError(LookupError):
    """No such case. The router turns this into a 404."""


@dataclass
class CaseClosed:
    case_id: uuid.UUID
    already_closed: bool
    cancelled_timer_ids: list[uuid.UUID] = field(default_factory=list)


async def close_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    reason: str | None = None,
    note: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> CaseClosed:
    """Close a case and stop its clock, in one transaction.

    The case row is locked first. That is what makes "atomically" true against
    a worker mid-fire: the worker holds a lock on the *timer* row and this
    holds one on the *case*, and the cancel below then blocks on the timer
    until the worker commits. Whichever wins, the loser sees the other's
    result and declines — there is no ordering in which a timer both fires and
    is cancelled.
    """
    moment = now or dt.datetime.now(dt.UTC)

    case = (
        await session.execute(
            text(
                "SELECT id, state FROM pending_cases "
                " WHERE id = :i AND deleted_at IS NULL FOR UPDATE"
            ),
            {"i": str(case_id)},
        )
    ).first()
    if case is None:
        raise CaseNotFoundError(str(case_id))

    if case.state == "closed":
        # Idempotent: a replayed closure must not write a second closed_at or
        # a second event. Timers were already cancelled the first time.
        return CaseClosed(case_id=case_id, already_closed=True)

    await session.execute(
        text(
            "UPDATE pending_cases "
            "   SET state = 'closed', closed_at = :t, closure_reason = :r, "
            "       closure_note = :n, updated_by = :a, updated_at = now() "
            " WHERE id = :i"
        ),
        {
            "i": str(case_id),
            "t": moment,
            "r": reason,
            "n": note,
            "a": str(actor_user_id) if actor_user_id else None,
        },
    )

    cancelled = await cancel_case_timers(session, case_id)

    await record_event(
        session,
        case_id,
        EVENT_CASE_CLOSED,
        {"reason": reason, "note": note, "cancelled_timers": len(cancelled)},
        actor_user_id=actor_user_id,
        now=moment,
    )
    if cancelled:
        await record_event(
            session,
            case_id,
            EVENT_TIMERS_CANCELLED,
            {"timer_ids": [str(t) for t in cancelled], "reason": "case_closed"},
            actor_user_id=actor_user_id,
            now=moment,
        )

    return CaseClosed(
        case_id=case_id, already_closed=False, cancelled_timer_ids=cancelled
    )


@dataclass
class CasePaused:
    case_id: uuid.UUID
    reason: str
    paused_timer_ids: list[uuid.UUID] = field(default_factory=list)


async def pause_case_for_encounter_status(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> CasePaused | None:
    """Pause a case's timers because its patient died or was transferred.

    The reason is **read from the encounter**, never accepted from a caller.
    A client able to assert "this patient is deceased" could silence any
    case's timers at will, which is the one thing a tracking product must not
    allow.

    Returns ``None`` when the encounter is in neither state — nothing to do,
    and not an error.
    """
    moment = now or dt.datetime.now(dt.UTC)

    row = (
        await session.execute(
            text(
                "SELECT pc.id, e.status FROM pending_cases pc "
                "  JOIN encounters e ON e.id = pc.encounter_id "
                " WHERE pc.id = :i AND pc.deleted_at IS NULL"
            ),
            {"i": str(case_id)},
        )
    ).first()
    if row is None:
        raise CaseNotFoundError(str(case_id))

    if row.status not in PAUSING_ENCOUNTER_STATUSES:
        return None

    paused = await pause_case_timers(session, case_id, row.status)
    if paused:
        await record_event(
            session,
            case_id,
            EVENT_TIMERS_PAUSED,
            {
                "timer_ids": [str(t) for t in paused],
                "reason": row.status,
                "source": "encounter_status",
            },
            actor_user_id=actor_user_id,
            now=moment,
        )
    return CasePaused(case_id=case_id, reason=row.status, paused_timer_ids=paused)


async def resume_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> list[uuid.UUID]:
    """Undo a pause and put the doorbells back.

    Deterministic by construction: the timers kept their original ``fire_at``
    while paused, so resuming restores the *same* deadlines rather than
    deriving new ones from the resume time. A result that was due on Tuesday
    is still due on Tuesday, even if the pause ran over it — in which case the
    re-enqueued wake-up is immediately visible and the handler picks it up at
    once.
    """
    moment = now or dt.datetime.now(dt.UTC)

    exists = (
        await session.execute(
            text("SELECT id FROM pending_cases WHERE id = :i AND deleted_at IS NULL"),
            {"i": str(case_id)},
        )
    ).first()
    if exists is None:
        raise CaseNotFoundError(str(case_id))

    resumed = await resume_case_timers(session, case_id, now=moment)
    if resumed:
        await record_event(
            session,
            case_id,
            EVENT_TIMERS_RESUMED,
            {"timer_ids": [str(t) for t in resumed]},
            actor_user_id=actor_user_id,
            now=moment,
        )
    return resumed
