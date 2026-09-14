"""Phase 7 end to end — a document's text through every stage, real database.

Not a unit test. Real Postgres, real trigram indexes, the real cascade and the
real matcher, so the SQL is exercised rather than mocked — the two defects this
phase has already produced were both SQL-only and invisible to a unit test.

The assertions worth reading are the ones about **failing towards a human**: an
unmapped analyte, an unclassifiable report and an unmatchable result each land
in the queue with a reason a clerk can act on, and none of them raises.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.extraction import pipeline
from app.services.extraction.cascade import TIER_GENERIC
from app.services.extraction.classify import REPORT_BIOCHEMISTRY, REPORT_UNKNOWN
from app.services.matching import OUTCOME_UNMATCHED

BIOCHEM = (
    "CITY LAB — CLINICAL CHEMISTRY\n"
    "FINAL REPORT\n"
    "Potassium             6.9         mmol/L    3.5 - 5.1\n"
    "Sodium                138         mmol/L    135 - 145\n"
    "Creatinine            1.1         mg/dL     0.6 - 1.2\n"
)


async def test_the_whole_chain_runs_against_a_real_database(
    session: AsyncSession,
) -> None:
    outcome = await pipeline.run(session, text_layer=BIOCHEM)

    # 7.1 — rules alone were enough.
    assert outcome.classification.report_type == REPORT_BIOCHEMISTRY
    # 7.2/7.3 — the generic tier read the table; no model was needed.
    assert outcome.extraction.tier == TIER_GENERIC
    assert {a.test_name_raw for a in outcome.analytes} >= {"Potassium", "Creatinine"}
    # 7.6 — the stamp was found.
    assert outcome.report_status == "final"


async def test_unmapped_analytes_name_themselves_for_the_admin(
    session: AsyncSession,
) -> None:
    """★ *Mapping them once fixes them forever* — but only if an admin can see
    **which** names need mapping. A count alone is not actionable."""
    outcome = await pipeline.run(session, text_layer=BIOCHEM)

    # `loinc_terms` is empty and no synonyms are seeded, so everything is
    # unmapped. That is the honest state of this deployment, not a bug.
    assert all(a.loinc_code is None for a in outcome.analytes)
    assert outcome.needs_review
    reason = next(r for r in outcome.reasons if "not yet mapped" in r)
    assert "Potassium" in reason


async def test_a_mapping_written_back_is_used_next_time(
    session: AsyncSession,
) -> None:
    """★ The cascade must get shorter over time, or the hospital's tenth week
    costs exactly what its first did."""
    from app.services.extraction.normalise import map_test_name, remember_mapping

    before = await map_test_name(session, "Potassium")
    assert before.loinc_code is None

    await remember_mapping(session, raw="Potassium", loinc_code="2823-3")
    await session.flush()

    after = await map_test_name(session, "Potassium")
    assert after.loinc_code == "2823-3"
    assert after.method == "exact_synonym"

    # And a spelling variant now resolves without anyone mapping it again.
    variant = await map_test_name(session, "POTASSIUM.")
    assert variant.loinc_code == "2823-3"


async def test_an_unclassifiable_report_is_not_parsed_with_the_wrong_parser(
    session: AsyncSession,
) -> None:
    """★ 7.1: below threshold → review. Never a guess."""
    outcome = await pipeline.run(
        session, text_layer="Dear colleague, thank you for the referral."
    )
    assert outcome.classification.report_type == REPORT_UNKNOWN
    assert outcome.needs_review
    assert any("kind of report" in r for r in outcome.reasons)


async def test_node_b_absent_still_produces_a_usable_outcome(
    session: AsyncSession,
) -> None:
    """★ RULE 2 at document level: throughput drops, correctness does not."""
    outcome = await pipeline.run(session, text_layer=BIOCHEM, llm=None)
    assert outcome.extraction.tier == TIER_GENERIC
    assert outcome.analytes


async def test_a_result_with_no_candidates_is_unmatched_never_filed(
    session: AsyncSession,
) -> None:
    """★ The wrong-patient guarantee, exercised through the real query."""
    outcome = await pipeline.run(
        session,
        text_layer=BIOCHEM,
        mrn="MRN-THAT-DOES-NOT-EXIST",
        patient_name="Nobody At All",
        collected_at=dt.date(2026, 9, 14),
    )
    assert outcome.match.outcome == OUTCOME_UNMATCHED
    assert outcome.match.chosen_case_id is None
    assert outcome.needs_review


async def test_candidate_loading_runs_the_real_trigram_sql(
    session: AsyncSession,
) -> None:
    """The similarity query is the part a unit test cannot reach.

    Both Phase 7 SQL defects so far were of this kind — an untyped parameter and
    a cast SQLAlchemy could not parse — and neither would have shown up without
    actually executing the statement.
    """
    by_name = await pipeline.load_candidates(
        session, mrn=None, patient_name="Sunita Rao"
    )
    by_mrn = await pipeline.load_candidates(session, mrn="MRN-77021", patient_name=None)
    neither = await pipeline.load_candidates(session, mrn=None, patient_name=None)

    assert isinstance(by_name, list)
    assert isinstance(by_mrn, list)
    assert neither == []


async def test_a_decision_must_describe_something(session: AsyncSession) -> None:
    """★ The database refuses a match decision with no subject.

    A row saying "we decided nothing, about nothing" is not an audit trail, and
    it is the shape a bug would take if the pipeline ever recorded a decision it
    had not actually made. Refused by a CHECK rather than trusted to callers.
    """
    import pytest
    from sqlalchemy.exc import IntegrityError

    outcome = await pipeline.run(
        session, text_layer=BIOCHEM, mrn="MRN-NONE", patient_name="No Such Person"
    )
    # `session.execute` runs the INSERT immediately, so the violation surfaces
    # from this call rather than from a later flush. One statement inside
    # `raises`, so the test says exactly which call it expects to fail.
    with pytest.raises(IntegrityError):
        await pipeline.record_match_decision(session, outcome.match)
    await session.rollback()


async def test_a_match_decision_is_recorded_even_when_nothing_matched(
    session: AsyncSession,
) -> None:
    """★ Recording only the ambiguous cases would leave the auto-matches — the
    ones that can actually misfile a result — with no audit trail at all."""
    document_id = (
        await session.execute(
            text(
                "INSERT INTO documents "
                "  (id, sha256, mime_type, size_bytes, source_channel, status, "
                "   received_at, storage_path, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :sha, 'application/pdf', 10, "
                "        'upload', 'extracted', now(), :path, now(), now()) "
                "RETURNING id"
            ),
            {"sha": "7" * 64, "path": "77/77/" + "7" * 64 + ".pdf"},
        )
    ).scalar_one()

    outcome = await pipeline.run(
        session, text_layer=BIOCHEM, mrn="MRN-NONE", patient_name="No Such Person"
    )
    await pipeline.record_match_decision(
        session, outcome.match, document_id=document_id
    )
    await session.flush()

    row = (
        await session.execute(
            text(
                "SELECT outcome, method, score, candidates "
                "  FROM match_decisions WHERE document_id = :d"
            ),
            {"d": document_id},
        )
    ).first()
    assert row is not None
    assert row.outcome == OUTCOME_UNMATCHED
    # Present even when empty, so a later reader can tell "nothing was in
    # contention" from "we did not record it".
    assert row.candidates is not None
