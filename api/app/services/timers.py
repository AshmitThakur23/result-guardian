"""The SLA timer lifecycle. Phase 2.2.

Phase 2.1 built the table. This is everything that moves a row through it:
create, fire, cancel, supersede, pause, resume.

──────────────────────────────────────────────────────────────────────────
THE ONE INVARIANT
──────────────────────────────────────────────────────────────────────────

**PostgreSQL is timer truth. pgmq is only a doorbell.**

Every operation here reads and writes the ``sla_timers`` row, and treats the
queue message as a disposable hint. A message that arrives twice, arrives
late, or never arrives at all must not change the answer to "has this timer
fired?" — only the row does.

Concretely, that means:

* **Creation** is idempotent through the database, not through a check-then-act
  in Python. ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`` either
  wins or it does not, and only the winner enqueues a wake-up. Two concurrent
  discharges of the same case therefore produce one timer and one message.
* **Firing** takes ``SELECT ... FOR UPDATE`` on the timer row and re-reads the
  status *inside* that lock. A duplicate delivery finds ``status = 'fired'``
  and does nothing. This is the build plan's own wording: *"check
  ``status != 'fired'`` inside a ``SELECT ... FOR UPDATE`` before acting"*.
* **Cancellation and supersession** take the same lock, so a worker that is
  mid-fire either completes first (and the cancel finds nothing pending) or
  waits (and then finds the timer already cancelled and does nothing). Either
  ordering is safe; both cannot happen.

Nothing here talks to NODE B, and nothing here waits on anything that does.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.timers import PAUSE_REASONS, TIMER_TYPES, idempotency_key
from app.db.types import uuid7

SLA_TIMER_QUEUE = "sla_timers"

# pgmq's delay is an integer number of seconds. A deadline already in the past
# becomes 0 -- visible immediately -- rather than a negative delay.
MAX_PGMQ_DELAY_S = 2_147_483_647


@dataclass(frozen=True)
class TimerCreated:
    """What ``create_timer`` did, so callers can tell new from existing."""

    timer_id: uuid.UUID
    idempotency_key: str
    pgmq_msg_id: int | None
    created: bool
    """False when an identical timer already existed and was left alone."""


def _delay_seconds(fire_at: dt.datetime, now: dt.datetime) -> int:
    return max(0, min(int((fire_at - now).total_seconds()), MAX_PGMQ_DELAY_S))


async def create_timer(
    session: AsyncSession,
    case_id: uuid.UUID,
    timer_type: str,
    fire_at: dt.datetime,
    *,
    order_id: uuid.UUID | None = None,
    encounter_id: uuid.UUID | None = None,
    contract_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
    escalation_level: int | None = None,
) -> TimerCreated:
    """Create one timer and its wake-up, idempotently, in the caller's
    transaction.

    Order matters and is deliberate: **truth first, doorbell second.**

    1. ``INSERT ... ON CONFLICT DO NOTHING`` the durable row.
    2. Only if that insert won, ``pgmq.send`` the wake-up.
    3. Write the returned ``msg_id`` back onto the row.

    All three share the caller's transaction, so the row and the message
    commit together or not at all — ADR 0001's single-Postgres choice is what
    makes that possible. A replay finds the row already there, enqueues
    nothing, and returns ``created=False``.

    The reverse order would be worse in a specific way: enqueueing first means
    a rollback leaves a message referring to a timer that does not exist, and
    the handler would have to treat "no such timer" as normal — which would
    also hide the case where a timer genuinely vanished.
    """
    if timer_type not in TIMER_TYPES:
        raise ValueError(f"unknown timer_type: {timer_type}")
    if fire_at.tzinfo is None:
        raise ValueError("fire_at must be timezone-aware")
    # Phase 4.4. The database enforces the biconditional too; refusing here
    # gives the caller a readable error instead of an IntegrityError.
    if (timer_type == "case_escalation") != (escalation_level is not None):
        raise ValueError(
            "escalation_level belongs to case_escalation timers and to no "
            f"other type (got type={timer_type!r}, level={escalation_level!r})"
        )

    moment = now or dt.datetime.now(dt.UTC)
    key = idempotency_key(case_id, timer_type, fire_at, escalation_level)
    timer_id = uuid7()

    inserted = (
        await session.execute(
            text(
                "INSERT INTO sla_timers "
                "(id, case_id, timer_type, fire_at, status, attempts, "
                " idempotency_key, escalation_level, created_by, updated_by) "
                "VALUES (:i, :c, :tt, :f, 'pending', 0, :k, :lvl, :a, :a) "
                "ON CONFLICT (idempotency_key) DO NOTHING "
                "RETURNING id"
            ),
            {
                "i": str(timer_id),
                "c": str(case_id),
                "tt": timer_type,
                "f": fire_at,
                "k": key,
                "lvl": escalation_level,
                "a": str(actor_user_id) if actor_user_id else None,
            },
        )
    ).first()

    if inserted is None:
        # Somebody already created this exact timer. Do not enqueue a second
        # wake-up: the first one is still the doorbell for the same row.
        existing = (
            await session.execute(
                text(
                    "SELECT id, pgmq_msg_id FROM sla_timers "
                    "WHERE idempotency_key = :k"
                ),
                {"k": key},
            )
        ).one()
        return TimerCreated(
            timer_id=existing.id,
            idempotency_key=key,
            pgmq_msg_id=existing.pgmq_msg_id,
            created=False,
        )

    payload = json.dumps(
        {
            "timer_type": timer_type,
            "timer_id": str(timer_id),
            "case_id": str(case_id),
            "order_id": str(order_id) if order_id else None,
            "encounter_id": str(encounter_id) if encounter_id else None,
            "contract_id": str(contract_id) if contract_id else None,
            "fire_at": fire_at.astimezone(dt.UTC).isoformat(),
            "escalation_level": escalation_level,
        }
    )
    msg_id = (
        await session.execute(
            text("SELECT pgmq.send(:q, CAST(:p AS jsonb), CAST(:d AS integer))"),
            {
                "q": SLA_TIMER_QUEUE,
                "p": payload,
                "d": _delay_seconds(fire_at, moment),
            },
        )
    ).scalar()

    await session.execute(
        text("UPDATE sla_timers SET pgmq_msg_id = :m WHERE id = :i"),
        {"m": int(msg_id or 0), "i": str(timer_id)},
    )

    return TimerCreated(
        timer_id=timer_id,
        idempotency_key=key,
        pgmq_msg_id=int(msg_id or 0),
        created=True,
    )


@dataclass(frozen=True)
class TimerClaim:
    """A timer this worker has exclusively locked and may act on."""

    timer_id: uuid.UUID
    case_id: uuid.UUID
    timer_type: str
    fire_at: dt.datetime
    attempts: int
    escalation_level: int | None = None
    """Phase 4.4's rung, for a ``case_escalation`` timer. Null for every
    Phase 2 type."""


async def claim_timer_for_firing(
    session: AsyncSession, timer_id: uuid.UUID
) -> TimerClaim | None:
    """Lock a timer and decide whether it may fire. The heart of idempotency.

    Returns the claim when this caller now holds the row lock **and** the timer
    is still eligible; returns ``None`` when it is not — already fired,
    cancelled, superseded, paused or soft-deleted.

    The lock is held until the caller's transaction ends, so the whole
    fire-and-record sequence is serialised against a second worker, a case
    closure, and a result arrival alike. That is what makes "fires exactly
    once" a database property rather than a hope about queue semantics.

    ``attempts`` is incremented here rather than in the handler: it counts
    *claims*, so a handler that crashes mid-work and is redelivered still
    shows the retry. A timer that never gets claimed never increments, which
    is what makes the sweep's own increment meaningful.
    """
    row = (
        await session.execute(
            text(
                "SELECT id, case_id, timer_type, fire_at, status, attempts, "
                "       paused_at, deleted_at, escalation_level "
                "  FROM sla_timers WHERE id = :i FOR UPDATE"
            ),
            {"i": str(timer_id)},
        )
    ).first()

    if row is None:
        return None
    # Every one of these is a legitimate reason not to fire, and all of them
    # are read *inside* the lock so a concurrent writer cannot change the
    # answer between the read and the act.
    if row.deleted_at is not None:
        return None
    if row.status != "pending":
        return None
    if row.paused_at is not None:
        return None

    await session.execute(
        text(
            "UPDATE sla_timers SET attempts = attempts + 1, updated_at = now() "
            "WHERE id = :i"
        ),
        {"i": str(timer_id)},
    )

    return TimerClaim(
        timer_id=row.id,
        case_id=row.case_id,
        timer_type=row.timer_type,
        fire_at=row.fire_at,
        attempts=row.attempts + 1,
        escalation_level=row.escalation_level,
    )


async def mark_fired(
    session: AsyncSession, timer_id: uuid.UUID, now: dt.datetime | None = None
) -> None:
    """Record that a claimed timer fired.

    ``fired_at`` and ``status`` are set in one statement because the database
    requires them to agree (Phase 2.1's
    ``ck_sla_timers_fired_at_matches_status``). Setting one without the other
    is refused, which is the point of the constraint.

    Only ever called while holding the claim from ``claim_timer_for_firing``.
    """
    await session.execute(
        text(
            "UPDATE sla_timers "
            "   SET status = 'fired', fired_at = :t, updated_at = now() "
            " WHERE id = :i AND status = 'pending'"
        ),
        {"i": str(timer_id), "t": now or dt.datetime.now(dt.UTC)},
    )


async def cancel_case_timers(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    only_types: tuple[str, ...] | None = None,
    new_status: str = "cancelled",
) -> list[uuid.UUID]:
    """Cancel every still-pending timer on a case, atomically.

    Phase 2.2: *"Cancel all case timers atomically on case closure."* One
    statement, so there is no window in which half a case's timers are
    cancelled — and ``WHERE status = 'pending'`` means a timer that a worker
    already fired is left as history rather than rewritten.

    Race safety against a firing worker comes from the row lock that worker
    holds: this UPDATE blocks until it commits, then sees ``status = 'fired'``
    and skips it. If this commits first, the worker's claim finds
    ``status = 'cancelled'`` and declines to fire. Both orderings are correct;
    neither produces a fired *and* cancelled timer.

    ``new_status`` exists so supersession can reuse the identical mechanism
    with the status the plan names for it.
    """
    if new_status not in ("cancelled", "superseded"):
        raise ValueError(f"not a terminal status for this operation: {new_status}")

    sql = (
        "UPDATE sla_timers SET status = :s, updated_at = now() "
        " WHERE case_id = :c AND status = 'pending' AND deleted_at IS NULL"
    )
    params: dict[str, object] = {"c": str(case_id), "s": new_status}
    if only_types is not None:
        if not only_types:
            return []
        sql += " AND timer_type = ANY(:types)"
        params["types"] = list(only_types)
    sql += " RETURNING id"

    rows = (await session.execute(text(sql), params)).scalars().all()
    return list(rows)


async def supersede_result_due(
    session: AsyncSession, case_id: uuid.UUID
) -> list[uuid.UUID]:
    """A result arrived: the deadline it was waiting for no longer applies.

    Phase 2.2: *"Supersede: new result arrives → cancel ``result_due``, create
    classification timers."* Superseded rather than cancelled, because the
    plan distinguishes the two and the distinction is real — cancelled means
    "this stopped mattering", superseded means "something replaced it". An
    audit six months later can tell a result that arrived from a case someone
    closed.
    """
    return await cancel_case_timers(
        session, case_id, only_types=("result_due",), new_status="superseded"
    )


async def pause_case_timers(
    session: AsyncSession, case_id: uuid.UUID, reason: str
) -> list[uuid.UUID]:
    """Stop a case's timers firing without destroying them. Phase 2.2.

    *"Pause capability for patient ``deceased`` / ``transferred`` states."*

    The timers stay ``pending`` and keep their deadlines — the case is still
    tracked and still visible, it simply stops chasing people about a patient
    who has died or left. Cancelling instead would lose the deadline, and
    resuming would then have to invent a new one.

    Phase 4.6 decides who to notify instead. Phase 2 only provides the switch.
    """
    if reason not in PAUSE_REASONS:
        raise ValueError(f"not a pause reason the plan names: {reason}")

    rows = (
        (
            await session.execute(
                text(
                    "UPDATE sla_timers "
                    "   SET paused_at = now(), pause_reason = :r, updated_at = now() "
                    " WHERE case_id = :c AND status = 'pending' "
                    "   AND paused_at IS NULL AND deleted_at IS NULL "
                    " RETURNING id"
                ),
                {"c": str(case_id), "r": reason},
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def resume_case_timers(
    session: AsyncSession, case_id: uuid.UUID, *, now: dt.datetime | None = None
) -> list[uuid.UUID]:
    """Undo a pause, and make sure a doorbell still exists.

    Resume is not just clearing the flag. While a timer was paused its queue
    message may have been consumed (the handler would have declined to fire
    it) or expired, so a resumed timer can be pending, unpaused, overdue and
    silent. Re-enqueueing here closes that gap immediately instead of waiting
    up to five minutes for the sweep to notice.
    """
    moment = now or dt.datetime.now(dt.UTC)
    rows = (
        await session.execute(
            text(
                "UPDATE sla_timers "
                "   SET paused_at = NULL, pause_reason = NULL, updated_at = now() "
                " WHERE case_id = :c AND status = 'pending' "
                "   AND paused_at IS NOT NULL AND deleted_at IS NULL "
                " RETURNING id, case_id, timer_type, fire_at"
            ),
            {"c": str(case_id)},
        )
    ).all()

    resumed: list[uuid.UUID] = []
    for row in rows:
        payload = json.dumps(
            {
                "timer_type": row.timer_type,
                "timer_id": str(row.id),
                "case_id": str(row.case_id),
                "fire_at": row.fire_at.astimezone(dt.UTC).isoformat(),
                "resent_by": "resume",
            }
        )
        msg_id = (
            await session.execute(
                text("SELECT pgmq.send(:q, CAST(:p AS jsonb), CAST(:d AS integer))"),
                {
                    "q": SLA_TIMER_QUEUE,
                    "p": payload,
                    "d": _delay_seconds(row.fire_at, moment),
                },
            )
        ).scalar()
        await session.execute(
            text("UPDATE sla_timers SET pgmq_msg_id = :m WHERE id = :i"),
            {"m": int(msg_id or 0), "i": str(row.id)},
        )
        resumed.append(row.id)
    return resumed
