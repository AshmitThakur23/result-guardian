"""core schema — departments, users, patients, encounters, orders, contracts

Revision ID: 0002_core_schema
Revises: 0001_baseline
Create Date: 2026-09-12

Phase 1.1, first six tables. The remaining five from the build plan's 1.1 list
(discharge_contract_revisions, pending_cases, case_events,
discharge_medications, discharge_overrides) are NOT in this revision and are
still outstanding.

Conventions applied to every table, per CLAUDE.md:
  * UUIDv7 primary keys (time-sortable -- Phase 5.7 pages on the PK)
  * TIMESTAMPTZ everywhere, stored UTC, displayed IST at the edge
  * created_at / updated_at / created_by / updated_by on every table
  * soft delete via deleted_at -- clinical rows are never hard-deleted
  * enums as text + CHECK, never PG enum types (altering those is painful and
    these value sets change per hospital)
  * NUMERIC for values, never float

Ordering note: departments.unit_head_user_id and users.department_id
reference each other, and every table's created_by/updated_by references
users. So departments and users are created without their user-facing foreign
keys, which are added once both tables exist.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "0002_core_schema"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _audit_columns(with_user_fks: bool) -> list[sa.Column]:
    """created_at / updated_at / created_by / updated_by / deleted_at.

    ``with_user_fks=False`` for departments and users, whose actor columns
    cannot carry a foreign key until the users table exists.
    """

    def actor(name: str) -> sa.Column:
        # A fresh ForeignKey per column: SQLAlchemy refuses to attach one
        # instance to two parents ("This ForeignKey already has a parent").
        args = [sa.ForeignKey("users.id", ondelete="SET NULL")] if with_user_fks else []
        return sa.Column(name, PGUUID(as_uuid=True), *args, nullable=True)

    return [
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _pk() -> sa.Column:
    return sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False)


def upgrade() -> None:
    # ── departments ───────────────────────────────────────────────
    op.create_table(
        "departments",
        _pk(),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        # FK added below, once users exists.
        sa.Column("unit_head_user_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_audit_columns(with_user_fks=False),
    )
    op.create_index("ix_departments_deleted_at", "departments", ["deleted_at"])

    # ── users ─────────────────────────────────────────────────────
    op.create_table(
        "users",
        _pk(),
        sa.Column("employee_code", sa.String(64), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone_e164", sa.String(20), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "department_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        *_audit_columns(with_user_fks=False),
        sa.CheckConstraint(
            "role IN ('doctor', 'unit_head', 'lab_tech', 'admin', 'auditor')",
            name="ck_users_role",
        ),
    )
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])

    # ── the deferred foreign keys ─────────────────────────────────
    op.create_foreign_key(
        "fk_departments_unit_head_user_id",
        "departments",
        "users",
        ["unit_head_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for table in ("departments", "users"):
        for column in ("created_by", "updated_by"):
            op.create_foreign_key(
                f"fk_{table}_{column}",
                table,
                "users",
                [column],
                ["id"],
                ondelete="SET NULL",
            )

    # ── patients ──────────────────────────────────────────────────
    op.create_table(
        "patients",
        _pk(),
        sa.Column("mrn", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("dob", sa.Date(), nullable=True),
        # No CHECK on sex: the build plan gives value sets for every other
        # enum-like column but not this one, and the coding scheme is the
        # hospital's decision. Constrain it once they confirm.
        sa.Column("sex", sa.String(32), nullable=True),
        sa.Column("phone_primary_e164", sa.String(20), nullable=True),
        sa.Column("phone_alt_e164", sa.String(20), nullable=True),
        sa.Column(
            "preferred_language",
            sa.String(2),
            nullable=False,
            server_default="en",
        ),
        sa.Column("address_line", sa.String(500), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("pincode", sa.String(12), nullable=True),
        # Without this the Phase 4 T+24h patient rung is skipped, not failed.
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sms_consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sms_consent_basis", sa.String(200), nullable=True),
        *_audit_columns(with_user_fks=True),
        sa.CheckConstraint(
            "preferred_language IN ('en', 'hi', 'pa')",
            name="ck_patients_preferred_language",
        ),
    )
    # Unique INDEX rather than a unique CONSTRAINT plus a second index:
    # a unique constraint already builds an index, so the pair was
    # redundant and left the models and the schema disagreeing.
    op.create_index("ix_patients_mrn", "patients", ["mrn"], unique=True)
    op.create_index("ix_patients_deleted_at", "patients", ["deleted_at"])

    # ── encounters ────────────────────────────────────────────────
    op.create_table(
        "encounters",
        _pk(),
        sa.Column(
            "patient_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("patients.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("encounter_no", sa.String(64), nullable=False, unique=True),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discharged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ward", sa.String(64), nullable=True),
        sa.Column("bed", sa.String(64), nullable=True),
        sa.Column(
            "attending_doctor_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "department_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        *_audit_columns(with_user_fks=True),
        # 'opd' stays in the enum deliberately -- ADR 0003 puts OPD out of
        # scope for v1 without foreclosing it, so admitting OPD later needs no
        # migration.
        sa.CheckConstraint(
            "type IN ('ipd', 'opd', 'emergency', 'daycare')",
            name="ck_encounters_type",
        ),
        # lama / transferred / deceased drive the Phase 4.6 suppression rules.
        sa.CheckConstraint(
            "status IN ('active', 'discharged', 'lama', 'transferred', 'deceased')",
            name="ck_encounters_status",
        ),
    )
    op.create_index("ix_encounters_deleted_at", "encounters", ["deleted_at"])

    # ── orders ────────────────────────────────────────────────────
    op.create_table(
        "orders",
        _pk(),
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
        sa.Column("external_order_id", sa.String(128), nullable=True),
        sa.Column("test_code", sa.String(64), nullable=False),
        sa.Column("test_name", sa.String(300), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column(
            "ordered_by_user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_collected_at", sa.DateTime(timezone=True), nullable=True),
        # NUMERIC, never float: the gate derives the suggested expected_by
        # from ordered_at + this value.
        sa.Column("expected_tat_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ordered"),
        *_audit_columns(with_user_fks=True),
        sa.CheckConstraint(
            "category IN ('lab', 'radiology', 'pathology', 'micro')",
            name="ck_orders_category",
        ),
        sa.CheckConstraint(
            "status IN ('ordered', 'collected', 'in_lab', 'preliminary', "
            "'final', 'cancelled', 'rejected')",
            name="ck_orders_status",
        ),
        sa.CheckConstraint(
            "expected_tat_hours IS NULL OR expected_tat_hours > 0",
            name="ck_orders_expected_tat_hours_positive",
        ),
    )
    op.create_index("ix_orders_external_order_id", "orders", ["external_order_id"])
    op.create_index("ix_orders_deleted_at", "orders", ["deleted_at"])

    # ── discharge_contracts ───────────────────────────────────────
    op.create_table(
        "discharge_contracts",
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
        sa.Column(
            "responsible_doctor_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("expected_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        *_audit_columns(with_user_fks=True),
        # One contract per pending order. This is the database enforcing the
        # safety property, which is what stops two concurrent discharge
        # attempts from both succeeding.
        sa.UniqueConstraint("order_id", name="uq_discharge_contracts_order_id"),
    )
    op.create_index(
        "ix_discharge_contracts_deleted_at", "discharge_contracts", ["deleted_at"]
    )


def downgrade() -> None:
    op.drop_table("discharge_contracts")
    op.drop_table("orders")
    op.drop_table("encounters")
    op.drop_table("patients")
    # Drop the circular foreign keys before the tables they tie together.
    op.drop_constraint("fk_departments_unit_head_user_id", "departments")
    for table in ("departments", "users"):
        for column in ("created_by", "updated_by"):
            op.drop_constraint(f"fk_{table}_{column}", table)
    op.drop_table("users")
    op.drop_table("departments")
