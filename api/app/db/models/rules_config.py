"""Rule-engine configuration. Phase 3.2.

    Per-hospital configurability is the difference between a product and a
    demo. **Thresholds and delays live in tables an admin can edit, never in
    code.**

Everything the rule engine decides with lives here, because every one of these
values is a clinical policy a hospital owns and will change: critical
thresholds come from its own SOP, keyword lists reflect how its radiologists
write, and the antibiotic synonyms depend on which brands its pharmacy
stocks.

Hardcoding any of it turns the product back into a demo — and worse, makes a
severity decision unexplainable six months later, because the threshold that
produced it would have been a constant in a deleted line of code rather than a
row with `effective_from`, `effective_to` and a `source`.

**No AI anywhere near these.** RULE 1: Phase 3 is deterministic. Every value
here is read, compared and logged; none of it is inferred.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (
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
    true,
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

KEYWORD_CATEGORIES = ("malignancy", "infection", "acute", "incidental")
# The two severities a keyword may assert. NORMAL is not among them: a keyword
# match is never evidence that something is fine.
KEYWORD_SEVERITIES = ("critical", "follow_up")

# Sex-specific thresholds. 'any' rather than NULL so the lookup is a plain
# equality against a value the admin picked, not a three-way null dance.
THRESHOLD_SEXES = ("any", "M", "F")

# Phase 3.6 writes these; Phase 4 reads them.
SEVERITIES = ("normal", "follow_up", "critical")


class PanicThreshold(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """A hospital's own critical-value list. Rule A step 5.

    ⚠️ The build plan is emphatic: *"Seed from your hospital's own critical
    value list, not from the internet."* Nothing here ships with clinical
    values; the seed for development is explicitly marked as fiction.

    ``effective_from`` / ``effective_to`` exist so a threshold change does not
    rewrite history. A decision made last March must still be explicable using
    the threshold that was in force last March.
    """

    __tablename__ = "panic_thresholds"

    test_code: Mapped[str] = mapped_column(String(64), nullable=False)
    loinc_code: Mapped[str | None] = mapped_column(String(32), nullable=True)

    sex: Mapped[str] = mapped_column(String(8), nullable=False, server_default="any")
    age_min_years: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    age_max_years: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    critical_low: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    critical_high: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)

    # Rule A step 7: "beyond range by > slight_abnormal_factor". Per-test
    # overrides of the global default in rule_config.
    follow_up_low_multiplier: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 3), nullable=True
    )
    follow_up_high_multiplier: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 3), nullable=True
    )

    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "hospital SOP v3". Without provenance nobody can audit the number.
    source: Mapped[str] = mapped_column(String(200), nullable=False)

    effective_from: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_to: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(text_enum("sex", THRESHOLD_SEXES), name="ck_panic_sex"),
        # A threshold that constrains nothing is a configuration mistake that
        # would silently classify everything as normal.
        CheckConstraint(
            "critical_low IS NOT NULL OR critical_high IS NOT NULL",
            name="ck_panic_has_a_bound",
        ),
        CheckConstraint(
            "critical_low IS NULL OR critical_high IS NULL "
            "OR critical_low <= critical_high",
            name="ck_panic_bounds_ordered",
        ),
        CheckConstraint(
            "age_min_years IS NULL OR age_max_years IS NULL "
            "OR age_min_years <= age_max_years",
            name="ck_panic_ages_ordered",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_panic_effective_window_ordered",
        ),
        # Rule A's lookup: by test, then narrowed by sex and age.
        Index("ix_panic_thresholds_test_code", "test_code", "sex"),
    )


class ClinicalKeyword(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """A term that means something, and how much. Rule C step 2."""

    __tablename__ = "clinical_keywords"

    term: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    # "no evidence of malignancy" must not flag. Almost every term wants this;
    # it is a column rather than an assumption because a few (an explicit
    # "MRSA isolated") do not.
    requires_negation_check: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())

    __table_args__ = (
        CheckConstraint(
            text_enum("category", KEYWORD_CATEGORIES), name="ck_keyword_category"
        ),
        CheckConstraint(
            text_enum("severity", KEYWORD_SEVERITIES), name="ck_keyword_severity"
        ),
        UniqueConstraint("term", name="uq_clinical_keywords_term"),
        Index(
            "ix_clinical_keywords_active",
            "active",
            postgresql_where=text("active AND deleted_at IS NULL"),
        ),
    )


class NegationPattern(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """What makes a keyword hit mean the opposite. Rule C step 3.

    The build plan names the cases that must work: *"no evidence of"*,
    *"negative for"*, *"ruled out"*, *"cannot exclude"*, *"unlikely"*, *"r/o"*.

    ``scope_words_before`` / ``scope_words_after`` bound how far the negation
    reaches. Unbounded negation is how *"no evidence of infection. Findings
    consistent with malignancy."* ends up suppressing the malignancy.
    """

    __tablename__ = "negation_patterns"

    pattern: Mapped[str] = mapped_column(String(300), nullable=False)
    scope_words_before: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    scope_words_after: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("6")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    # "cannot exclude" is a hedge, not a denial. A hedged finding is still
    # worth a look, so it suppresses CRITICAL down to FOLLOW_UP rather than
    # discarding the hit entirely.
    is_hedge: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(
            "scope_words_before >= 0 AND scope_words_after >= 0",
            name="ck_negation_scope_non_negative",
        ),
        UniqueConstraint("pattern", name="uq_negation_patterns_pattern"),
    )


class AntibioticSynonym(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """brand → generic → ATC. Rule B step 3's first move.

    ⚠️ *"Indian brands matter: Augmentin, Monocef, Taxim, Zifi, Mox."* A
    discharge prescription says "Monocef"; the sensitivity grid says
    "Ceftriaxone". Without this table Rule B silently finds no sensitivity row
    and the resistant-drug case — the one the product exists for — is missed.
    """

    __tablename__ = "antibiotic_synonyms"

    # What appears on a prescription or a sensitivity grid, lowercased on read.
    synonym: Mapped[str] = mapped_column(String(200), nullable=False)
    generic_name: Mapped[str] = mapped_column(String(200), nullable=False)
    atc_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())

    __table_args__ = (
        UniqueConstraint("synonym", name="uq_antibiotic_synonyms_synonym"),
        Index("ix_antibiotic_synonyms_generic", "generic_name"),
    )


class MdroRule(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """Organism + resistance pattern → automatically critical. Rule B step 6.

    MRSA, ESBL, CRE. These are CRITICAL *regardless* of what the patient went
    home on, because the infection-control consequence does not depend on the
    prescription.
    """

    __tablename__ = "mdro_rules"

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    # Matched case-insensitively against organism_name; a regex rather than an
    # exact name because labs write "Staphylococcus aureus (MRSA)",
    # "MRSA", and "S. aureus - methicillin resistant".
    organism_pattern: Mapped[str] = mapped_column(String(300), nullable=False)
    # Optional second condition: resistance to any of these generics. Null
    # means the organism name alone is sufficient.
    resistant_to_any: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())

    __table_args__ = (UniqueConstraint("code", name="uq_mdro_rules_code"),)


class RuleConfig(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """Key/value tunables. The build plan names four.

    ``slight_abnormal_factor``, ``preliminary_hold_hours``,
    ``culture_contaminant_threshold``, and compressed-clock test values.

    JSONB rather than a typed column per key, because the set of keys grows
    with the rules and a migration per tunable is how tunables stop being
    tunable.
    """

    __tablename__ = "rule_config"

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("key", name="uq_rule_config_key"),)


class UnitConversion(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """Rule A step 1: *"Unit conversion to canonical unit (conversion table)"*.

    The plan names the table in the algorithm without listing it in 3.2's
    schema. It is configuration by the same argument as everything else here —
    which unit a lab reports creatinine in is a per-hospital fact — so it lives
    beside the rest rather than as a dict in code.
    """

    __tablename__ = "unit_conversions"

    # Scoped per test where it matters (mg/dL means different things for
    # glucose and creatinine in terms of SI factor). Null test_code is the
    # generic conversion.
    test_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_unit: Mapped[str] = mapped_column(String(64), nullable=False)
    to_unit: Mapped[str] = mapped_column(String(64), nullable=False)
    # value_in_to_unit = value * factor + offset. Offset carries temperature-
    # style conversions; it is 0 for everything else.
    factor: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    offset: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, server_default=text("0")
    )

    __table_args__ = (
        CheckConstraint("factor <> 0", name="ck_unit_conversions_factor_non_zero"),
        # NULLS NOT DISTINCT, deliberately. ``test_code`` is null for a generic
        # conversion, which is the *common* case -- and under PostgreSQL's
        # default NULLS DISTINCT, two rows of (NULL, 'g/L', 'g/dL') do not
        # collide, so the constraint would permit exactly the duplicate it
        # exists to prevent. Two conflicting g/L -> g/dL factors in the table
        # means Rule A picks one arbitrarily and a severity depends on row
        # order.
        UniqueConstraint(
            "test_code",
            "from_unit",
            "to_unit",
            name="uq_unit_conversions_triple",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_unit_conversions_lookup", "from_unit", "to_unit"),
    )


class Classification(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """What the engine decided, and why. Phase 3.6.

    ``engine_version`` is **mandatory**, in the plan's own words: *"You must be
    able to explain a 6-month-old decision."* Together with `rule_outputs` and
    the `effective_from`-scoped thresholds above, a classification can be
    reconstructed long after the rules have moved on.
    """

    __tablename__ = "classifications"

    result_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("results.id", ondelete="RESTRICT"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_cases.id", ondelete="RESTRICT"),
        nullable=True,
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    # Every rule that ran, its inputs and its reason_code. This is the
    # explanation, and it is written even when the answer is "normal".
    rule_outputs: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    classified_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            text_enum("severity", SEVERITIES), name="ck_classifications_severity"
        ),
        # One classification per result per engine version. A re-run under the
        # same version is a replay and must not create a second row; a re-run
        # under a NEW version is a genuinely different decision and gets one.
        UniqueConstraint(
            "result_id", "engine_version", name="uq_classifications_result_version"
        ),
        Index("ix_classifications_case_id", "case_id"),
    )
