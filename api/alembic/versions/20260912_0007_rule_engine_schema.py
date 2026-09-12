"""phase 3.1 + 3.2 + 3.6 — rule engine schema

Revision ID: 0007_rule_engine_schema
Revises: 0006_phase_2_lifecycle
Create Date: 2026-09-12

Everything the deterministic rule engine reads and writes.

**3.1 — what is inside a result.** ``results`` already exists (Phase 2 created
it because 2.4's intake endpoint needed somewhere to put a payload). These are
its contents, split by shape rather than by report type: ``result_analytes``
for numbers, ``result_organisms`` + ``result_sensitivities`` for cultures,
``result_narratives`` for prose. A single report can carry more than one, which
is exactly why they are separate tables -- 3.6 picks rules *by content, not by
report type*, and a radiology report with an incidental potassium value has
both.

**3.2 — configuration, never hardcoded.** ``panic_thresholds``,
``clinical_keywords``, ``negation_patterns``, ``antibiotic_synonyms``,
``mdro_rules``, ``rule_config``, plus ``unit_conversions`` (Rule A step 1 names
a conversion table in the algorithm without listing it in 3.2's schema; it is
configuration by the same argument as the rest). *"Thresholds and delays live
in tables an admin can edit, never in code."*

**3.6 — ``classifications``**, with a mandatory ``engine_version`` so a
six-month-old decision can still be explained.

⚠️ **This migration creates no clinical data.** ``panic_thresholds`` in
particular ships empty: the build plan is explicit that it must be seeded from
*"your hospital's own critical value list, not from the internet"*. The
development seed adds obviously-fictional values, clearly marked.

Nothing here touches Phase 0, 1 or 2. ``downgrade()`` removes only these
tables, children first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_rule_engine_schema"
down_revision: str | None = "0006_phase_2_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "antibiotic_synonyms",
        sa.Column("synonym", sa.String(length=200), nullable=False),
        sa.Column("generic_name", sa.String(length=200), nullable=False),
        sa.Column("atc_code", sa.String(length=16), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("synonym", name="uq_antibiotic_synonyms_synonym"),
    )
    op.create_index(
        op.f("ix_antibiotic_synonyms_deleted_at"),
        "antibiotic_synonyms",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_antibiotic_synonyms_generic",
        "antibiotic_synonyms",
        ["generic_name"],
        unique=False,
    )
    op.create_table(
        "clinical_keywords",
        sa.Column("term", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column(
            "requires_negation_check",
            sa.Boolean(),
            server_default=sa.text("true"),
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
            "category IN ('malignancy', 'infection', 'acute', 'incidental')",
            name="ck_keyword_category",
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'follow_up')", name="ck_keyword_severity"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("term", name="uq_clinical_keywords_term"),
    )
    op.create_index(
        "ix_clinical_keywords_active",
        "clinical_keywords",
        ["active"],
        unique=False,
        postgresql_where=sa.text("active AND deleted_at IS NULL"),
    )
    op.create_index(
        op.f("ix_clinical_keywords_deleted_at"),
        "clinical_keywords",
        ["deleted_at"],
        unique=False,
    )
    op.create_table(
        "mdro_rules",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("organism_pattern", sa.String(length=300), nullable=False),
        sa.Column(
            "resistant_to_any", postgresql.JSONB(astext_type=sa.Text()), nullable=True
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_mdro_rules_code"),
    )
    op.create_index(
        op.f("ix_mdro_rules_deleted_at"), "mdro_rules", ["deleted_at"], unique=False
    )
    op.create_table(
        "negation_patterns",
        sa.Column("pattern", sa.String(length=300), nullable=False),
        sa.Column(
            "scope_words_before",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "scope_words_after",
            sa.Integer(),
            server_default=sa.text("6"),
            nullable=False,
        ),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "is_hedge", sa.Boolean(), server_default=sa.text("false"), nullable=False
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
            "scope_words_before >= 0 AND scope_words_after >= 0",
            name="ck_negation_scope_non_negative",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pattern", name="uq_negation_patterns_pattern"),
    )
    op.create_index(
        op.f("ix_negation_patterns_deleted_at"),
        "negation_patterns",
        ["deleted_at"],
        unique=False,
    )
    op.create_table(
        "panic_thresholds",
        sa.Column("test_code", sa.String(length=64), nullable=False),
        sa.Column("loinc_code", sa.String(length=32), nullable=True),
        sa.Column("sex", sa.String(length=8), server_default="any", nullable=False),
        sa.Column("age_min_years", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("age_max_years", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("critical_low", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("critical_high", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column(
            "follow_up_low_multiplier", sa.Numeric(precision=6, scale=3), nullable=True
        ),
        sa.Column(
            "follow_up_high_multiplier", sa.Numeric(precision=6, scale=3), nullable=True
        ),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("sex IN ('any', 'M', 'F')", name="ck_panic_sex"),
        sa.CheckConstraint(
            "age_min_years IS NULL OR age_max_years IS NULL"
            " OR age_min_years <= age_max_years",
            name="ck_panic_ages_ordered",
        ),
        sa.CheckConstraint(
            "critical_low IS NOT NULL OR critical_high IS NOT NULL",
            name="ck_panic_has_a_bound",
        ),
        sa.CheckConstraint(
            "critical_low IS NULL OR critical_high IS NULL"
            " OR critical_low <= critical_high",
            name="ck_panic_bounds_ordered",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_panic_effective_window_ordered",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_panic_thresholds_deleted_at"),
        "panic_thresholds",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_panic_thresholds_test_code",
        "panic_thresholds",
        ["test_code", "sex"],
        unique=False,
    )
    op.create_table(
        "rule_config",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_rule_config_key"),
    )
    op.create_index(
        op.f("ix_rule_config_deleted_at"), "rule_config", ["deleted_at"], unique=False
    )
    op.create_table(
        "unit_conversions",
        sa.Column("test_code", sa.String(length=64), nullable=True),
        sa.Column("from_unit", sa.String(length=64), nullable=False),
        sa.Column("to_unit", sa.String(length=64), nullable=False),
        sa.Column("factor", sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column(
            "offset",
            sa.Numeric(precision=20, scale=10),
            server_default=sa.text("0"),
            nullable=False,
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
        sa.CheckConstraint("factor <> 0", name="ck_unit_conversions_factor_non_zero"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # NULLS NOT DISTINCT: test_code is null for a generic conversion, and
        # under the default NULLS DISTINCT two identical generic rows would not
        # collide -- the constraint would permit the duplicate it exists to stop.
        sa.UniqueConstraint(
            "test_code",
            "from_unit",
            "to_unit",
            name="uq_unit_conversions_triple",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_unit_conversions_deleted_at"),
        "unit_conversions",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_unit_conversions_lookup",
        "unit_conversions",
        ["from_unit", "to_unit"],
        unique=False,
    )
    op.create_table(
        "classifications",
        sa.Column("result_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column(
            "rule_outputs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("engine_version", sa.String(length=32), nullable=False),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=False),
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
            "severity IN ('normal', 'follow_up', 'critical')",
            name="ck_classifications_severity",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["pending_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_id"], ["results.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "result_id", "engine_version", name="uq_classifications_result_version"
        ),
    )
    op.create_index(
        "ix_classifications_case_id", "classifications", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_classifications_deleted_at"),
        "classifications",
        ["deleted_at"],
        unique=False,
    )
    op.create_table(
        "result_analytes",
        sa.Column("result_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("test_name_raw", sa.String(length=300), nullable=False),
        sa.Column("loinc_code", sa.String(length=32), nullable=True),
        sa.Column("value_raw", sa.String(length=120), nullable=True),
        sa.Column("value_numeric", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("unit_raw", sa.String(length=64), nullable=True),
        sa.Column("unit_normalized", sa.String(length=64), nullable=True),
        sa.Column("ref_low", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("ref_high", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("ref_text", sa.String(length=200), nullable=True),
        sa.Column("abnormal_flag_from_lab", sa.String(length=16), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column(
            "source_bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=True
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
        sa.CheckConstraint("seq >= 0", name="ck_result_analytes_seq_non_negative"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_id"], ["results.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_result_analytes_deleted_at"),
        "result_analytes",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_result_analytes_loinc_code",
        "result_analytes",
        ["loinc_code"],
        unique=False,
        postgresql_where=sa.text("loinc_code IS NOT NULL"),
    )
    op.create_index(
        "ix_result_analytes_result_id_seq",
        "result_analytes",
        ["result_id", "seq"],
        unique=False,
    )
    op.create_table(
        "result_narratives",
        sa.Column("result_id", sa.UUID(), nullable=False),
        sa.Column("section", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_offset_start", sa.Integer(), nullable=True),
        sa.Column("source_offset_end", sa.Integer(), nullable=True),
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
            "section IN ('impression', 'findings', 'conclusion', 'microscopy')",
            name="ck_result_narratives_section",
        ),
        sa.CheckConstraint(
            "source_offset_start IS NULL OR source_offset_end IS NULL"
            " OR source_offset_end >= source_offset_start",
            name="ck_result_narratives_offsets_ordered",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_id"], ["results.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_result_narratives_deleted_at"),
        "result_narratives",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_result_narratives_result_id",
        "result_narratives",
        ["result_id"],
        unique=False,
    )
    op.create_table(
        "result_organisms",
        sa.Column("result_id", sa.UUID(), nullable=False),
        sa.Column("organism_name", sa.String(length=200), nullable=False),
        sa.Column("colony_count", sa.String(length=64), nullable=True),
        sa.Column("specimen_type", sa.String(length=64), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_id"], ["results.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_result_organisms_deleted_at"),
        "result_organisms",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_result_organisms_result_id", "result_organisms", ["result_id"], unique=False
    )
    op.create_table(
        "result_sensitivities",
        sa.Column("organism_id", sa.UUID(), nullable=False),
        sa.Column("antibiotic_name", sa.String(length=200), nullable=False),
        sa.Column("antibiotic_code", sa.String(length=32), nullable=True),
        sa.Column("interpretation", sa.String(length=1), nullable=False),
        sa.Column("mic_value", sa.String(length=32), nullable=True),
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
            "interpretation IN ('S', 'I', 'R')",
            name="ck_result_sensitivities_interpretation",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["organism_id"], ["result_organisms.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_result_sensitivities_deleted_at"),
        "result_sensitivities",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_result_sensitivities_organism_id",
        "result_sensitivities",
        ["organism_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_result_sensitivities_organism_id", table_name="result_sensitivities"
    )
    op.drop_index(
        op.f("ix_result_sensitivities_deleted_at"), table_name="result_sensitivities"
    )
    op.drop_table("result_sensitivities")
    op.drop_index("ix_result_organisms_result_id", table_name="result_organisms")
    op.drop_index(op.f("ix_result_organisms_deleted_at"), table_name="result_organisms")
    op.drop_table("result_organisms")
    op.drop_index("ix_result_narratives_result_id", table_name="result_narratives")
    op.drop_index(
        op.f("ix_result_narratives_deleted_at"), table_name="result_narratives"
    )
    op.drop_table("result_narratives")
    op.drop_index("ix_result_analytes_result_id_seq", table_name="result_analytes")
    op.drop_index(
        "ix_result_analytes_loinc_code",
        table_name="result_analytes",
        postgresql_where=sa.text("loinc_code IS NOT NULL"),
    )
    op.drop_index(op.f("ix_result_analytes_deleted_at"), table_name="result_analytes")
    op.drop_table("result_analytes")
    op.drop_index(op.f("ix_classifications_deleted_at"), table_name="classifications")
    op.drop_index("ix_classifications_case_id", table_name="classifications")
    op.drop_table("classifications")
    op.drop_index("ix_unit_conversions_lookup", table_name="unit_conversions")
    op.drop_index(op.f("ix_unit_conversions_deleted_at"), table_name="unit_conversions")
    op.drop_table("unit_conversions")
    op.drop_index(op.f("ix_rule_config_deleted_at"), table_name="rule_config")
    op.drop_table("rule_config")
    op.drop_index("ix_panic_thresholds_test_code", table_name="panic_thresholds")
    op.drop_index(op.f("ix_panic_thresholds_deleted_at"), table_name="panic_thresholds")
    op.drop_table("panic_thresholds")
    op.drop_index(
        op.f("ix_negation_patterns_deleted_at"), table_name="negation_patterns"
    )
    op.drop_table("negation_patterns")
    op.drop_index(op.f("ix_mdro_rules_deleted_at"), table_name="mdro_rules")
    op.drop_table("mdro_rules")
    op.drop_index(
        op.f("ix_clinical_keywords_deleted_at"), table_name="clinical_keywords"
    )
    op.drop_index(
        "ix_clinical_keywords_active",
        table_name="clinical_keywords",
        postgresql_where=sa.text("active AND deleted_at IS NULL"),
    )
    op.drop_table("clinical_keywords")
    op.drop_index("ix_antibiotic_synonyms_generic", table_name="antibiotic_synonyms")
    op.drop_index(
        op.f("ix_antibiotic_synonyms_deleted_at"), table_name="antibiotic_synonyms"
    )
    op.drop_table("antibiotic_synonyms")
