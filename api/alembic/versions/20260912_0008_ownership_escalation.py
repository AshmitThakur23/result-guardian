"""Phase 4.1 + 4.3 + 4.6 — ownership, escalation and notifications.

**4.1 — availability.** ``duty_roster`` (who is on shift), ``user_absences``
(who is away and who covers) and ``escalation_chain`` (what happens per
department when nobody acknowledges). [ADR 0004] settles that the unit head
maintains the roster weekly.

**4.3 — the notification layer.** ``notifications`` records what the system
owed whom and what became of it, including the two outcomes that are not
failures: ``suppressed`` (a deliberate decision not to send) and ``queued``.

**4.6 — patient contact.** ``patient_contacts`` is the inbound half, so the
front desk can mark "patient called back".

**Phase 4.4's ladder rides on Phase 2's timers rather than a new mechanism.**
``sla_timers`` gains ``escalation_level`` and a sixth ``timer_type``,
``case_escalation``. The rung number is on the timer because the fire handler
must know which rung it is running without re-deriving it from the clock.

⚠️ ``case_escalation`` is a *new* type rather than a reuse of the existing
``owner_reminder`` / ``unit_head_escalation``: Phase 2.3 already uses those two
for the lab re-check chain ("the result has not arrived"), which is a different
concern from Phase 4's acknowledgement ladder ("the result arrived, was flagged
and nobody has looked"). Sharing a type would leave the handler unable to tell
the chains apart. Nothing in Phase 2's behaviour changes.

The default escalation ladder from 4.4's table is seeded here as global rows
(``department_id IS NULL``) so a department that has defined no chain of its
own still escalates. A hospital overrides it per department; it is
configuration, not code.

``downgrade()`` removes only what this migration added.

Revision ID: 0008_ownership_escalation
Revises: 0007_rule_engine_schema
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_ownership_escalation"
down_revision: str | None = "0007_rule_engine_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "duty_roster",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("department_id", sa.UUID(), nullable=False),
        sa.Column("shift_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shift_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("role_on_duty", sa.String(length=16), nullable=False),
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
        sa.CheckConstraint(
            "role_on_duty IN ('primary', 'backup', 'consultant')",
            name="ck_duty_roster_role",
        ),
        sa.CheckConstraint(
            "shift_end > shift_start", name="ck_duty_roster_shift_ordered"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_duty_roster_deleted_at"), "duty_roster", ["deleted_at"], unique=False
    )
    op.create_index(
        "ix_duty_roster_department_shift",
        "duty_roster",
        ["department_id", "shift_start", "shift_end"],
        unique=False,
    )
    op.create_index("ix_duty_roster_user", "duty_roster", ["user_id"], unique=False)
    op.create_table(
        "escalation_chain",
        sa.Column("department_id", sa.UUID(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("delay_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "channels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[\"in_app\"]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.String(length=16),
            server_default=sa.text("'any'"),
            nullable=False,
        ),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
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
        sa.CheckConstraint(
            "severity IN ('any', 'follow_up', 'critical')",
            name="ck_escalation_chain_severity",
        ),
        sa.CheckConstraint(
            "target_type IN ('owner', 'roster_on_duty', 'unit_head', 'admin', "
            "'patient')",
            name="ck_escalation_chain_target",
        ),
        sa.CheckConstraint(
            "delay_minutes >= 0", name="ck_escalation_chain_delay_non_negative"
        ),
        sa.CheckConstraint("level >= 0", name="ck_escalation_chain_level_non_negative"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id",
            "severity",
            "level",
            name="uq_escalation_chain_rung",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_escalation_chain_deleted_at"),
        "escalation_chain",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_escalation_chain_lookup",
        "escalation_chain",
        ["department_id", "severity", "level"],
        unique=False,
        postgresql_where=sa.text("active AND deleted_at IS NULL"),
    )
    op.create_table(
        "user_absences",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("absence_type", sa.String(length=16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delegate_user_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint(
            "absence_type IN ('leave', 'resigned', 'suspended', 'training')",
            name="ck_user_absences_type",
        ),
        sa.CheckConstraint(
            "delegate_user_id IS NULL OR delegate_user_id <> user_id",
            name="ck_user_absences_delegate_not_self",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_user_absences_window_ordered",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["delegate_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_absences_deleted_at"),
        "user_absences",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_user_absences_user_window",
        "user_absences",
        ["user_id", "starts_at", "ends_at"],
        unique=False,
    )
    op.create_table(
        "notifications",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column(
            "locale",
            sa.String(length=8),
            server_default=sa.text("'en'"),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("suppression_reason", sa.String(length=32), nullable=True),
        sa.Column("provider_msg_id", sa.String(length=200), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("escalation_level", sa.Integer(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=250), nullable=False),
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
        sa.CheckConstraint(
            "(status = 'suppressed') = (suppression_reason IS NOT NULL)",
            name="ck_notifications_suppression_reason_matches_status",
        ),
        sa.CheckConstraint(
            "(status IN ('sent', 'delivered')) = (sent_at IS NOT NULL)",
            name="ck_notifications_sent_at_matches_status",
        ),
        sa.CheckConstraint(
            "channel IN ('in_app', 'email', 'sms', 'whatsapp')",
            name="ck_notifications_channel",
        ),
        sa.CheckConstraint(
            "locale IN ('en', 'hi', 'pa')", name="ck_notifications_locale"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'delivered', 'failed', 'suppressed')",
            name="ck_notifications_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0", name="ck_notifications_attempts_non_negative"
        ),
        sa.ForeignKeyConstraint(["case_id"], ["pending_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
    )
    op.create_index(
        "ix_notifications_case_id", "notifications", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_notifications_deleted_at"),
        "notifications",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_provider_msg_id",
        "notifications",
        ["provider_msg_id"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_queued",
        "notifications",
        ["status"],
        unique=False,
        postgresql_where=sa.text("status = 'queued' AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_notifications_user_channel_sent",
        "notifications",
        ["user_id", "channel", "sent_at"],
        unique=False,
    )
    op.create_table(
        "patient_contacts",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column(
            "contacted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("recorded_by_user_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound')", name="ck_patient_contacts_direction"
        ),
        sa.ForeignKeyConstraint(["case_id"], ["pending_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_patient_contacts_case_id", "patient_contacts", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_patient_contacts_deleted_at"),
        "patient_contacts",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_patient_contacts_patient_id",
        "patient_contacts",
        ["patient_id"],
        unique=False,
    )
    op.add_column(
        "sla_timers", sa.Column("escalation_level", sa.Integer(), nullable=True)
    )

    # ── what autogenerate cannot see ──────────────────────────────
    # Alembic does not compare CHECK constraints, so the timer_type vocabulary
    # and the two new escalation_level rules are written by hand. Without the
    # first of these, creating a `case_escalation` timer is refused by the
    # database and the entire ladder is dead on arrival.
    op.drop_constraint("ck_sla_timers_timer_type", "sla_timers", type_="check")
    op.create_check_constraint(
        "ck_sla_timers_timer_type",
        "sla_timers",
        "timer_type IN ('result_due', 'owner_reminder', 'unit_head_escalation', "
        "'patient_notification', 'stale_preliminary', 'case_escalation')",
    )
    op.create_check_constraint(
        "ck_sla_timers_escalation_level_matches_type",
        "sla_timers",
        "(timer_type = 'case_escalation') = (escalation_level IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_sla_timers_escalation_level_range",
        "sla_timers",
        "escalation_level IS NULL OR escalation_level BETWEEN 0 AND 4",
    )

    # ── the default ladder, as configuration ──────────────────────
    # Phase 4.4's table, seeded as GLOBAL rows (department_id IS NULL) so a
    # department that has configured nothing still escalates rather than
    # sitting at rung 0 forever. A hospital overrides per department; the
    # values live in a table an admin can edit, never in code.
    #
    #   rung | default | critical | target       | channels
    #      0 |   0m    |    0m    | owner        | in_app + email
    #      1 |  +4h    |   +1h    | owner        | + sms
    #      2 | +12h    |   +4h    | unit head    | all
    #      3 | +24h    |   +8h    | patient      | sms
    #      4 | +48h    |  +48h    | admin        | all
    ladder = [
        # (level, target, default_delay_min, critical_delay_min, channels)
        (0, "owner", 0, 0, ["in_app", "email"]),
        (1, "owner", 240, 60, ["in_app", "email", "sms"]),
        (2, "unit_head", 720, 240, ["in_app", "email", "sms"]),
        (3, "patient", 1440, 480, ["sms"]),
        (4, "admin", 2880, 2880, ["in_app", "email", "sms"]),
    ]
    rows: list[tuple[int, str, int, str, str]] = []
    for level, target, default_min, critical_min, channels in ladder:
        encoded = json.dumps(channels)
        if level == 0:
            # Immediate for both severities, so one 'any' row says it once.
            rows.append((level, target, default_min, encoded, "any"))
            continue
        rows.append((level, target, default_min, encoded, "follow_up"))
        rows.append((level, target, critical_min, encoded, "critical"))

    insert = sa.text(
        "INSERT INTO escalation_chain "
        "(id, department_id, level, target_type, delay_minutes, channels, "
        " severity, active) "
        "VALUES (gen_random_uuid(), NULL, :lvl, :tgt, :delay, "
        "        CAST(:ch AS jsonb), :sev, true)"
    )
    for level, target, delay, encoded, severity in rows:
        op.execute(
            insert.bindparams(
                lvl=level, tgt=target, delay=delay, ch=encoded, sev=severity
            )
        )


def downgrade() -> None:
    # Restore the Phase 2 timer_type vocabulary and drop the Phase 4 rules,
    # in the reverse order of upgrade(). The column goes last because the
    # constraints reference it.
    op.drop_constraint(
        "ck_sla_timers_escalation_level_range", "sla_timers", type_="check"
    )
    op.drop_constraint(
        "ck_sla_timers_escalation_level_matches_type", "sla_timers", type_="check"
    )
    # A case_escalation timer cannot survive the vocabulary being narrowed.
    # Deleting them is correct: without Phase 4 there is no handler for them,
    # and a timer nothing can fire is worse than no timer.
    op.execute("DELETE FROM sla_timers WHERE timer_type = 'case_escalation'")
    op.drop_constraint("ck_sla_timers_timer_type", "sla_timers", type_="check")
    op.create_check_constraint(
        "ck_sla_timers_timer_type",
        "sla_timers",
        "timer_type IN ('result_due', 'owner_reminder', 'unit_head_escalation', "
        "'patient_notification', 'stale_preliminary')",
    )
    op.drop_column("sla_timers", "escalation_level")
    op.drop_index("ix_patient_contacts_patient_id", table_name="patient_contacts")
    op.drop_index(op.f("ix_patient_contacts_deleted_at"), table_name="patient_contacts")
    op.drop_index("ix_patient_contacts_case_id", table_name="patient_contacts")
    op.drop_table("patient_contacts")
    op.drop_index("ix_notifications_user_channel_sent", table_name="notifications")
    op.drop_index(
        "ix_notifications_queued",
        table_name="notifications",
        postgresql_where=sa.text("status = 'queued' AND deleted_at IS NULL"),
    )
    op.drop_index("ix_notifications_provider_msg_id", table_name="notifications")
    op.drop_index(op.f("ix_notifications_deleted_at"), table_name="notifications")
    op.drop_index("ix_notifications_case_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_user_absences_user_window", table_name="user_absences")
    op.drop_index(op.f("ix_user_absences_deleted_at"), table_name="user_absences")
    op.drop_table("user_absences")
    op.drop_index(
        "ix_escalation_chain_lookup",
        table_name="escalation_chain",
        postgresql_where=sa.text("active AND deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_escalation_chain_deleted_at"), table_name="escalation_chain")
    op.drop_table("escalation_chain")
    op.drop_index("ix_duty_roster_user", table_name="duty_roster")
    op.drop_index("ix_duty_roster_department_shift", table_name="duty_roster")
    op.drop_index(op.f("ix_duty_roster_deleted_at"), table_name="duty_roster")
    op.drop_table("duty_roster")
