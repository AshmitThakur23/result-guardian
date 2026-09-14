"""Ingested documents, their pages and their spans. Phase 6.1.

    **`documents`** — id, sha256 (unique — **free deduplication**), …
    **`document_pages`** — … page_no, width_pt, height_pt, is_scanned, …
    **`document_spans`** — … char_start, char_end, bbox JSONB, text
      **Capture spans from day one. Phase 8's span verifier is impossible
      without them.**

The last line is the reason the third table exists at all. A span ties a piece
of extracted text to the rectangle it occupied on the page, so a later phase
can show a clinician *where* a claim came from. Text without coordinates is a
quotation nobody can check.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import (
    ActorMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPkMixin,
    text_enum,
)

# The plan's own status list, in order of progress. `needs_review` and
# `failed` are both terminal-until-a-human-acts; they differ in whether the
# system produced anything usable.
DOCUMENT_STATUSES = (
    "received",
    "classified",
    "extracting",
    "extracted",
    "failed",
    "needs_review",
)

# Where a document came in from. `hl7` and `fhir` are listed by the
# architecture's Step 4 ingestion layer and are Phase 9's work -- named here
# so the CHECK does not have to change when that phase arrives.
SOURCE_CHANNELS = ("upload", "watched_folder", "hl7", "fhir")

# 6.1: "multipart, **max 25 MB**".
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# 6.4: "page mean confidence < 0.70 → route to `needs_review` instead of
# guessing". A number the plan fixes, not one to tune casually: below it the
# OCR is guessing, and a guessed lab value is worse than an honest gap.
MIN_OCR_CONFIDENCE = decimal.Decimal("0.70")


class Document(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """One ingested file.

    ``sha256`` is unique, which is the whole deduplication mechanism: the same
    report uploaded twice is one row and one file on disk. The plan calls this
    *"free deduplication"* and it is free precisely because it falls out of
    content-addressing rather than needing a comparison pass.
    """

    __tablename__ = "documents"

    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Sniffed from the bytes. 6.1: "do not trust the extension".
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Relative to the configured document root, so relocating the store is a
    # config change rather than an UPDATE over every row.
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_channel: Mapped[str] = mapped_column(String(20), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 6.5's timeout and DLQ need to distinguish "still working" from "stopped
    # working an hour ago". Two timestamps and a counter do that; a status
    # alone cannot.
    processing_started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_ended_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Nullable: a document can arrive before anyone knows which order it
    # belongs to. Matching is Phase 7.5. Until then the document exists, is
    # readable and is visible in the review queue -- which is the whole point
    # of not blocking on a link we cannot yet make.
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=True,
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_cases.id", ondelete="RESTRICT"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("sha256", name="uq_documents_sha256"),
        CheckConstraint(
            text_enum("status", DOCUMENT_STATUSES), name="ck_documents_status"
        ),
        CheckConstraint(
            text_enum("source_channel", SOURCE_CHANNELS),
            name="ck_documents_source_channel",
        ),
        CheckConstraint("size_bytes > 0", name="ck_documents_size_positive"),
        CheckConstraint("length(sha256) = 64", name="ck_documents_sha256_length"),
        CheckConstraint("attempts >= 0", name="ck_documents_attempts_non_negative"),
        Index("ix_documents_status", "status"),
        Index("ix_documents_received_at", "received_at"),
        Index("ix_documents_order_id", "order_id"),
        Index(
            "ix_documents_needs_review",
            "received_at",
            postgresql_where=text(
                "status IN ('needs_review', 'failed') AND deleted_at IS NULL"
            ),
        ),
    )


class DocumentPage(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """One page. **Scanned-ness is decided per page, not per document.**

    6.2: *"Mixed documents: decide **per page**, not per document."* Real lab
    reports are routinely a digital cover sheet with a scanned annexe, and
    treating the whole file as one or the other loses half of it.
    """

    __tablename__ = "document_pages"

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    width_pt: Mapped[decimal.Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    height_pt: Mapped[decimal.Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    is_scanned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    text_layer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL on a native page: there was no OCR, so there is no confidence in
    # it. Writing 0.0 would assert "certainly wrong", which is a different
    # and false claim.
    ocr_confidence: Mapped[decimal.Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("document_id", "page_no", name="uq_document_pages_page"),
        CheckConstraint("page_no >= 1", name="ck_document_pages_page_no_positive"),
        CheckConstraint(
            "ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_document_pages_confidence_range",
        ),
        CheckConstraint(
            "is_scanned OR ocr_confidence IS NULL",
            name="ck_document_pages_native_has_no_confidence",
        ),
        Index("ix_document_pages_document_id", "document_id", "page_no"),
    )


class DocumentSpan(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """Text, and where on the page it was. Phase 6.1 ★

    *"Capture spans from day one. Phase 8's span verifier is impossible
    without them."*

    ``char_start`` / ``char_end`` are half-open offsets into the page's
    ``text_layer``, so ``text_layer[char_start:char_end] == text`` exactly.
    That identity is what a verifier checks, and it is tested.
    """

    __tablename__ = "document_spans"

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # {x0, y0, x1, y1} in PDF points, origin top-left -- PyMuPDF's convention,
    # kept rather than converted so the overlay maths has no hidden flip.
    bbox: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("page_no >= 1", name="ck_document_spans_page_no_positive"),
        CheckConstraint(
            "char_start >= 0 AND char_end > char_start",
            name="ck_document_spans_offsets_ordered",
        ),
        CheckConstraint(
            "bbox ? 'x0' AND bbox ? 'y0' AND bbox ? 'x1' AND bbox ? 'y1'",
            name="ck_document_spans_bbox_shape",
        ),
        Index(
            "ix_document_spans_document_page", "document_id", "page_no", "char_start"
        ),
    )
