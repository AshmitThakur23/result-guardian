"""phase 2.1 sla timer model

Revision ID: 0005_sla_timers
Revises: 0004_phase_1_2_indexes
Create Date: 2026-09-12

Phase 2.1, in full and nothing beyond it:

1. ``sla_timers`` -- the table that carries timer truth.
2. ``rg_sweep_overdue_sla_timers()`` + a ``pg_cron`` job every 5 minutes --
   *"any pending timer with fire_at < now() - interval '10 min' and no live
   queue message -> re-enqueue. This closes the gap if the queue is ever
   lost."*

**Deliberately NOT here.** Creating a timer on discharge is Phase **2.2**
("Create on discharge: result_due at contract.expected_by"), along with the
fire handler, cancellation and supersession. Phase 1.3's discharge still
enqueues its own ``result_due`` wake-up directly and writes no ``sla_timers``
row; wiring the two together is 2.2's first task, and doing it here would be
implementing the next phase's work under this one's name.

A consequence worth stating plainly: until 2.2 lands, the sweep has nothing to
sweep. That is the correct state of a half-built phase, not a defect. The sweep
is verified by inserting timer rows directly in the tests.

**Reversibility.** ``downgrade()`` removes only what ``upgrade()`` added -- the
cron job, the function, then the table. It touches no Phase 1 table, no
extension and no queue. ``pgmq.q_sla_timers`` in particular is left alone: the
queue predates this migration (Phase 0 created all five queues) and Phase 1.3
depends on it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_sla_timers"
down_revision: str | None = "0004_phase_1_2_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TIMER_TYPES = (
    "result_due",
    "owner_reminder",
    "unit_head_escalation",
    "patient_notification",
    "stale_preliminary",
)
TIMER_STATUSES = ("pending", "fired", "cancelled", "superseded")

SWEEP_JOB_NAME = "rg-sla-timer-sweep"

# The sweep. Written as a function rather than inline in the cron command so it
# can be called directly from a test -- a scheduled job that can only be
# observed by waiting five minutes is a job nobody verifies.
#
# Two details that matter:
#
# * "no live queue message" is `NOT EXISTS` against pgmq.q_sla_timers, which
#   also covers pgmq_msg_id IS NULL. A message that was consumed, archived or
#   lost is gone from that table, and that is precisely the gap being closed.
# * The payload is rebuilt from the database, matching the shape Phase 1.3
#   enqueues, so a swept wake-up is indistinguishable from an original one to
#   whatever consumes it in 2.2.
SWEEP_SQL = """
CREATE OR REPLACE FUNCTION rg_sweep_overdue_sla_timers()
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    swept integer := 0;
    t record;
    new_msg_id bigint;
BEGIN
    FOR t IN
        SELECT s.id,
               s.case_id,
               s.timer_type,
               s.fire_at,
               pc.order_id,
               pc.encounter_id,
               pc.contract_id
          FROM sla_timers s
          JOIN pending_cases pc ON pc.id = s.case_id
         WHERE s.status = 'pending'
           AND s.deleted_at IS NULL
           AND s.fire_at < now() - interval '10 minutes'
           AND NOT EXISTS (
                 SELECT 1
                   FROM pgmq.q_sla_timers q
                  WHERE q.msg_id = s.pgmq_msg_id
               )
         ORDER BY s.fire_at
         FOR UPDATE OF s SKIP LOCKED
    LOOP
        -- Already overdue, so no delay: it should be visible immediately.
        SELECT pgmq.send(
                 'sla_timers',
                 jsonb_build_object(
                   'timer_type',   t.timer_type,
                   'case_id',      t.case_id::text,
                   'order_id',     t.order_id::text,
                   'encounter_id', t.encounter_id::text,
                   'contract_id',  t.contract_id::text,
                   'fire_at',      to_char(
                                     t.fire_at AT TIME ZONE 'UTC',
                                     'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'
                                   ),
                   'resent_by',    'sweep'
                 ),
                 0
               )
          INTO new_msg_id;

        UPDATE sla_timers
           SET pgmq_msg_id = new_msg_id,
               attempts    = attempts + 1,
               updated_at  = now()
         WHERE id = t.id;

        swept := swept + 1;
    END LOOP;

    RETURN swept;
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        "sla_timers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timer_type", sa.String(length=32), nullable=False),
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("pgmq_msg_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        # Phase 0.6 conventions, same as every clinical table since 1.1.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "timer_type IN (" + ", ".join(f"'{v}'" for v in TIMER_TYPES) + ")",
            name="ck_sla_timers_timer_type",
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{v}'" for v in TIMER_STATUSES) + ")",
            name="ck_sla_timers_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_sla_timers_attempts_non_negative"),
        sa.CheckConstraint(
            "(status = 'fired') = (fired_at IS NOT NULL)",
            name="ck_sla_timers_fired_at_matches_status",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["pending_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # "idempotency_key (unique)" -- the plan's own words, and the guarantee
    # that the same logical timer cannot exist twice.
    op.create_index(
        "uq_sla_timers_idempotency_key", "sla_timers", ["idempotency_key"], unique=True
    )
    # Phase 2.2 cancels every timer on a case atomically when it closes.
    op.create_index("ix_sla_timers_case_id", "sla_timers", ["case_id"])
    # The sweep's query, and later the fire scan. Partial: those reads always
    # filter on pending, and fired timers accumulate forever.
    op.create_index(
        "ix_sla_timers_pending_fire_at",
        "sla_timers",
        ["fire_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    # SoftDeleteMixin indexes deleted_at on every table that carries it.
    op.create_index("ix_sla_timers_deleted_at", "sla_timers", ["deleted_at"])

    op.execute(SWEEP_SQL)

    # Every 5 minutes, per the build plan. Unscheduled first so a re-run after
    # a partial failure does not end up with two jobs doing the same work.
    op.execute(f"""
        DO $$
        BEGIN
            PERFORM cron.unschedule('{SWEEP_JOB_NAME}');
        EXCEPTION WHEN OTHERS THEN
            NULL;  -- no such job yet, which is the normal case
        END $$;
        """)
    op.execute(
        f"SELECT cron.schedule('{SWEEP_JOB_NAME}', '*/5 * * * *', "
        "'SELECT rg_sweep_overdue_sla_timers()')"
    )


def downgrade() -> None:
    # Reverse order, and nothing outside what upgrade() created.
    op.execute(f"""
        DO $$
        BEGIN
            PERFORM cron.unschedule('{SWEEP_JOB_NAME}');
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END $$;
        """)
    op.execute("DROP FUNCTION IF EXISTS rg_sweep_overdue_sla_timers()")

    op.drop_index("ix_sla_timers_deleted_at", table_name="sla_timers")
    op.drop_index("ix_sla_timers_pending_fire_at", table_name="sla_timers")
    op.drop_index("ix_sla_timers_case_id", table_name="sla_timers")
    op.drop_index("uq_sla_timers_idempotency_key", table_name="sla_timers")
    op.drop_table("sla_timers")
