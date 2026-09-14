"""The knowledge base, and the record of what the model got wrong. Phase 8.

    **`kb_documents`** — only **approved** documents are retrievable.
    **`kb_chunks`** — text, span offsets, `embedding VECTOR(1024)`, `tsv`.
    **`ai_rejections`** — every citation the span verifier refused.

## Why `approved_by` is not decoration

8.1: *"**Only approved documents are retrievable.** An unapproved guideline must
not reach a doctor."* A draft SOP someone uploaded to see how it looked is
indistinguishable, at retrieval time, from the policy the hospital actually
follows — unless the database can tell them apart. So approval is a column and
the retrieval query filters on it, rather than being a process someone is
trusted to remember.

## Why `ai_rejections` exists at all

It is the **hallucination rate metric**. A system that silently drops bad
citations looks identical to one that never produces any, and those are very
different systems. Every rejection is a row, so the number is countable and can
be watched over time rather than asserted.

## The embedding column is nullable, deliberately

Vector search is one half of hybrid retrieval and it needs a model on NODE A.
Where no embedder is installed the column stays NULL, keyword search carries the
retrieval on its own, and the system degrades to *worse ranking* rather than to
*no guidance*. Making it NOT NULL would turn a missing model into a missing
feature — the exact failure THE ONE RULE forbids.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
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

#: Where a piece of guidance came from. `hospital` outranks the rest at
#: retrieval time — local policy beats a global guideline when they disagree,
#: because the local one is what the hospital is actually accountable to.
KB_PUBLISHERS = ("who", "icmr", "hospital", "nlem", "other")

#: 8.1's content list. `antibiogram` is called out separately because it is the
#: single highest-value local document and must not be filed under "other".
KB_DOC_TYPES = (
    "guideline",
    "sop",
    "antibiogram",
    "antibiotic_policy",
    "formulary",
    "protocol",
)

#: Why the span verifier refused a citation. Kept as a vocabulary so the
#: hallucination metric can be broken down rather than being one number.
REJECTION_REASONS = (
    "chunk_missing",
    "quote_not_found",
    "quote_fuzzy_below_threshold",
    "no_evidence_items",
    "malformed_response",
)

#: 1024 dimensions — bge-m3's output size. Fixed here rather than configurable
#: because changing it invalidates every stored vector, which is a migration
#: and a re-embed, not a setting.
EMBEDDING_DIMS = 1024


class KbDocument(UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin, Base):
    """A guideline, SOP or antibiogram. **Retrievable only once approved.**"""

    __tablename__ = "kb_documents"

    title: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str] = mapped_column(String(32), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    #: When this version starts and stops being the one in force. A retired
    #: version is kept, never deleted — an explanation given last March must
    #: still be readable against the guidance that was current then.
    effective_from: Mapped[dt.date | None] = mapped_column(nullable=True)
    effective_to: Mapped[dt.date | None] = mapped_column(nullable=True)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    #: ★ Both NULL means unapproved, and unapproved means invisible to
    #: retrieval. There is no "pending" state: a document is either something a
    #: doctor may be shown or it is not.
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            text_enum("publisher", KB_PUBLISHERS), name="ck_kb_documents_publisher"
        ),
        CheckConstraint(
            text_enum("doc_type", KB_DOC_TYPES), name="ck_kb_documents_doc_type"
        ),
        # Approval is one act. A row claiming an approver but no time, or a time
        # but no approver, is a half-written audit trail.
        CheckConstraint(
            "(approved_by IS NULL) = (approved_at IS NULL)",
            name="ck_kb_documents_approval_complete",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL "
            "OR effective_to >= effective_from",
            name="ck_kb_documents_effective_order",
        ),
        UniqueConstraint("sha256", "version", name="uq_kb_documents_sha_version"),
        Index("ix_kb_documents_approved", "approved_at"),
    )


class KbChunk(UUIDPkMixin, TimestampMixin, Base):
    """A retrievable passage, and the offsets that let it be quoted verifiably.

    ``char_start`` / ``char_end`` are what make 8.5 possible. A citation without
    offsets is a claim about a document; with them it is a pointer into one, and
    a clinician can be shown the passage rather than asked to trust a summary.
    """

    __tablename__ = "kb_chunks"

    kb_document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: "4.2 > Empirical therapy > Urinary tract" — kept so a citation can name
    #: where in a 200-page policy it came from.
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: NULL where no embedder is installed. Keyword retrieval then carries the
    #: search alone — degraded ranking, never absent guidance.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMS), nullable=True
    )
    #: Maintained by a trigger, so it cannot drift from `chunk_text`.
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)

    __table_args__ = (
        CheckConstraint("char_end > char_start", name="ck_kb_chunks_span_order"),
        CheckConstraint("char_start >= 0", name="ck_kb_chunks_span_nonneg"),
        Index("ix_kb_chunks_document", "kb_document_id"),
        Index("ix_kb_chunks_tsv", "tsv", postgresql_using="gin"),
        # 8.2's index, declared here as well as created in the migration so
        # `alembic check` sees model and database agree. m=16 and
        # ef_construction=64 are the phase doc's own numbers.
        Index(
            "ix_kb_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class AiRejection(UUIDPkMixin, TimestampMixin, Base):
    """A citation the span verifier refused. **The hallucination rate metric.**

    8.5: *"Log every rejection to `ai_rejections` — this is your hallucination
    rate metric. Target: zero unverified citations ever displayed."*

    The quoted text is stored verbatim, including when it was invented. That is
    the point: "the model quoted something that is not in the source" is only
    checkable later if the something is kept.
    """

    __tablename__ = "ai_rejections"

    #: The case whose explanation was being generated, when there was one.
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_cases.id", ondelete="SET NULL"),
        nullable=True,
    )
    kb_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("kb_chunks.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    #: What the model claimed the source said. Kept even when fabricated.
    quoted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Best fuzzy ratio achieved, when one was computed. Lets a threshold be
    #: tuned against real near-misses instead of guessed at.
    best_ratio: Mapped[float | None] = mapped_column(nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            text_enum("reason", REJECTION_REASONS), name="ck_ai_rejections_reason"
        ),
        Index("ix_ai_rejections_reason", "reason"),
        Index("ix_ai_rejections_created", "created_at"),
    )


#: Keeps `tsv` in step with `chunk_text`. A trigger rather than application
#: code, so a chunk written by a script, a migration or a future importer is
#: indexed the same way as one written by the API.
KB_TSV_TRIGGER = text("""
    CREATE OR REPLACE FUNCTION rg_kb_chunks_tsv() RETURNS trigger AS $$
    BEGIN
        NEW.tsv := to_tsvector('english', coalesce(NEW.chunk_text, ''));
        RETURN NEW;
    END
    $$ LANGUAGE plpgsql;
    """)
