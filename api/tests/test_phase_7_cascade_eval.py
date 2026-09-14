"""Phase 7.2 and 7.7 — the extraction cascade, and measuring it.

The assertions that matter:

* the cascade **stops at the first tier that works**, so a template is never
  skipped in favour of a model;
* **NODE B absent removes a tier, not the cascade** — RULE 2 at the level of one
  document;
* the evaluator reports an undecidable gate as **undecidable**, not as a pass
  and not as a failure;
* a wrong auto-match is counted, never averaged.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.extraction.cascade import (
    TIER_GENERIC,
    TIER_HUMAN,
    TIER_LLM,
    TIER_TEMPLATE,
    ExtractedAnalyte,
    apply_template,
    extract,
    parse_generic,
)
from app.services.extraction.evaluate import (
    EvaluationReport,
    FieldScore,
    format_report,
    score_documents,
)

REPORT = (
    "CITY LAB — BIOCHEMISTRY\n"
    "Potassium             6.9         mmol/L    3.5 - 5.1\n"
    "Sodium                138         mmol/L    135 - 145\n"
    "Creatinine            1.1         mg/dL     0.6 - 1.2\n"
)


# ── 7.2 tier 2: the generic parser ────────────────────────────────────


def test_the_generic_parser_reads_a_whitespace_table() -> None:
    rows = parse_generic(REPORT)
    names = {r.test_name_raw for r in rows}
    assert {"Potassium", "Sodium", "Creatinine"} <= names

    potassium = next(r for r in rows if r.test_name_raw == "Potassium")
    assert potassium.value is not None
    assert str(potassium.value.number) == "6.9"
    assert potassium.unit_raw == "mmol/L"
    assert potassium.reference is not None
    assert str(potassium.reference.high) == "5.1"


def test_prose_is_not_mistaken_for_a_table_row() -> None:
    """★ A row with no parseable value was never a row.

    Coercing it would attach a number to the wrong analyte; skipping it costs a
    human one line in the review queue.
    """
    rows = parse_generic(
        "Comment:   the sample was   grossly haemolysed and may be unreliable\n"
    )
    assert rows == []


def test_every_extracted_field_records_method_and_confidence() -> None:
    """7.2 requires both on every field, so a value can always be traced to the
    tier that produced it."""
    for row in parse_generic(REPORT):
        assert row.method == TIER_GENERIC
        assert 0.0 < row.confidence <= 1.0


def test_a_template_scores_higher_than_a_generic_parse() -> None:
    """A recognised layout is better evidence than inferred columns, and the
    confidence has to say so or the two are indistinguishable downstream."""
    template = {
        "field_map": {
            "row_pattern": (
                r"^(?P<name>[A-Za-z]+)\s+(?P<value>[\d.]+)\s+"
                r"(?P<unit>\S+)\s+(?P<reference>[\d.]+\s*-\s*[\d.]+)\s*$"
            )
        }
    }
    templated = apply_template(template, REPORT)
    generic = parse_generic(REPORT)
    assert templated and generic
    assert templated[0].confidence > generic[0].confidence
    assert templated[0].method == TIER_TEMPLATE


def test_a_broken_template_regex_is_skipped_not_fatal() -> None:
    """An admin typed a bad regex. That must not take the cascade down."""
    assert apply_template({"field_map": {"row_pattern": "([unclosed"}}, REPORT) == []


# ── 7.2 the cascade order ─────────────────────────────────────────────


async def test_the_model_is_not_consulted_when_the_text_parses(
    session: AsyncSession,
) -> None:
    """★ *Do not skip straight to the LLM.*

    A tier that works must stop the cascade. The model here raises if called —
    the cheapest possible proof that it was not.
    """
    called = False

    async def never_called(_: str) -> list[ExtractedAnalyte]:
        nonlocal called
        called = True
        raise AssertionError("the model must not be consulted when tier 2 worked")

    result = await extract(
        session,
        text_layer=REPORT,
        report_type="biochemistry",
        llm=never_called,
    )
    assert result.tier == TIER_GENERIC
    assert called is False


async def test_node_b_absent_removes_a_tier_not_the_cascade(
    session: AsyncSession,
) -> None:
    """★ RULE 2 for one document: *throughput drops, correctness does not.*"""
    result = await extract(
        session,
        text_layer=REPORT,
        report_type="biochemistry",
        llm=None,
    )
    assert result.tier == TIER_GENERIC
    assert result.analytes


async def test_unparseable_text_with_no_model_reaches_a_human(
    session: AsyncSession,
) -> None:
    result = await extract(
        session,
        text_layer="Dear colleague, please find enclosed the enclosed.",
        report_type="biochemistry",
        llm=None,
    )
    assert result.tier == TIER_HUMAN
    assert result.needs_human
    assert result.reason and "by hand" in result.reason
    # The clerk is told *why* they are doing this, and "the AI is offline" is a
    # different situation from "the AI could not read it".
    assert "offline" in result.reason


async def test_a_failing_model_degrades_to_a_human_not_to_an_error(
    session: AsyncSession,
) -> None:
    """A model that raises is a missing tier, not a failed document."""

    async def explodes(_: str) -> list[ExtractedAnalyte]:
        raise RuntimeError("NODE B fell over mid-request")

    result = await extract(
        session,
        text_layer="Dear colleague, please find enclosed the enclosed.",
        report_type="biochemistry",
        llm=explodes,
    )
    assert result.tier == TIER_HUMAN
    assert result.needs_human


async def test_the_model_runs_only_for_what_the_earlier_tiers_missed(
    session: AsyncSession,
) -> None:
    async def model(_: str) -> list[ExtractedAnalyte]:
        return [
            ExtractedAnalyte(
                test_name_raw="Potassium",
                value=None,
                unit_raw=None,
                reference=None,
                abnormal_flag_from_lab=None,
                method=TIER_LLM,
                confidence=0.6,
            )
        ]

    result = await extract(
        session,
        text_layer="Dear colleague, please find enclosed the enclosed.",
        report_type="biochemistry",
        llm=model,
    )
    assert result.tier == TIER_LLM
    assert result.analytes[0].method == TIER_LLM


async def test_an_empty_document_goes_straight_to_a_human(
    session: AsyncSession,
) -> None:
    result = await extract(
        session,
        text_layer="   ",
        report_type="biochemistry",
    )
    assert result.tier == TIER_HUMAN
    assert result.reason and "no readable text" in result.reason


# ── 7.7 evaluation ────────────────────────────────────────────────────


def _doc(doc_id: str, **kw: object) -> dict[str, object]:
    return {"document_id": doc_id, **kw}


def test_a_perfect_run_scores_perfectly() -> None:
    rows = [{"test_name": "Potassium", "value": "6.9", "unit": "mmol/L"}]
    report = score_documents([_doc("d1", analytes=rows)], [_doc("d1", analytes=rows)])
    assert report.fields["value"].precision == 1.0
    assert report.fields["value"].recall == 1.0


def test_a_censored_value_does_not_score_as_its_bare_number() -> None:
    """★ ``<5`` and ``5`` are different results, and an evaluator that called
    them equal would score a real extraction bug as a pass."""
    report = score_documents(
        [_doc("d1", analytes=[{"test_name": "Troponin", "value": "<0.01"}])],
        [_doc("d1", analytes=[{"test_name": "Troponin", "value": "0.01"}])],
    )
    assert report.fields["value"].precision == 0.0


def test_a_dropped_document_counts_as_missed_not_skipped() -> None:
    """★ A parser that silently dropped half the corpus must not score
    perfectly on the half it kept."""
    report = score_documents(
        [
            _doc("d1", analytes=[{"test_name": "K", "value": "6.9"}]),
            _doc("d2", analytes=[{"test_name": "Na", "value": "138"}]),
        ],
        [_doc("d1", analytes=[{"test_name": "K", "value": "6.9"}])],
    )
    assert report.documents == 2
    assert report.fields["value"].false_negative >= 1
    assert report.fields["value"].recall is not None
    assert report.fields["value"].recall < 1.0


def test_a_wrong_auto_match_is_counted_never_averaged() -> None:
    """★ Exit Gate 7's third clause is a count, not a rate. One wrong-patient
    match must not hide behind a hundred correct ones."""
    report = score_documents(
        [_doc("d1", expected_case_id="case-A")],
        [_doc("d1", matched_case_id="case-B", match_outcome="auto_matched")],
    )
    assert report.wrong_auto_matches == 1
    assert report.gate_status()  # does not raise


def test_a_review_queue_entry_is_not_a_wrong_auto_match() -> None:
    """Sending an ambiguous result to a human is the correct behaviour, and
    must not be scored as the dangerous failure."""
    report = score_documents(
        [_doc("d1", expected_case_id="case-A")],
        [_doc("d1", matched_case_id=None, match_outcome="needs_review")],
    )
    assert report.wrong_auto_matches == 0


def test_the_gate_is_undecidable_below_a_hundred_documents() -> None:
    """★ "Not enough data" is a different answer from "failed", and reporting
    one as the other is how a gate gets closed on evidence that never existed.
    """
    report = score_documents([_doc("d1")], [_doc("d1")])
    status = report.gate_status()
    assert status["passed"] is None
    assert "cannot be decided" in status["reason"]


def test_a_full_corpus_with_one_wrong_match_fails_the_gate() -> None:
    expected = [_doc(f"d{i}", expected_case_id=f"c{i}") for i in range(100)]
    actual = [
        _doc(f"d{i}", matched_case_id=f"c{i}", match_outcome="auto_matched")
        for i in range(99)
    ] + [_doc("d99", matched_case_id="WRONG", match_outcome="auto_matched")]

    report = score_documents(expected, actual)
    status = report.gate_status()
    assert status["passed"] is False
    assert status["clauses"]["zero_incorrect_auto_matches"] is False


def test_the_report_prints_the_wrong_match_count_even_when_zero() -> None:
    """A reader must be able to see that it was checked, not infer it."""
    text = format_report(
        EvaluationReport(documents=5, fields={"value": FieldScore("value", 5, 0, 0)})
    )
    assert "wrong auto-matches  : 0" in text
    assert "gate: 0" in text
    assert "UNDECIDABLE" in text
