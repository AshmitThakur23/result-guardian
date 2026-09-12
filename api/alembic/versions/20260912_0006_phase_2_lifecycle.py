"""phase 2.2-2.4 lifecycle schema

Revision ID: 0006_phase_2_lifecycle
Revises: 0005_sla_timers
Create Date: 2026-09-12

Three additions, each demanded by a specific Phase 2 task:

1. ``sla_timers.paused_at`` / ``pause_reason`` -- Phase 2.2's *"Pause
   capability for patient `deceased` / `transferred` states"*.

   The plan enumerates exactly four timer statuses
   (``pending|fired|cancelled|superseded``) and "paused" is not among them, so
   pausing is **not** a fifth status. A paused timer stays ``pending`` -- it
   still exists, still has a deadline, still appears in timer truth -- and is
   merely skipped by the fire handler and the sweep. That preserves the timer
   rather than destroying and rebuilding it, and makes resume a single
   ``UPDATE`` instead of a re-derivation that could pick a different deadline.

   Phase 4.6 owns the *policy* (who to notify instead for a deceased or
   transferred patient). Phase 2 owns only the mechanism.

2. ``lab_flags`` -- Phase 2.3, verbatim: *"id, case_id, flag_type
   (`sample_missing`, `report_delayed`, `sample_rejected`), raised_at,
   resolved_at, resolved_by, resolution_note"*.

3. ``results`` -- Phase 2.4's ``POST /api/orders/{id}/results`` has to put the
   payload somewhere.

   ⚠️ **This table is specified in Phase 3.1, not Phase 2.** It is created here
   because 2.4 cannot work without it and the plan is explicit that the
   endpoint *"stays forever"* -- an intake endpoint that persists nothing would
   make that false. The columns are 3.1's list exactly, unchanged.

   Phase 3.1's *other* result tables -- ``result_analytes``,
   ``result_organisms``, ``result_sensitivities``, ``result_narratives`` -- are
   deliberately NOT created. Those exist to be read by the rule engine, which
   is Phase 3's work; Phase 2 stores the payload and enqueues classification
   without interpreting any of it.

Also creates the ``classify`` queue that 2.4 enqueues onto. Phase 0 created
five queues before there was anything to classify; this is the sixth, and the
Phase 3 rule engine is its consumer.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_phase_2_lifecycle"
down_revision: str | None = "0005_sla_timers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LAB_FLAG_TYPES = ("sample_missing", "report_delayed", "sample_rejected")
REPORT_STATUSES = ("preliminary", "final", "amended", "corrected")
RESULT_SOURCES = ("manual", "pdf", "hl7", "fhir")

CLASSIFY_QUEUE = "classify"


# Migration 0005 created this function before `paused_at` existed, so it would
# happily re-enqueue a paused timer every five minutes -- ringing the doorbell
# for a patient who has died is precisely what the pause exists to stop.
# Replaced here rather than patched, because CREATE OR REPLACE is the only way
# to change a function body and the downgrade has to put 0005's version back.
SWEEP_SQL_WITH_PAUSE = """
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
        SELECT s.id, s.case_id, s.timer_type, s.fire_at,
               pc.order_id, pc.encounter_id, pc.contract_id
          FROM sla_timers s
          JOIN pending_cases pc ON pc.id = s.case_id
         WHERE s.status = 'pending'
           AND s.deleted_at IS NULL
           AND s.paused_at IS NULL
           AND s.fire_at < now() - interval '10 minutes'
           AND NOT EXISTS (
                 SELECT 1 FROM pgmq.q_sla_timers q WHERE q.msg_id = s.pgmq_msg_id
               )
         ORDER BY s.fire_at
         FOR UPDATE OF s SKIP LOCKED
    LOOP
        SELECT pgmq.send(
                 'sla_timers',
                 jsonb_build_object(
                   'timer_type',   t.timer_type,
                   'timer_id',     t.id::text,
                   'case_id',      t.case_id::text,
                   'order_id',     t.order_id::text,
                   'encounter_id', t.encounter_id::text,
                   'contract_id',  t.contract_id::text,
                   'fire_at',      to_char(t.fire_at AT TIME ZONE 'UTC',
                                     'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
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

# 0005's body, restored verbatim by downgrade(): no paused_at filter and no
# timer_id in the payload, exactly as it was before this migration.
SWEEP_SQL_0005 = """
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
        SELECT s.id, s.case_id, s.timer_type, s.fire_at,
               pc.order_id, pc.encounter_id, pc.contract_id
          FROM sla_timers s
          JOIN pending_cases pc ON pc.id = s.case_id
         WHERE s.status = 'pending'
           AND s.deleted_at IS NULL
           AND s.fire_at < now() - interval '10 minutes'
           AND NOT EXISTS (
                 SELECT 1 FROM pgmq.q_sla_timers q WHERE q.msg_id = s.pgmq_msg_id
               )
         ORDER BY s.fire_at
         FOR UPDATE OF s SKIP LOCKED
    LOOP
        SELECT pgmq.send(
                 'sla_timers',
                 jsonb_build_object(
                   'timer_type',   t.timer_type,
                   'case_id',      t.case_id::text,
                   'order_id',     t.order_id::text,
                   'encounter_id', t.encounter_id::text,
                   'contract_id',  t.contract_id::text,
                   'fire_at',      to_char(t.fire_at AT TIME ZONE 'UTC',
                                     'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
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
    # ── 2.2: pause capability ─────────────────────────────────────
    op.add_column(
        "sla_timers", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sla_timers", sa.Column("pause_reason", sa.String(length=32), nullable=True)
    )
    # A reason without a pause, or a pause without a reason, is a half-recorded
    # decision. Phase 4.6 reads the reason to decide who to tell instead.
    op.create_check_constraint(
        "ck_sla_timers_pause_reason_matches_paused_at",
        "sla_timers",
        "(paused_at IS NULL) = (pause_reason IS NULL)",
    )
    # The fire scan and the sweep both want "pending, not paused, due by X".
    # Replaces the 0005 index rather than adding a second one on the same
    # column: a paused timer must never be picked up, so every such read now
    # carries this predicate.
    op.drop_index("ix_sla_timers_pending_fire_at", table_name="sla_timers")
    op.create_index(
        "ix_sla_timers_pending_fire_at",
        "sla_timers",
        ["fire_at"],
        postgresql_where=sa.text("status = 'pending' AND paused_at IS NULL"),
    )

    # ── 2.3: lab_flags ────────────────────────────────────────────
    op.create_table(
        "lab_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("flag_type", sa.String(length=32), nullable=False),
        sa.Column(
            "raised_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
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
            "flag_type IN (" + ", ".join(f"'{v}'" for v in LAB_FLAG_TYPES) + ")",
            name="ck_lab_flags_flag_type",
        ),
        # Resolution is three facts that arrive together; half of them is an
        # unfinished audit trail.
        sa.CheckConstraint(
            "(resolved_at IS NULL) = (resolved_by IS NULL)",
            name="ck_lab_flags_resolution_is_complete",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["pending_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lab_flags_case_id", "lab_flags", ["case_id"])
    # SoftDeleteMixin indexes deleted_at on every table that carries it.
    op.create_index("ix_lab_flags_deleted_at", "lab_flags", ["deleted_at"])
    # 2.3's dashboard metric: "open lab flags by age". Partial, because an
    # open flag is the only kind that metric ever counts, and resolved flags
    # accumulate forever.
    op.create_index(
        "ix_lab_flags_open_raised_at",
        "lab_flags",
        ["raised_at"],
        postgresql_where=sa.text("resolved_at IS NULL AND deleted_at IS NULL"),
    )
    # One open flag of a given type per case. Without this, every 24h re-check
    # that raced with itself would raise another "sample missing" for the same
    # case and the lab's queue would fill with copies of one problem.
    op.create_index(
        "uq_lab_flags_open_per_case_type",
        "lab_flags",
        ["case_id", "flag_type"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL AND deleted_at IS NULL"),
    )

    # ── 2.4: results (schema specified in Phase 3.1) ───────────────
    op.create_table(
        "results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("report_status", sa.String(length=16), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=True),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "superseded_by_result_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
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
            "report_status IN (" + ", ".join(f"'{v}'" for v in REPORT_STATUSES) + ")",
            name="ck_results_report_status",
        ),
        sa.CheckConstraint(
            "source IN (" + ", ".join(f"'{v}'" for v in RESULT_SOURCES) + ")",
            name="ck_results_source",
        ),
        # A result cannot supersede itself; that is a cycle of length one and
        # the only one cheap to catch in a CHECK.
        sa.CheckConstraint(
            "superseded_by_result_id IS NULL OR superseded_by_result_id <> id",
            name="ck_results_not_self_superseding",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["case_id"], ["pending_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["superseded_by_result_id"], ["results.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_results_order_id", "results", ["order_id"])
    op.create_index("ix_results_deleted_at", "results", ["deleted_at"])
    op.create_index("ix_results_case_id", "results", ["case_id"])
    # Replay protection for intake: the same lab report delivered twice must
    # not become two results. Partial because source_ref is null for payloads
    # that carry no accession or document reference.
    op.create_index(
        "uq_results_source_ref",
        "results",
        ["source", "source_ref"],
        unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL AND deleted_at IS NULL"),
    )

    # ── 2.4: the queue classification is enqueued onto ────────────
    op.execute(f"SELECT pgmq.create('{CLASSIFY_QUEUE}')")

    # ── 2.2: teach the 2.1 sweep about paused timers ──────────────
    op.execute(SWEEP_SQL_WITH_PAUSE)


def downgrade() -> None:
    op.execute(SWEEP_SQL_0005)
    op.execute(f"SELECT pgmq.drop_queue('{CLASSIFY_QUEUE}')")

    op.drop_index("uq_results_source_ref", table_name="results")
    op.drop_index("ix_results_case_id", table_name="results")
    op.drop_index("ix_results_deleted_at", table_name="results")
    op.drop_index("ix_results_order_id", table_name="results")
    op.drop_table("results")

    op.drop_index("uq_lab_flags_open_per_case_type", table_name="lab_flags")
    op.drop_index("ix_lab_flags_open_raised_at", table_name="lab_flags")
    op.drop_index("ix_lab_flags_deleted_at", table_name="lab_flags")
    op.drop_index("ix_lab_flags_case_id", table_name="lab_flags")
    op.drop_table("lab_flags")

    # Put the 0005 index back exactly as it was.
    op.drop_index("ix_sla_timers_pending_fire_at", table_name="sla_timers")
    op.create_index(
        "ix_sla_timers_pending_fire_at",
        "sla_timers",
        ["fire_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_constraint(
        "ck_sla_timers_pause_reason_matches_paused_at", "sla_timers", type_="check"
    )
    op.drop_column("sla_timers", "pause_reason")
    op.drop_column("sla_timers", "paused_at")
