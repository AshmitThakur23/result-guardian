"""Phase 7 — link every extracted analyte to the span it came from.

7.3: *"**Every field carries its `document_span_id`**."*

Without this a value is a number with no provenance. Phase 8's span verifier
needs to point a clinician at the rectangle on the page a claim came from, and
"trust me, it said 6.9" is exactly the unverifiable citation the whole
architecture is built to avoid.

Nullable, because a value extracted by the model tier genuinely has no span --
and recording that honestly is better than inventing coordinates for it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015_analyte_span_link"
down_revision: str | None = "0014_phase_7_extraction_matching"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "result_analytes",
        sa.Column("document_span_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_result_analytes_document_span",
        "result_analytes",
        "document_spans",
        ["document_span_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_result_analytes_span", "result_analytes", ["document_span_id"])
    # How the value was obtained and how sure the cascade was. 7.2: "Every
    # extracted field records `method` and `confidence`."
    op.add_column(
        "result_analytes",
        sa.Column("extraction_method", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "result_analytes",
        sa.Column("extraction_confidence", sa.Numeric(4, 3), nullable=True),
    )
    op.create_check_constraint(
        "ck_result_analytes_extraction_confidence",
        "result_analytes",
        "extraction_confidence IS NULL OR "
        "(extraction_confidence >= 0 AND extraction_confidence <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_result_analytes_extraction_confidence", "result_analytes", type_="check"
    )
    op.drop_column("result_analytes", "extraction_confidence")
    op.drop_column("result_analytes", "extraction_method")
    op.drop_index("ix_result_analytes_span", table_name="result_analytes")
    op.drop_constraint(
        "fk_result_analytes_document_span", "result_analytes", type_="foreignkey"
    )
    op.drop_column("result_analytes", "document_span_id")
