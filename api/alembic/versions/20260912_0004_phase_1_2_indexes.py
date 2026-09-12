"""phase 1.2 indexes

Revision ID: 0004_phase_1_2_indexes
Revises: 0003_core_schema_remaining
Create Date: 2026-09-12

Phase 1.2's index list, in full. Three are new, two replace plain indexes with
the partial versions the plan specifies, and one already exists and is
deliberately left alone.

**Already correct, NOT recreated:** ``ix_case_events_case_id_occurred_at`` was
created by 0003 as ``btree (case_id, occurred_at)`` -- exactly what 1.2 asks
for. Duplicating it would cost writes and buy nothing.

**Two replacements, and why they are replacements rather than additions:**

* ``ix_orders_external_order_id`` was a plain index from 0002 (1.1 calls the
  column "indexed"; 1.2 refines that to a partial). A partial index
  ``WHERE external_order_id IS NOT NULL`` still serves every ``= value``
  lookup -- equality implies NOT NULL -- so the plain index costs writes for
  no extra reads. Most orders carry no external id until the lab returns one,
  so the partial is also far smaller.
* ``ix_pending_cases_current_owner_id`` was a plain index added by 0003 on my
  own initiative; the build plan does not ask for it. 1.2 specifies the
  partial form, so the speculative one goes.

⚠️ The partial forms are narrower than what they replace. A query for *all*
cases belonging to a doctor regardless of state, or for orders where
``external_order_id IS NULL``, is no longer index-assisted. Neither is a query
the plan describes; add an index back with evidence if a later phase needs one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_phase_1_2_indexes"
down_revision: str | None = "0003_core_schema_remaining"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── orders ────────────────────────────────────────────────────
    # The discharge gate's central question: which orders on this encounter
    # are still outstanding? encounter_id first because it is the equality
    # predicate; status second.
    op.create_index(
        "ix_orders_encounter_id_status", "orders", ["encounter_id", "status"]
    )

    # Plain -> partial. See the module docstring.
    op.drop_index("ix_orders_external_order_id", table_name="orders")
    op.create_index(
        "ix_orders_external_order_id",
        "orders",
        ["external_order_id"],
        postgresql_where=sa.text("external_order_id IS NOT NULL"),
    )

    # ── pending_cases ─────────────────────────────────────────────
    # The dashboard's default ordering: worst first, then oldest.
    op.create_index(
        "ix_pending_cases_state_severity_opened_at",
        "pending_cases",
        ["state", "severity", "opened_at"],
    )

    # Plain -> partial. "My open flags" is the query that matters; a closed
    # case never appears in it.
    op.drop_index("ix_pending_cases_current_owner_id", table_name="pending_cases")
    op.create_index(
        "ix_pending_cases_current_owner_id",
        "pending_cases",
        ["current_owner_id"],
        postgresql_where=sa.text("state IN ('flagged', 'result_received')"),
    )

    # ── patients ──────────────────────────────────────────────────
    # Trigram GIN for fuzzy name search (Phase 1.5) and the similarity scoring
    # Phase 7.4 ranks candidate patient matches with. pg_trgm is installed by
    # the Phase 0 init scripts.
    op.create_index(
        "ix_patients_name_trgm",
        "patients",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_patients_name_trgm", table_name="patients")

    op.drop_index("ix_pending_cases_current_owner_id", table_name="pending_cases")
    op.create_index(
        "ix_pending_cases_current_owner_id", "pending_cases", ["current_owner_id"]
    )
    op.drop_index(
        "ix_pending_cases_state_severity_opened_at", table_name="pending_cases"
    )

    op.drop_index("ix_orders_external_order_id", table_name="orders")
    op.create_index("ix_orders_external_order_id", "orders", ["external_order_id"])
    op.drop_index("ix_orders_encounter_id_status", table_name="orders")
