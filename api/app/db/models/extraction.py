"""Classification, extraction templates, normalisation and matching. Phase 7.

Five tables, and the last one is the one that matters:

    **`document_classifications`** — what kind of report this is, and how we
      decided (7.1)
    **`extraction_templates`** — lab-specific anchors, tried *before* any model
      (7.2)
    **`loinc_terms`** / **`test_synonyms`** — raw lab strings → a stable code
      (7.4)
    **`match_decisions`** — which pending case a result belongs to, the score
      that decided it, and who confirmed it (7.5)

## Why `match_decisions` records the score and not just the answer

7.5 is the phase's ★ and its rule is absolute:

> **AI never decides which patient a result belongs to. This is scoring
> arithmetic and thresholds. A wrong match puts a result on a stranger's file.**

So every decision stores the **score, the method and the candidates considered**,
not merely the case it chose. Six months later, "why did this result land on this
patient" has to be answerable from the database alone — and if the answer is ever
wrong, the same row is what shows whether the arithmetic or the threshold was at
fault. A table that stored only the outcome could not tell those apart.

`method` is text + CHECK rather than a PG enum, per the base conventions, and
deliberately includes `human` — a reviewer's choice is a first-class method, not
an absence of one.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from typing import Any

from sqlalchemy import (
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

# ── vocabularies ──────────────────────────────────────────────────────

#: What a document turned out to be. `unknown` is a real answer, not a failure:
#: 7.1 says an unclassified document goes to review, never to a guess.
REPORT_TYPES = (
    "biochemistry",
    "haematology",
    "microbiology",
    "radiology",
    "histopathology",
    "unknown",
)

#: How a decision was reached. `rule` and `template` are free and reproducible;
#: `llm` is the fallback 7.2 insists must come *third*; `human` is a person.
DECISION_METHODS = ("rule", "template", "llm", "human")

#: 7.5's scoring signals, named so a stored score can be read back to the rule
#: that produced it rather than being an unattributed number.
MATCH_METHODS = (
    "order_id_exact",
    "mrn_test_date",
    "mrn_loinc_date",
    "name_dob_test_date",
    "human",
    "none",
)

#: What happened to the candidate set.
MATCH_OUTCOMES = ("auto_matched", "needs_review", "unmatched", "orphan")


class DocumentClassification(UUIDPkMixin, TimestampMixin, ActorMixin, Base):
    """7.1 — what kind of report a document is.

    Rules run first: header keywords, the lab's name, section titles, and the
    presence of a sensitivity grid. The model on NODE B is a **fallback for
    unmatched documents only**, and when it is unreachable an unmatched document
    goes to ``needs_review`` — never to a guess. That is RULE 2 applied to
    classification: losing NODE B costs throughput, never correctness.
    """

    __tablename__ = "document_classifications"

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown"
    )
    #: 0.000 to 1.000. Below the configured threshold the document is reviewed.
    confidence: Mapped[decimal.Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Which rule fired, or which model answered. Human-readable on purpose.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            text_enum("report_type", REPORT_TYPES),
            name="ck_document_classifications_report_type",
        ),
        CheckConstraint(
            text_enum("method", DECISION_METHODS),
            name="ck_document_classifications_method",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_document_classifications_confidence_range",
        ),
        # One live classification per document. A re-run replaces it rather
        # than accumulating rows nobody can choose between.
        UniqueConstraint("document_id", name="uq_document_classifications_document"),
        Index("ix_document_classifications_type", "report_type"),
    )


class ExtractionTemplate(
    UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin, Base
):
    """7.2 — a lab's own layout, tried before anything expensive.

    > *Templates are boring, fast, free and reproducible. A hospital sends
    > reports from 5–10 labs; 10 templates cover 90% of volume. **Do not skip
    > straight to the LLM.***

    ``version`` exists because a lab changes its letterhead and the anchors stop
    matching. Superseding a template rather than editing it keeps old extractions
    explainable against the template that actually produced them.
    """

    __tablename__ = "extraction_templates"

    #: Free text rather than an FK: the sending lab is often only a string on a
    #: letterhead, and waiting for a `labs` table would block the cascade.
    lab_name: Mapped[str] = mapped_column(String(200), nullable=False)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: Regexes that identify the layout. All must match for the template to apply.
    anchors: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: How to read rows once the layout is recognised.
    field_map: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    __table_args__ = (
        CheckConstraint(
            text_enum("report_type", REPORT_TYPES),
            name="ck_extraction_templates_report_type",
        ),
        CheckConstraint("version >= 1", name="ck_extraction_templates_version"),
        UniqueConstraint(
            "lab_name", "report_type", "version", name="uq_extraction_templates_version"
        ),
        Index("ix_extraction_templates_lookup", "lab_name", "report_type", "is_active"),
    )


class LoincTerm(UUIDPkMixin, TimestampMixin, Base):
    """7.4 — the stable identity a lab string is resolved to.

    Only the columns this product uses. The full LOINC release is large and most
    of it is irrelevant here; importing it wholesale would make the table slower
    to search for no clinical gain.
    """

    __tablename__ = "loinc_terms"

    loinc_code: Mapped[str] = mapped_column(String(20), nullable=False)
    long_name: Mapped[str] = mapped_column(Text, nullable=False)
    short_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    component: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The unit LOINC considers canonical. Conversions live in `unit_conversions`.
    example_units: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("loinc_code", name="uq_loinc_terms_code"),
        # Trigram index: the 7.4 cascade falls back to similarity search, and
        # without this that step scans the whole table on every unmapped result.
        Index(
            "ix_loinc_terms_long_name_trgm",
            "long_name",
            postgresql_using="gin",
            postgresql_ops={"long_name": "gin_trgm_ops"},
        ),
    )


class TestSynonym(UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin, Base):
    """7.4 — ``S. Creat`` → ``2160-0``, per lab.

    > *Unmapped terms land in an admin queue; **mapping them once fixes them
    > forever**.*

    That sentence is the whole design. `lab_name` is nullable so a mapping can be
    either global or specific to one lab's spelling, and the unique constraint
    covers both shapes.
    """

    __tablename__ = "test_synonyms"

    #: Exactly as the lab printed it, before normalisation. Kept verbatim so the
    #: admin queue can show what was actually received.
    raw_text: Mapped[str] = mapped_column(String(300), nullable=False)
    #: Lowercased, punctuation-stripped, unaccented. What the cascade matches on.
    normalised_text: Mapped[str] = mapped_column(String(300), nullable=False)
    loinc_code: Mapped[str] = mapped_column(String(20), nullable=False)
    #: NULL means "any lab". A lab-specific mapping wins over a global one.
    lab_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Where it came from, so a seeded guess can be told from a human's decision.
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")

    __table_args__ = (
        UniqueConstraint(
            "normalised_text", "lab_name", name="uq_test_synonyms_normalised_lab"
        ),
        Index("ix_test_synonyms_loinc", "loinc_code"),
        Index(
            "ix_test_synonyms_normalised_trgm",
            "normalised_text",
            postgresql_using="gin",
            postgresql_ops={"normalised_text": "gin_trgm_ops"},
        ),
    )


class MatchDecision(UUIDPkMixin, TimestampMixin, ActorMixin, Base):
    """7.5 ★ — which pending case a result belongs to, and why.

    > **AI never decides which patient a result belongs to.**
    > **Wrong-patient matching must be zero. Prefer the review queue every time.**

    Stores the **score, the method and every candidate considered** — not just
    the winner. Six months on, *"why did this result land on this patient"* must
    be answerable from the database alone, and if a match is ever wrong this row
    is what distinguishes bad arithmetic from a badly chosen threshold.

    ``chosen_case_id`` is nullable on purpose: ``unmatched`` and ``needs_review``
    are legitimate outcomes, and 7.5 requires that a tie between candidates goes
    to a human rather than to whichever row sorted first.
    """

    __tablename__ = "match_decisions"

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    result_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("results.id", ondelete="RESTRICT"),
        nullable=True,
    )
    chosen_case_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_cases.id", ondelete="RESTRICT"),
        nullable=True,
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The winning candidate's score. 0 when nothing scored at all.
    score: Mapped[decimal.Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    #: Every candidate and its score, so a near-miss is visible after the fact.
    candidates: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: Set when a human resolved it. Distinct from `created_by`, which is
    #: whatever process wrote the row.
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            text_enum("outcome", MATCH_OUTCOMES), name="ck_match_decisions_outcome"
        ),
        CheckConstraint(
            text_enum("method", MATCH_METHODS), name="ck_match_decisions_method"
        ),
        CheckConstraint(
            "score >= 0 AND score <= 1", name="ck_match_decisions_score_range"
        ),
        # An auto-match without a case is a contradiction, and it is exactly the
        # shape a wrong-patient bug would take. Refused by the database rather
        # than trusted to application code.
        CheckConstraint(
            "outcome <> 'auto_matched' OR chosen_case_id IS NOT NULL",
            name="ck_match_decisions_auto_requires_case",
        ),
        # A decision must describe something.
        CheckConstraint(
            "document_id IS NOT NULL OR result_id IS NOT NULL",
            name="ck_match_decisions_has_subject",
        ),
        Index("ix_match_decisions_outcome", "outcome"),
        Index("ix_match_decisions_document", "document_id"),
        Index("ix_match_decisions_case", "chosen_case_id"),
    )
