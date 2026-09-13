"""SLA timers. Phase 2.1.

    Timers are derived from the DB, not only from the queue. pgmq carries the
    wake-up; **the table carries the truth.**

That sentence is the whole design, and it is worth being precise about why.

pgmq is a queue: a message is delivered, becomes invisible, and is eventually
deleted or archived. Ask a queue "does a timer exist for this case, and has it
fired?" and it cannot answer -- a missing message is indistinguishable from a
timer that never existed, one that already fired, and one that was lost. A
patient falling into that gap is exactly the failure this product exists to
prevent, so the answer lives in a table where it can be queried, constrained
and audited.

``pgmq_msg_id`` therefore points *outward*, from the truth to the wake-up, and
never the other way. It is nullable on purpose: a timer whose queue message has
been consumed, archived or lost is still a timer, and the Phase 2.1 sweep
exists precisely to find those and re-enqueue them.

**Scope.** Phase 2.1 is the model and the recovery sweep. Creating timers on
discharge is Phase 2.2 ("Create on discharge: result_due at
contract.expected_by"), and so is the fire handler, cancellation and
supersession. Nothing here fires, cancels or supersedes anything.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import (
    ActorMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPkMixin,
    text_enum,
)

# The build plan's list, exactly. Not one value more: a timer type that nothing
# creates is a branch nothing tests.
#
# ⚠️ ``case_escalation`` is Phase 4's addition, and it is a *sixth* type rather
# than a reuse of the three that look like they were meant for it. The reason
# is a collision that would otherwise be silent:
#
#   Phase 2.3 already uses ``owner_reminder`` for the 24-hourly **lab** re-check
#   ("the result has not arrived, chase the lab") and ``unit_head_escalation``
#   for that chain's 7-day ceiling.
#
# Phase 4.4's ladder is a different thing entirely -- the result *has* arrived,
# it was flagged, and nobody has acknowledged it. Pointing both at one timer
# type would make the fire handler unable to tell which chain a timer belongs
# to, and the handler would run the wrong one. One new type keeps Phase 2's
# behaviour untouched, which is THE ONE RULE.
TIMER_TYPES = (
    "result_due",
    "owner_reminder",
    "unit_head_escalation",
    "patient_notification",
    "stale_preliminary",
    "case_escalation",
)

# Phase 4.4's rungs. The ladder is a sequence of timers on one case, so a rung
# number is carried on the timer row -- two rungs on the same case differ by
# `fire_at`, but the handler needs to know *which* rung it is firing without
# re-deriving it from the clock.
ESCALATION_LEVELS = (0, 1, 2, 3, 4)

TIMER_STATUSES = ("pending", "fired", "cancelled", "superseded")

# Phase 2.2: "Pause capability for patient `deceased` / `transferred` states".
# Exactly those two encounter statuses, no invented third.
PAUSE_REASONS = ("deceased", "transferred")

# Phase 2.1's sweep: "any pending timer with fire_at < now() - interval '10 min'
# and no live queue message -> re-enqueue".
SWEEP_OVERDUE_GRACE = dt.timedelta(minutes=10)
SWEEP_INTERVAL_CRON = "*/5 * * * *"
SWEEP_JOB_NAME = "rg-sla-timer-sweep"
SWEEP_FUNCTION = "rg_sweep_overdue_sla_timers"


def idempotency_key(
    case_id: uuid.UUID | str,
    timer_type: str,
    fire_at: dt.datetime,
    escalation_level: int | None = None,
) -> str:
    """Canonical identity of a timer: *this case, this kind, this instant*.

    ⚠️ **Derived, not quoted.** The build plan lists ``idempotency_key
    (unique)`` and never defines its construction -- that is the one genuine
    ambiguity in Phase 2.1, and it is reported as such rather than papered
    over. This is the reading the rest of the plan forces:

    * 2.2 creates one ``result_due`` per case at ``contract.expected_by``, and
      a replayed discharge must not produce a second one.
    * 2.3 re-checks "every 24h until resolved", which is a *different* instant
      each time and so must be a different timer.

    ``(case_id, timer_type, fire_at)`` is the only triple that satisfies both.
    The timestamp is normalised to UTC and rendered to microsecond precision so
    that the same instant expressed in two timezones produces one key.

    ⚠️ **Phase 4.4 adds the rung, and it is load-bearing.** The escalation
    ladder puts several timers on one case at instants a hospital configures,
    and two rungs may legitimately fall due together -- rung 0 and rung 1 are
    both immediate if an admin sets them so, and a compressed test clock makes
    every rung simultaneous. Without the level in the key those rungs collapse
    onto one timer and the ladder silently loses its upper rungs. The level is
    appended only when present, so every Phase 2 key is byte-identical to what
    it was.

    Deliberately *not* a second UNIQUE(case_id, timer_type, fire_at) constraint
    on the table: the plan specifies one unique column, and encoding the same
    rule twice means two things to change if 2.2 refines this.
    """
    if fire_at.tzinfo is None:
        raise ValueError("fire_at must be timezone-aware")
    if timer_type not in TIMER_TYPES:
        raise ValueError(f"unknown timer_type: {timer_type}")
    stamp = fire_at.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")
    key = f"{case_id}:{timer_type}:{stamp}Z"
    if escalation_level is not None:
        key = f"{key}:rung{escalation_level}"
    return key


class SlaTimer(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """One scheduled wake-up for one pending case."""

    __tablename__ = "sla_timers"

    # RESTRICT, like every other clinical foreign key in this schema. Clinical
    # rows are never hard-deleted (cases close, they do not vanish), so a
    # cascade here would only ever fire on a mistake -- and silently taking the
    # escalation clock with it is the worst possible response to one.
    case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )

    timer_type: Mapped[str] = mapped_column(String(32), nullable=False)

    fire_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )

    # BIGINT to match pgmq.q_sla_timers.msg_id. Nullable by design: a timer
    # whose message was consumed, archived or lost still exists, and finding
    # exactly those is what the sweep is for.
    pgmq_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Incremented by the sweep each time it has to re-enqueue. A timer with a
    # climbing count is a queue that keeps losing messages -- worth seeing.
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    fired_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)

    # Phase 2.2's pause capability. NOT a fifth status: the plan enumerates
    # exactly pending|fired|cancelled|superseded, so a paused timer stays
    # `pending` -- it still exists, still has a deadline, still shows in timer
    # truth -- and is simply skipped by the fire handler and the sweep.
    # Resuming is one UPDATE rather than a re-derivation that might land on a
    # different deadline. Phase 4.6 owns the policy this serves.
    paused_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pause_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Phase 4.4. Which rung of the escalation ladder this timer is. Null for
    # every Phase 2 timer type, which is why it is nullable rather than
    # defaulted -- a `result_due` with `escalation_level = 0` would read as
    # rung zero of a ladder it is not part of.
    escalation_level: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            text_enum("timer_type", TIMER_TYPES), name="ck_sla_timers_timer_type"
        ),
        CheckConstraint(
            text_enum("status", TIMER_STATUSES), name="ck_sla_timers_status"
        ),
        CheckConstraint("attempts >= 0", name="ck_sla_timers_attempts_non_negative"),
        # fired_at and status='fired' are the same fact recorded twice, so the
        # database keeps them agreeing: a fired timer knows when it fired, and
        # nothing else claims to have fired.
        #
        # ⚠️ Derived from the column's meaning; the plan states the field list
        # but not this rule. Phase 2.2 owns the lifecycle -- if a transition it
        # needs is refused by this, the constraint is the thing to revisit.
        CheckConstraint(
            "(status = 'fired') = (fired_at IS NOT NULL)",
            name="ck_sla_timers_fired_at_matches_status",
        ),
        # The plan's one explicit index: "idempotency_key (unique)". This is
        # what makes "the same logical timer cannot be created twice" a
        # database guarantee rather than an application convention.
        Index("uq_sla_timers_idempotency_key", "idempotency_key", unique=True),
        # Phase 2.2 cancels every timer on a case atomically when it closes.
        Index("ix_sla_timers_case_id", "case_id"),
        # The sweep's exact query, and later the fire scan: pending timers by
        # deadline. Partial because every one of those reads filters on
        # status = 'pending', and fired timers accumulate forever -- the same
        # reasoning Phase 1.2 applied to its partial indexes.
        # A reason without a pause, or a pause without a reason, is a
        # half-recorded decision.
        CheckConstraint(
            "(paused_at IS NULL) = (pause_reason IS NULL)",
            name="ck_sla_timers_pause_reason_matches_paused_at",
        ),
        Index(
            "ix_sla_timers_pending_fire_at",
            "fire_at",
            postgresql_where=text("status = 'pending' AND paused_at IS NULL"),
        ),
        # Phase 4.4. A rung number belongs to a ladder timer and to nothing
        # else, and the ladder's rungs are the plan's five.
        CheckConstraint(
            "(timer_type = 'case_escalation') = (escalation_level IS NOT NULL)",
            name="ck_sla_timers_escalation_level_matches_type",
        ),
        CheckConstraint(
            "escalation_level IS NULL OR escalation_level BETWEEN 0 AND 4",
            name="ck_sla_timers_escalation_level_range",
        ),
    )
