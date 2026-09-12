"""core schema remaining — revisions, pending_cases, case_events, meds, overrides

Revision ID: 0003_core_schema_remaining
Revises: 0002_core_schema
Create Date: 2026-09-12

Completes Phase 1.1: the five tables 0002 did not cover. Same conventions as
0002 -- UUIDv7 PKs, TIMESTAMPTZ, audit columns everywhere, text + CHECK rather
than PG enum types, NUMERIC for values.

Two tables deliberately have **no** ``deleted_at``:

* ``case_events`` -- it is append-only, and a soft delete is an UPDATE, which
  the trigger below refuses.
* ``discharge_contract_revisions`` -- a revision history whose rows can be
  removed is not a history.

The append-only trigger is the part that cannot be expressed in SQLAlchemy, so
it lives here. The build plan is explicit: *"Append-only. No UPDATE, no DELETE
-- enforce with a trigger, not convention."*
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "0003_core_schema_remaining"
down_revision: str | None = "0002_core_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _audit_columns(soft_delete: bool = True) -> list[sa.Column]:
    def actor(name: str) -> sa.Column:
        # A fresh ForeignKey per column -- SQLAlchemy refuses to reuse one.
        return sa.Column(
            name,
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        )

    columns = [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        actor("created_by"),
        actor("updated_by"),
    ]
    if soft_delete:
        columns.append(
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        )
    return columns


def _pk() -> sa.Column:
    return sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False)


def upgrade() -> None:
    # ── discharge_contract_revisions ──────────────────────────────
    op.create_table(
        "discharge_contract_revisions",
        _pk(),
        sa.Column(
            "contract_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("discharge_contracts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column(
            "changed_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        *_audit_columns(soft_delete=False),
    )
    op.create_index(
        "ix_discharge_contract_revisions_contract_id",
        "discharge_contract_revisions",
        ["contract_id"],
    )

    # ── pending_cases ─────────────────────────────────────────────
    op.create_table(
        "pending_cases",
        _pk(),
        sa.Column(
            "order_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "encounter_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("encounters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("patients.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Nullable: the override path creates a case with no contract.
        sa.Column(
            "contract_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("discharge_contracts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "current_owner_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "state", sa.String(20), nullable=False, server_default="awaiting_result"
        ),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("result_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flagged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        # Unconstrained on purpose: the closure reasons are enumerated in
        # Phase 5.3, not in the 1.1 schema spec. A CHECK written now would
        # block Phase 5 if that list is refined.
        sa.Column("closure_reason", sa.String(40), nullable=True),
        sa.Column("closure_note", sa.Text(), nullable=True),
        sa.Column(
            "reopened_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        *_audit_columns(),
        # One case per order -- the database stopping a duplicate intake from
        # opening a second case for the same investigation.
        sa.UniqueConstraint("order_id", name="uq_pending_cases_order_id"),
        sa.CheckConstraint(
            "state IN ('awaiting_result', 'result_received', 'classified', "
            "'flagged', 'acknowledged', 'closed', 'reopened')",
            name="ck_pending_cases_state",
        ),
        # NULL severity means "not yet classified" -- a real state before the
        # Phase 3 rule engine has run.
        sa.CheckConstraint(
            "severity IS NULL OR severity IN ('normal', 'follow_up', 'critical')",
            name="ck_pending_cases_severity",
        ),
        sa.CheckConstraint(
            "reopened_count >= 0",
            name="ck_pending_cases_reopened_count_non_negative",
        ),
    )
    op.create_index("ix_pending_cases_deleted_at", "pending_cases", ["deleted_at"])
    op.create_index(
        "ix_pending_cases_current_owner_id", "pending_cases", ["current_owner_id"]
    )

    # ── case_events ───────────────────────────────────────────────
    op.create_table(
        "case_events",
        _pk(),
        sa.Column(
            "case_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("pending_cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        # Nullable for system events -- a timer firing has no human actor.
        sa.Column(
            "actor_user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "payload",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        *_audit_columns(soft_delete=False),
    )
    op.create_index(
        "ix_case_events_case_id_occurred_at", "case_events", ["case_id", "occurred_at"]
    )

    # ── append-only enforcement ───────────────────────────────────
    # A trigger, not a convention. A case's history is medico-legal evidence
    # (Phase 10.3): it must not be editable, including by this application's
    # own code or by anyone holding the app's database role.
    #
    # The function is written generically because Phase 5.5's audit_log needs
    # exactly the same protection and should reuse it rather than duplicate it.
    op.execute("""
        CREATE OR REPLACE FUNCTION rg_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                '% is append-only: % is not permitted',
                TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$;
        """)
    op.execute("""
        CREATE TRIGGER trg_case_events_append_only
        BEFORE UPDATE OR DELETE ON case_events
        FOR EACH ROW EXECUTE FUNCTION rg_reject_mutation();
        """)

    # ── discharge_medications ─────────────────────────────────────
    op.create_table(
        "discharge_medications",
        _pk(),
        sa.Column(
            "encounter_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("encounters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("drug_name", sa.String(300), nullable=False),
        sa.Column("drug_code", sa.String(64), nullable=True),
        sa.Column("atc_code", sa.String(16), nullable=True),
        sa.Column("dose", sa.String(64), nullable=True),
        sa.Column("route", sa.String(32), nullable=True),
        sa.Column("frequency", sa.String(64), nullable=True),
        sa.Column("duration_days", sa.Numeric(5, 1), nullable=True),
        # Rule B only considers antibiotics; flagging them here saves
        # re-deriving the answer from the drug name at classification time.
        sa.Column(
            "is_antibiotic",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        *_audit_columns(),
        sa.CheckConstraint(
            "duration_days IS NULL OR duration_days > 0",
            name="ck_discharge_medications_duration_positive",
        ),
    )
    op.create_index(
        "ix_discharge_medications_encounter_id",
        "discharge_medications",
        ["encounter_id"],
    )
    op.create_index(
        "ix_discharge_medications_deleted_at",
        "discharge_medications",
        ["deleted_at"],
    )

    # ── discharge_overrides ───────────────────────────────────────
    op.create_table(
        "discharge_overrides",
        _pk(),
        sa.Column(
            "encounter_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("encounters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column(
            "overridden_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "approved_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_audit_columns(),
        # Both constraints come from the build plan's 1.3 override spec for
        # this table. They are data constraints, not workflow.
        sa.CheckConstraint(
            "reason_code IN ('patient_lama', 'transfer_out', 'deceased', "
            "'system_outage', 'clinical_urgency')",
            name="ck_discharge_overrides_reason_code",
        ),
        # A one-word excuse is not an audit trail.
        sa.CheckConstraint(
            "char_length(trim(reason_text)) >= 20",
            name="ck_discharge_overrides_reason_text_min_length",
        ),
    )
    op.create_index(
        "ix_discharge_overrides_encounter_id", "discharge_overrides", ["encounter_id"]
    )
    op.create_index(
        "ix_discharge_overrides_deleted_at", "discharge_overrides", ["deleted_at"]
    )


def downgrade() -> None:
    op.drop_table("discharge_overrides")
    op.drop_table("discharge_medications")
    op.execute("DROP TRIGGER IF EXISTS trg_case_events_append_only ON case_events;")
    op.drop_table("case_events")
    # Dropped after case_events, which is the only current user of it. Phase
    # 5.5's audit_log will recreate it if this is ever rolled back that far.
    op.execute("DROP FUNCTION IF EXISTS rg_reject_mutation();")
    op.drop_table("pending_cases")
    op.drop_table("discharge_contract_revisions")
