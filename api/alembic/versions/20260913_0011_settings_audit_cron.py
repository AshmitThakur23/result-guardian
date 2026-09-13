"""Runtime settings and the nightly audit notarisation. Phase 5.4 / 5.5.

Two things the plan asks for that have nowhere else to live:

**The kill switch.** 5.4 wants an admin screen with *"NODE B status and kill
switch (``LLM_ENABLED``)"*. Today ``RG_LLM_ENABLED`` is an environment
variable, which means turning NODE B off is a container restart by whoever
has shell access — not something a hospital admin can do at 3am when NODE B
starts returning nonsense. CLAUDE.md is explicit: *"Configuration lives in
tables, never in code."* So ``system_settings`` holds the runtime overrides
and the environment variable becomes the default the table can override.

**The nightly verification.** 5.5: *"Nightly ``pg_cron`` job verifies the
chain and publishes the head hash."* A structural check runs in SQL from
pg_cron, because a tamper window that opens whenever the application is
stopped is not much of a control; the full content check stays in Python,
where the canonical-JSON rule is defined. See the long comment on
``rg_verify_audit_chain`` for why that split is deliberate and what each half
actually catches.

Revision ID: 0011_settings_audit_cron
Revises: 0010_auth_and_audit_log
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.types import uuid7

revision: str = "0011_settings_audit_cron"
down_revision: str | None = "0010_auth_and_audit_log"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        # UUIDv7, generated in Python by `app.db.types.uuid7` -- there is no
        # server-side default, matching every other table in this schema.
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False),
        # Stored as text and parsed by the reader. A JSONB column would be
        # tidier and would also let an admin screen write `{"a": 1}` into a
        # boolean flag; text plus an explicit value_type is narrower.
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "updated_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("key", name="uq_system_settings_key"),
        sa.CheckConstraint(
            "value_type IN ('bool', 'int', 'float', 'string')",
            name="ck_system_settings_value_type",
        ),
    )

    # The SoftDeleteMixin's index, which every other soft-deletable table
    # carries. Without it `alembic check` reports drift on every run.
    op.create_index("ix_system_settings_deleted_at", "system_settings", ["deleted_at"])

    # Seeded, not left absent. An admin screen that shows an empty list until
    # someone guesses the right key name is not a switch anyone will find in
    # an emergency.
    op.execute(sa.text("""
            INSERT INTO system_settings (id, key, value, value_type, description)
            VALUES
              (CAST(:id_enabled AS uuid), 'llm_enabled', 'true', 'bool',
               'Master kill switch for NODE B inference. Turning this off leaves '
               'every safety guarantee intact -- RULE 1: safety never depends '
               'on AI.'),
              (CAST(:id_reason AS uuid), 'llm_kill_switch_reason', '', 'string',
               'Why inference was disabled, shown to users on the status pill.')
            ON CONFLICT (key) DO NOTHING
            """).bindparams(id_enabled=str(uuid7()), id_reason=str(uuid7())))

    # ── the nightly chain verification ────────────────────────────
    #
    # **This function checks the chain's STRUCTURE, not its content**, and the
    # distinction is deliberate rather than a shortcut.
    #
    # Reproducing `row_hash` here would mean reproducing Python's
    # canonical_json byte-for-byte in SQL, and a JSONB round-trip does not:
    # Postgres orders object keys by length-then-bytes and inserts a space
    # after ':' and ',', where canonical_json sorts lexicographically with no
    # whitespace. A second implementation that is *nearly* right is worse than
    # none -- it would report tampering on every honest row and be switched
    # off within a week.
    #
    # So the responsibility is split, and both halves are real:
    #
    #   * **This SQL function** catches a deleted, inserted or reordered row
    #     (seq continuity + prev_hash linkage). It runs from pg_cron, so it
    #     keeps running when the API and the worker are stopped -- which is
    #     exactly when someone would try.
    #   * **app/services/audit.py::verify_chain** catches a *modified* row by
    #     recomputing every hash. The worker runs it nightly and the auditor
    #     can run it on demand via GET /api/audit/verify.
    #
    # Neither alone is sufficient; together they cover every way the log can
    # be attacked from inside the database.
    op.execute("""
        CREATE OR REPLACE FUNCTION rg_verify_audit_chain()
        RETURNS TABLE (
            intact boolean,
            rows_verified bigint,
            first_break_seq bigint,
            head_seq bigint,
            head_hash char(64)
        )
        LANGUAGE plpgsql
        AS $$
        DECLARE
            r record;
            expected_prev char(64) := NULL;
            expected_seq bigint := NULL;
            checked bigint := 0;
            last_seq bigint := NULL;
            last_hash char(64) := NULL;
        BEGIN
            FOR r IN SELECT * FROM audit_log ORDER BY seq LOOP
                IF expected_prev IS NULL THEN
                    expected_prev := r.prev_hash;
                    expected_seq := r.seq;
                END IF;

                IF r.seq <> expected_seq OR r.prev_hash <> expected_prev THEN
                    RETURN QUERY SELECT false, checked, r.seq, last_seq, last_hash;
                    RETURN;
                END IF;

                checked := checked + 1;
                last_seq := r.seq;
                last_hash := r.row_hash;
                expected_prev := r.row_hash;
                expected_seq := r.seq + 1;
            END LOOP;

            RETURN QUERY SELECT true, checked, NULL::bigint, last_seq, last_hash;
        END;
        $$;
        """)

    op.execute("""
        CREATE OR REPLACE FUNCTION rg_anchor_audit_chain()
        RETURNS void
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v record;
        BEGIN
            SELECT * INTO v FROM rg_verify_audit_chain();
            INSERT INTO audit_anchors
                (head_seq, head_hash, rows_verified, chain_intact, first_break_seq)
            VALUES (
                COALESCE(v.head_seq, 0),
                COALESCE(v.head_hash, repeat('0', 64)),
                v.rows_verified,
                v.intact,
                v.first_break_seq
            );

            IF NOT v.intact THEN
                -- Loud, because a silent row in a table nobody reads is not an
                -- alert. This lands in the Postgres log, which Phase 10
                -- monitors.
                RAISE WARNING 'AUDIT CHAIN BROKEN at seq %', v.first_break_seq;
            END IF;
        END;
        $$;
        """)

    # 02:30 IST = 21:00 UTC the previous day. pg_cron schedules in UTC.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                PERFORM cron.schedule(
                    'rg_anchor_audit_chain',
                    '0 21 * * *',
                    $job$SELECT rg_anchor_audit_chain();$job$
                );
            END IF;
        END $$;
        """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                PERFORM cron.unschedule('rg_anchor_audit_chain');
            END IF;
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END $$;
        """)
    op.execute("DROP FUNCTION IF EXISTS rg_anchor_audit_chain()")
    op.execute("DROP FUNCTION IF EXISTS rg_verify_audit_chain()")
    op.execute("DROP INDEX IF EXISTS ix_system_settings_deleted_at")
    op.drop_table("system_settings")
