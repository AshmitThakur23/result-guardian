"""The structured contents of a result. Phase 3.1.

``results`` (created in Phase 2 because 2.4's intake endpoint needed it) is the
envelope: which order, which case, what kind of report. These are what is
*inside* it, and they exist so the rule engine has something deterministic to
read instead of parsing ``raw_payload`` every time.

Four shapes, because a lab report is four different things depending on what
was ordered:

* **analytes** — numbers with reference ranges (Rule A)
* **organisms** + **sensitivities** — what grew and what kills it (Rule B)
* **narratives** — prose (Rule C)

A single report can carry more than one. The orchestrator picks rules *by
content, not by report type*, which is why these are separate tables rather
than a discriminated union: a radiology report with an incidental potassium
value has both a narrative and an analyte, and both must be classified.

``loinc_code`` and the ``source_page`` / ``source_bbox`` columns are null until
Phase 7 extracts them from a document. They are declared now because adding a
column to a table the rule engine already reads is a migration with data in
it; declaring them empty costs nothing.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
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

# Phase 3.5's sections, exactly.
NARRATIVE_SECTIONS = ("impression", "findings", "conclusion", "microscopy")

# Phase 3.4: "interpretation (S|I|R)". Susceptible, Intermediate, Resistant.
SENSITIVITY_INTERPRETATIONS = ("S", "I", "R")


class ResultAnalyte(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """One measured value on one report. Rule A's input."""

    __tablename__ = "result_analytes"

    result_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("results.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Order on the report. A panel read back out of order is a panel a
    # clinician cannot reconcile against the paper they are holding.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    test_name_raw: Mapped[str] = mapped_column(String(300), nullable=False)
    # Null until Phase 7.3 maps the lab's own name onto a standard code.
    loinc_code: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Both kept. `value_raw` is what the lab actually wrote -- "<0.01", "NEG",
    # "1.2" -- and is evidence; `value_numeric` is what Rule A can compare.
    # Censored values ("<0.01") parse into an operator plus a number, so the
    # raw string is the only place the operator survives.
    value_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # NUMERIC, never float: a binary rounding error here is a severity
    # decision, and the project's conventions forbid float for values.
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)

    unit_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit_normalized: Mapped[str | None] = mapped_column(String(64), nullable=True)

    ref_low: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ref_high: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    # Some ranges are not numeric at all ("negative", "<40"). Kept verbatim so
    # Rule A can tell "no range" from "a range it cannot use".
    ref_text: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # The lab's own H/L/HH/LL marker, when it sends one. Never trusted as the
    # decision -- it is one more input, and labs disagree about thresholds --
    # but a disagreement between it and Rule A is worth being able to see.
    abnormal_flag_from_lab: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )

    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_bbox: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint("seq >= 0", name="ck_result_analytes_seq_non_negative"),
        Index("ix_result_analytes_result_id_seq", "result_id", "seq"),
        # Phase 7.3 looks analytes up by code once it has one. Partial,
        # because every analyte carries a null loinc_code until that phase
        # lands, and indexing those nulls would cost writes and buy nothing.
        Index(
            "ix_result_analytes_loinc_code",
            "loinc_code",
            postgresql_where=text("loinc_code IS NOT NULL"),
        ),
    )


class ResultOrganism(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """What grew. Rule B's input, and the parent of its sensitivity grid."""

    __tablename__ = "result_organisms"

    result_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("results.id", ondelete="RESTRICT"),
        nullable=False,
    )
    organism_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Free text on purpose: labs report ">100,000 CFU/mL", "10^5", "scanty".
    # Rule B's contaminant check reads it, and normalising it here would throw
    # away the distinction between "scanty" and "not reported".
    colony_count: Mapped[str | None] = mapped_column(String(64), nullable=True)
    specimen_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_result_organisms_result_id", "result_id"),)


class ResultSensitivity(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """One antibiotic against one organism. The row Rule B turns on.

    ``R`` here, for a drug the patient went home on, is the CRITICAL case the
    whole product exists to catch.
    """

    __tablename__ = "result_sensitivities"

    organism_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("result_organisms.id", ondelete="RESTRICT"),
        nullable=False,
    )
    antibiotic_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Resolved through antibiotic_synonyms; null when the lab's name is not in
    # the synonym table yet, which is a gap worth being able to query for.
    antibiotic_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    interpretation: Mapped[str] = mapped_column(String(1), nullable=False)
    mic_value: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint(
            text_enum("interpretation", SENSITIVITY_INTERPRETATIONS),
            name="ck_result_sensitivities_interpretation",
        ),
        Index("ix_result_sensitivities_organism_id", "organism_id"),
    )


class ResultNarrative(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """Prose. Rule C's input.

    The offsets matter beyond tidiness: Phase 8's span verifier refuses to show
    a citation it cannot locate in the source, and these are what it locates
    against.
    """

    __tablename__ = "result_narratives"

    result_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("results.id", ondelete="RESTRICT"),
        nullable=False,
    )
    section: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_offset_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_offset_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            text_enum("section", NARRATIVE_SECTIONS),
            name="ck_result_narratives_section",
        ),
        CheckConstraint(
            "source_offset_start IS NULL OR source_offset_end IS NULL "
            "OR source_offset_end >= source_offset_start",
            name="ck_result_narratives_offsets_ordered",
        ),
        Index("ix_result_narratives_result_id", "result_id"),
    )
