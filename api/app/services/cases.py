"""Case lifecycle as it touches timers. Phase 2.2.

Two of 2.2's five bullets are really statements about the *case*, not the
timer:

* *"Cancel all case timers atomically on case closure."*
* *"Pause capability for patient ``deceased`` / ``transferred`` states."*

So they live here, next to the case, rather than being bolted onto the timer
service. ``app/services/timers.py`` owns the mechanics; this owns when they
are invoked and what gets written to the case's history.

**Scope.** This is not Phase 5.3's closure workflow. There is no
acknowledgement flow and no reopening — those live in
``app/services/closure.py``. What is here is only the part Phase 2 owns: when
a case stops being open, its timers must stop firing, atomically.

*Updated in Phase 5.3:* ``pending_cases.closure_reason`` is no longer
unconstrained. It now carries a CHECK against the vocabulary in
``app/db/models/cases.py``, so :func:`close_case` validates its ``reason``
before writing. The only production caller is Phase 3's auto-close, which
passes ``auto_closed_normal``; the validation exists so a future caller gets
a clear error rather than an IntegrityError raised after the timers have
already been cancelled.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cases import CLOSURE_REASONS
from app.db.models.timers import PAUSE_REASONS
from app.services.lab_flags import record_event
from app.services.timers import (
    cancel_case_timers,
    pause_case_timers,
    resume_case_timers,
)

# The row-lock `close_case` takes on the parent case. Named, and exported, so
# the deadlock regression test locks the row exactly the way production does --
# a test that hardcodes its own lock clause cannot detect this changing back.
CASE_ROW_LOCK = "FOR NO KEY UPDATE"

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


class UnknownClosureReasonError(ValueError):
    """The reason is not in Phase 5.3's vocabulary."""


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

    # `FOR NO KEY UPDATE`, not `FOR UPDATE`. This transaction never changes
    # `pending_cases.id`, so the weaker lock is the correct one: it still blocks
    # concurrent UPDATE, DELETE and `FOR UPDATE`, while not conflicting with the
    # `FOR KEY SHARE` that Postgres takes on this row for every INSERT into a
    # table referencing it.
    #
    # ⚠️ Chosen while investigating the CI deadlock recorded in PROGRESS.md
    # (2026-09-14). It reduces this row's lock footprint, which is a real
    # improvement, but **it is NOT a proven fix for that deadlock** -- a test
    # written to catch the defect passed with this reverted, so the cycle lies
    # elsewhere and is still open. Do not treat this comment as a resolution.
    case = (
        await session.execute(
            text(
                "SELECT id, state FROM pending_cases "
                f" WHERE id = :i AND deleted_at IS NULL {CASE_ROW_LOCK}"
            ),
            {"i": str(case_id)},
        )
    ).first()
    if case is None:
        raise CaseNotFoundError(str(case_id))

    # Phase 5.3 gave `closure_reason` a CHECK constraint. Validating here
    # turns what would otherwise surface as an IntegrityError from deep inside
    # a multi-statement transaction -- after the timers were already
    # cancelled, and rolled back with them -- into a clear failure before
    # anything is written.
    if reason is not None and reason not in CLOSURE_REASONS:
        raise UnknownClosureReasonError(
            f"'{reason}' is not a closure reason. Expected one of: "
            f"{', '.join(CLOSURE_REASONS)}."
        )

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
