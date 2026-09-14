"""Phase 7.5 — matching a result to a case. The wrong-patient tests. ★

Exit Gate 7's third clause is the one worth writing tests for:

> **ZERO incorrect auto-matches** — wrong-patient matching is the one failure
> mode that must be zero. **Prefer the review queue every time.**

So the starred tests below are not about accuracy. They are about what the
matcher **refuses** to do: auto-match a tie, auto-match on a name, auto-match a
result whose date is missing. Every one of them asserts the *queue*, because a
review queue entry is the correct answer to an ambiguity and a confident answer
is not.

These are pure-function tests with no database. That is deliberate — the
decision is arithmetic, so it can be tested exhaustively and quickly, and
anything that needs a session belongs in the repository layer instead.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid

from app.services.matching import (
    OUTCOME_AUTO,
    OUTCOME_REVIEW,
    OUTCOME_UNMATCHED,
    CandidateCase,
    IncomingResult,
    candidates_as_json,
    match_result,
    score_candidate,
)

DAY = dt.date(2026, 9, 14)
DOB = dt.date(1978, 3, 2)


def _case(**overrides: object) -> CandidateCase:
    base: dict[str, object] = {
        "case_id": uuid.uuid4(),
        "external_order_id": "ACC-1001",
        "mrn": "MRN-42",
        "patient_name": "Sunita Rao",
        "date_of_birth": DOB,
        "test_code": "URC",
        "loinc_code": "630-4",
        "expected_collected_at": DAY,
    }
    base.update(overrides)
    return CandidateCase(**base)  # type: ignore[arg-type]


def _incoming(**overrides: object) -> IncomingResult:
    base: dict[str, object] = {
        "external_order_id": "ACC-1001",
        "mrn": "MRN-42",
        "patient_name": "Sunita Rao",
        "date_of_birth": DOB,
        "test_code": "URC",
        "loinc_code": "630-4",
        "collected_at": DAY,
    }
    base.update(overrides)
    return IncomingResult(**base)  # type: ignore[arg-type]


# ── the signal table, as 7.5 specifies it ─────────────────────────────


def test_an_accession_number_matches_outright() -> None:
    outcome = match_result(_incoming(), [_case()])
    assert outcome.outcome == OUTCOME_AUTO
    assert outcome.score == decimal.Decimal("1.00")
    assert outcome.method == "order_id_exact"


def test_mrn_plus_test_plus_same_day_auto_matches() -> None:
    outcome = match_result(
        _incoming(external_order_id=None), [_case(external_order_id=None)]
    )
    assert outcome.outcome == OUTCOME_AUTO
    assert outcome.score == decimal.Decimal("0.90")


def test_mrn_plus_loinc_within_a_day_reaches_review_not_auto() -> None:
    """0.85 sits below the 0.90 auto threshold, and must stay there."""
    outcome = match_result(
        _incoming(external_order_id=None, test_code=None, collected_at=DAY),
        [_case(external_order_id=None, test_code=None, expected_collected_at=DAY)],
    )
    assert outcome.score == decimal.Decimal("0.85")
    assert outcome.outcome == OUTCOME_REVIEW
    assert outcome.chosen_case_id is None


def test_nothing_in_common_is_unmatched_not_a_guess() -> None:
    outcome = match_result(
        _incoming(
            external_order_id="ACC-9999",
            mrn="MRN-OTHER",
            patient_name="Someone Else",
            date_of_birth=dt.date(1990, 1, 1),
            loinc_code="9999-9",
        ),
        [_case()],
    )
    assert outcome.outcome == OUTCOME_UNMATCHED
    assert outcome.chosen_case_id is None
    assert outcome.candidates == []


# ── ★ the wrong-patient tests ─────────────────────────────────────────


def test_a_tie_is_never_broken_it_goes_to_a_person() -> None:
    """★ Exit Gate 7: zero incorrect auto-matches.

    Two cases score 0.90 — above the auto threshold. A sort would hand one of
    them the result, and would be right about half the time. Half of "which
    patient is this" is not shippable, so the tie must reach a human.
    """
    twin_a = _case(external_order_id=None)
    twin_b = _case(external_order_id=None)

    outcome = match_result(_incoming(external_order_id=None), [twin_a, twin_b])

    assert outcome.outcome == OUTCOME_REVIEW, "a tie must never auto-match"
    assert outcome.chosen_case_id is None
    assert outcome.score == decimal.Decimal("0.90")
    assert len(outcome.candidates) == 2
    assert "Only a person" in outcome.reason


def test_the_tie_rule_survives_candidate_order() -> None:
    """★ The same two cases, reversed. A tie-break by position would show here."""
    a, b = _case(external_order_id=None), _case(external_order_id=None)
    forward = match_result(_incoming(external_order_id=None), [a, b])
    backward = match_result(_incoming(external_order_id=None), [b, a])

    assert forward.outcome == backward.outcome == OUTCOME_REVIEW
    assert forward.chosen_case_id is backward.chosen_case_id is None


def test_a_name_and_a_birthday_can_never_auto_match() -> None:
    """★ A name is not an identifier.

    The strongest a name-based signal may score is 0.70 — the review floor — so
    it is arithmetically incapable of reaching the 0.90 auto threshold. Two
    siblings with the same surname and the same test on the same day must reach
    a person, not a file.
    """
    outcome = match_result(
        _incoming(external_order_id=None, mrn=None, loinc_code=None),
        [
            _case(
                external_order_id=None, mrn=None, loinc_code=None, name_similarity=0.97
            )
        ],
    )
    assert outcome.score == decimal.Decimal("0.70")
    assert outcome.outcome == OUTCOME_REVIEW
    assert outcome.chosen_case_id is None


def test_signals_are_never_summed() -> None:
    """★ Two weak coincidences must not add up to a confident answer.

    A near-identical name, the same birthday, the same test and the same day
    is a lot of agreement — and still only 0.70, because none of it is an
    identifier. Summation would carry it past 0.90 without an MRN or accession
    number ever agreeing.
    """
    outcome = match_result(
        _incoming(external_order_id=None, mrn=None, loinc_code=None),
        [_case(external_order_id=None, mrn=None, loinc_code=None, name_similarity=1.0)],
    )
    assert outcome.score <= decimal.Decimal("0.70")
    assert outcome.outcome != OUTCOME_AUTO


def test_a_missing_collection_date_does_not_count_as_agreement() -> None:
    """★ Absence of evidence is not evidence.

    A report with no collection date would otherwise score against *every* open
    case for that patient, and the most recent one would win a tie-break that
    should never have run.
    """
    outcome = match_result(
        _incoming(external_order_id=None, collected_at=None),
        [_case(external_order_id=None)],
    )
    assert outcome.outcome != OUTCOME_AUTO


def test_a_name_below_the_similarity_floor_does_not_score_at_all() -> None:
    """Sharing a surname is not sharing an identity."""
    scored = score_candidate(
        _incoming(external_order_id=None, mrn=None, loinc_code=None),
        _case(external_order_id=None, mrn=None, loinc_code=None, name_similarity=0.60),
    )
    assert scored is None


def test_an_uncomputed_name_similarity_is_not_treated_as_a_match() -> None:
    """`None` means "not computed" and must not behave like a perfect score."""
    scored = score_candidate(
        _incoming(external_order_id=None, mrn=None, loinc_code=None),
        _case(external_order_id=None, mrn=None, loinc_code=None, name_similarity=None),
    )
    assert scored is None


def test_a_different_mrn_with_everything_else_identical_does_not_match() -> None:
    """★ The case a hospital actually hits: two patients, one lab, one day."""
    outcome = match_result(
        _incoming(external_order_id=None, mrn="MRN-42"),
        [_case(external_order_id=None, mrn="MRN-43")],
    )
    assert outcome.outcome != OUTCOME_AUTO
    assert outcome.chosen_case_id is None


# ── the record the decision leaves behind ─────────────────────────────


def test_every_candidate_is_recorded_not_just_the_winner() -> None:
    """7.5 requires the score, the method and the candidates — not the answer
    alone. The near-miss is the interesting row when a match turns out wrong."""
    winner = _case()
    also_ran = _case(external_order_id=None)

    outcome = match_result(_incoming(), [winner, also_ran])
    payload = candidates_as_json(outcome.candidates)

    assert len(payload) == 2
    assert {p["case_id"] for p in payload} == {
        str(winner.case_id),
        str(also_ran.case_id),
    }
    for entry in payload:
        assert entry["method"]
        assert entry["score"]
        assert "evidence" in entry


def test_the_reason_is_written_for_a_person() -> None:
    """A reviewer reads this. It must never be a method name or a stack trace."""
    outcome = match_result(
        _incoming(external_order_id="ACC-NOPE", mrn="MRN-NOPE", loinc_code="0-0"),
        [_case()],
    )
    assert outcome.reason.endswith(".")
    assert "Traceback" not in outcome.reason
    assert len(outcome.reason.split()) > 5


def test_thresholds_are_arguments_not_constants() -> None:
    """Configuration lives in tables. A hospital that wants a stricter auto
    threshold must not need a code change to get one."""
    strict = match_result(
        _incoming(external_order_id=None),
        [_case(external_order_id=None)],
        auto_threshold=decimal.Decimal("0.95"),
    )
    assert strict.outcome == OUTCOME_REVIEW, "0.90 must not clear a 0.95 bar"
