"""Phase 5.1 + 5.3 + 5.5 — auth, the closure vocabulary and the audit log.

**5.1** — ``sessions`` holds refresh tokens **hashed**, and ``users`` gains the
lockout counters the plan names ("5 failures for 15 min").

**5.3** — ``pending_cases.closure_reason`` finally gets its CHECK. Phase 2's
own docstring said the column was "deliberately still unconstrained pending
that phase"; this is that phase.

**5.5 ★** — the hash-chained ``audit_log``, plus ``audit_anchors`` for the
nightly notarisation.

The audit log is the one table here designed to be un-rewritable, and it gets
three layers because each alone is defeatable:

1. a ``BEFORE UPDATE OR DELETE`` trigger that raises;
2. ``REVOKE UPDATE, DELETE`` from the application role;
3. the hash chain itself, which no amount of database access defeats — only
   rewriting every later row *and* every exported anchor.

``rg_audit_next_seq()`` takes a transaction-scoped advisory lock so ``seq`` is
gapless and ordered. The plan asks for exactly that: *"Single writer to
guarantee seq ordering: advisory lock, or a sequence + serialisable insert."*
An advisory lock is the cheaper of the two and does not force the whole
transaction to SERIALIZABLE.

``downgrade()`` removes only what this migration added. The audit rows go with
it, which is correct for a schema rollback and is the reason a production
rollback of this migration should export the anchors first — the runbook says
so.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_auth_and_audit_log"
down_revision: str | None = "0009_scheduled_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_anchors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "anchored_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("head_seq", sa.BigInteger(), nullable=False),
        sa.Column("head_hash", sa.String(length=64), nullable=False),
        sa.Column("rows_verified", sa.BigInteger(), nullable=False),
        sa.Column("chain_intact", sa.Boolean(), nullable=False),
        sa.Column("first_break_seq", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "(chain_intact) = (first_break_seq IS NULL)",
            name="ck_audit_anchors_break_matches_intact",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_anchors_anchored_at", "audit_anchors", ["anchored_at"], unique=False
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_ip", postgresql.INET(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.Column("break_glass_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_type IN ('pending_case', 'discharge_contract', 'encounter', "
            "'user', 'duty_roster', 'user_absence', 'escalation_chain', "
            "'panic_threshold', 'clinical_keyword', 'session', 'notification', "
            "'result')",
            name="ck_audit_log_entity_type",
        ),
        sa.CheckConstraint(
            "length(prev_hash) = 64", name="ck_audit_log_prev_hash_length"
        ),
        sa.CheckConstraint(
            "length(row_hash) = 64", name="ck_audit_log_row_hash_length"
        ),
        sa.CheckConstraint("seq > 0", name="ck_audit_log_seq_positive"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("row_hash", name="uq_audit_log_row_hash"),
        sa.UniqueConstraint("seq", name="uq_audit_log_seq"),
    )
    op.create_index("ix_audit_log_actor", "audit_log", ["actor_user_id"], unique=False)
    op.create_index(
        "ix_audit_log_break_glass",
        "audit_log",
        ["occurred_at"],
        unique=False,
        postgresql_where=sa.text("break_glass_reason IS NOT NULL"),
    )
    op.create_index(
        "ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"], unique=False
    )
    op.create_index(
        "ix_audit_log_occurred_at", "audit_log", ["occurred_at"], unique=False
    )
    op.create_table(
        "sessions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=40), nullable=True),
        sa.Column("rotated_to_id", sa.UUID(), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("expires_at > issued_at", name="ck_sessions_window_ordered"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["rotated_to_id"], ["sessions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token_hash", name="uq_sessions_refresh_hash"),
    )
    op.create_index(
        op.f("ix_sessions_deleted_at"), "sessions", ["deleted_at"], unique=False
    )
    op.create_index(
        "ix_sessions_user_live",
        "sessions",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL AND deleted_at IS NULL"),
    )
    op.add_column(
        "users",
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── 5.3 · the closure vocabulary ──────────────────────────────
    # Phase 2 left this column unconstrained on purpose, pending Phase 5.
    op.create_check_constraint(
        "ck_pending_cases_closure_reason",
        "pending_cases",
        "closure_reason IS NULL OR closure_reason IN ("
        "'action_taken', 'already_handled', 'not_clinically_relevant', "
        "'duplicate_report', 'patient_uncontactable', 'auto_closed_normal')",
    )

    # ── 5.5 · gapless, ordered seq ────────────────────────────────
    # "Single writer to guarantee seq ordering: advisory lock, or a sequence +
    # serialisable insert." The advisory lock is transaction-scoped, so it is
    # released on commit or rollback without any cleanup path to forget.
    op.execute("""
        CREATE OR REPLACE FUNCTION rg_audit_next_seq()
        RETURNS bigint
        LANGUAGE plpgsql
        AS $$
        DECLARE
            next_seq bigint;
        BEGIN
            -- One writer at a time. Two concurrent appends would otherwise
            -- both read the same MAX(seq) and write the same number, and the
            -- chain would fork.
            PERFORM pg_advisory_xact_lock(23003265569481);
            SELECT COALESCE(MAX(seq), 0) + 1 INTO next_seq FROM audit_log;
            RETURN next_seq;
        END;
        $$;
        """)

    # ── 5.5 · layer 1: the trigger ────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION rg_reject_audit_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_log is append-only: % is not permitted', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$;
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_log_append_only
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION rg_reject_audit_mutation();
        """)
    # The anchors are evidence too, and evidence that can be edited is not.
    op.execute("""
        CREATE TRIGGER trg_audit_anchors_append_only
        BEFORE UPDATE OR DELETE ON audit_anchors
        FOR EACH ROW EXECUTE FUNCTION rg_reject_audit_mutation();
        """)

    # ── 5.5 · layer 2: REVOKE from the application role ───────────
    # Belt and braces with the trigger. A superuser defeats both; the hash
    # chain is what does not care about database privileges at all.
    # current_user is the role Alembic is connected as, which is the role the
    # application uses -- naming it dynamically avoids hardcoding `rg_app`
    # and failing on a hospital that renamed it.
    op.execute("""
        DO $$
        BEGIN
            EXECUTE format(
                'REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM %I',
                current_user);
            EXECUTE format(
                'REVOKE UPDATE, DELETE, TRUNCATE ON audit_anchors FROM %I',
                current_user);
        END $$;
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_anchors_append_only ON audit_anchors")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS rg_reject_audit_mutation()")
    op.execute("DROP FUNCTION IF EXISTS rg_audit_next_seq()")
    op.drop_constraint(
        "ck_pending_cases_closure_reason", "pending_cases", type_="check"
    )
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
    op.drop_index(
        "ix_sessions_user_live",
        table_name="sessions",
        postgresql_where=sa.text("revoked_at IS NULL AND deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_sessions_deleted_at"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_audit_log_occurred_at", table_name="audit_log")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_index(
        "ix_audit_log_break_glass",
        table_name="audit_log",
        postgresql_where=sa.text("break_glass_reason IS NOT NULL"),
    )
    op.drop_index("ix_audit_log_actor", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_audit_anchors_anchored_at", table_name="audit_anchors")
    op.drop_table("audit_anchors")
