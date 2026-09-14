"""Document ingestion. Phase 6.1.

Three tables, and one sentence in the phase doc explains why the third exists:

    **Capture spans from day one. Phase 8's span verifier is impossible
    without them.**

A span is a piece of text plus the rectangle it occupied on the page. Without
it, Phase 8 can quote a report but cannot *prove* the quote came from the
report — and an unverifiable citation in a clinical system is worse than no
citation, because it looks like evidence.

**The database holds the path, never the blob.** The plan is explicit, and the
reason is operational: a 25 MB PDF in a JSONB column is a 25 MB row in every
backup, every replica and every `SELECT *` somebody writes by accident. Files
live on disk under a content-addressed name; `documents.sha256` is unique, so
the same report uploaded twice is one row and one file — *"free
deduplication"*, in the plan's words.

Revision ID: 0012_document_ingestion
Revises: 0011_settings_audit_cron
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012_document_ingestion"
down_revision: str | None = "0011_settings_audit_cron"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── documents ─────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), primary_key=True),
        # Content address. UNIQUE is the dedup mechanism: re-uploading the
        # same bytes hits this constraint and returns the existing row rather
        # than creating a second copy of a patient's report.
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=True),
        # Sniffed from the bytes, never from the extension.
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        # Where the file actually is. Relative to the configured document
        # root, so moving the store is a config change, not a data migration.
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("source_channel", sa.String(20), nullable=False),
        sa.Column("uploaded_by", sa.UUID(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        # Not in the plan's column list, added because 6.5 requires a timeout
        # and a DLQ: without these two you cannot tell "still working" from
        # "stopped working an hour ago", which is what a retry button needs.
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        # The link to the workflow. Nullable because a document can arrive
        # before anyone has said which order it belongs to -- Phase 7.5 is the
        # phase that matches them. Until then the document exists, is readable,
        # and is visible in the review queue.
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("case_id", sa.UUID(), nullable=True),
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
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        # RESTRICT, matching every other clinical FK in this schema: a
        # document is evidence and must not vanish because an order was
        # tidied away.
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["case_id"], ["pending_cases.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("sha256", name="uq_documents_sha256"),
        sa.CheckConstraint(
            "status IN ('received', 'classified', 'extracting', 'extracted', "
            "'failed', 'needs_review')",
            name="ck_documents_status",
        ),
        sa.CheckConstraint(
            "source_channel IN ('upload', 'watched_folder', 'hl7', 'fhir')",
            name="ck_documents_source_channel",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_documents_size_positive"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_documents_sha256_length"),
        sa.CheckConstraint("attempts >= 0", name="ck_documents_attempts_non_negative"),
    )
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_received_at", "documents", ["received_at"])
    op.create_index("ix_documents_order_id", "documents", ["order_id"])
    op.create_index("ix_documents_deleted_at", "documents", ["deleted_at"])
    # The review queue's own query: everything a human still has to look at,
    # oldest first. Partial, because the answer is usually a handful of rows
    # out of everything ever ingested.
    op.create_index(
        "ix_documents_needs_review",
        "documents",
        ["received_at"],
        postgresql_where=sa.text(
            "status IN ('needs_review', 'failed') AND deleted_at IS NULL"
        ),
    )

    # ── document_pages ────────────────────────────────────────────
    op.create_table(
        "document_pages",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("width_pt", sa.Numeric(10, 2), nullable=True),
        sa.Column("height_pt", sa.Numeric(10, 2), nullable=True),
        # 6.2: "Mixed documents: decide **per page**, not per document." This
        # column is that decision, and it is why it lives here and not on
        # `documents`.
        sa.Column("is_scanned", sa.Boolean(), nullable=False),
        sa.Column("text_layer", sa.Text(), nullable=True),
        # NULL for a native page -- there was no OCR, so there is no
        # confidence. 0.0 would be a lie meaning "certainly wrong".
        sa.Column("ocr_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("image_path", sa.String(500), nullable=True),
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
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("document_id", "page_no", name="uq_document_pages_page"),
        sa.CheckConstraint("page_no >= 1", name="ck_document_pages_page_no_positive"),
        sa.CheckConstraint(
            "ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_document_pages_confidence_range",
        ),
        # A native page has no OCR confidence; a scanned page that produced
        # text must have one. Catches a scanned page silently recorded as
        # native, which is how an unreadable page gets treated as empty.
        sa.CheckConstraint(
            "is_scanned OR ocr_confidence IS NULL",
            name="ck_document_pages_native_has_no_confidence",
        ),
    )
    op.create_index(
        "ix_document_pages_document_id", "document_pages", ["document_id", "page_no"]
    )
    op.create_index("ix_document_pages_deleted_at", "document_pages", ["deleted_at"])

    # ── document_spans ────────────────────────────────────────────
    op.create_table(
        "document_spans",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        # Offsets into that page's `text_layer`. Half-open [start, end), the
        # same convention Python slicing uses, so `text_layer[start:end]`
        # reproduces `text` exactly -- which the verifier checks.
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        # {x0, y0, x1, y1} in PDF points, origin top-left, matching PyMuPDF.
        sa.Column(
            "bbox", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("text", sa.Text(), nullable=False),
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
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("page_no >= 1", name="ck_document_spans_page_no_positive"),
        sa.CheckConstraint(
            "char_start >= 0 AND char_end > char_start",
            name="ck_document_spans_offsets_ordered",
        ),
        # A span with no rectangle cannot be highlighted, and a span that
        # cannot be highlighted cannot be verified.
        sa.CheckConstraint(
            "bbox ? 'x0' AND bbox ? 'y0' AND bbox ? 'x1' AND bbox ? 'y1'",
            name="ck_document_spans_bbox_shape",
        ),
    )
    op.create_index(
        "ix_document_spans_document_page",
        "document_spans",
        ["document_id", "page_no", "char_start"],
    )
    op.create_index("ix_document_spans_deleted_at", "document_spans", ["deleted_at"])


def downgrade() -> None:
    op.drop_table("document_spans")
    op.drop_table("document_pages")
    op.drop_table("documents")
