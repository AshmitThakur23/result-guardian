"""Phase 7.1, 7.3, 7.4 and 7.6 — reading a report and deciding what it is.

The assertions worth reading are the ones about what these functions **refuse**
to do, because a misparsed clinical value is not a cosmetic defect:

* ``<0.01`` is not ``0.01`` — a censored troponin means the opposite of an exact one
* a sex-specific range is not silently resolved to the male one
* ``FINAL (AMENDED)`` is amended, not final
* a report with no status stamp is not treated as complete
* two report types scoring equally is ``unknown``, not a coin toss
"""

from __future__ import annotations

import decimal

import pytest

from app.services.extraction import status as status_svc
from app.services.extraction.classify import (
    CONFIDENCE_THRESHOLD,
    REPORT_HAEMATOLOGY,
    REPORT_MICROBIOLOGY,
    REPORT_RADIOLOGY,
    REPORT_UNKNOWN,
    classify_text,
    unknown_because_node_b_is_down,
)
from app.services.extraction.normalise import normalise_test_name
from app.services.extraction.parse import (
    STATUS_AMENDED,
    STATUS_FINAL,
    STATUS_PRELIMINARY,
    parse_reference_range,
    parse_report_status,
    parse_value,
    split_sections,
)

# ── 7.3 values ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9.2", decimal.Decimal("9.2")),
        ("  138 ", decimal.Decimal("138")),
        ("1,50,000", decimal.Decimal("150000")),
        ("150,000", decimal.Decimal("150000")),
        ("-2.5", decimal.Decimal("-2.5")),
        (".45", decimal.Decimal("0.45")),
    ],
)
def test_plain_numbers_parse(raw: str, expected: decimal.Decimal) -> None:
    parsed = parse_value(raw)
    assert parsed is not None
    assert parsed.number == expected
    assert not parsed.is_censored


@pytest.mark.parametrize(
    ("raw", "operator", "number"),
    [
        ("<0.01", "<", decimal.Decimal("0.01")),
        ("< 5", "<", decimal.Decimal("5")),
        (">1000", ">", decimal.Decimal("1000")),
        ("≥ 3.5", ">=", decimal.Decimal("3.5")),
        ("<= 2", "<=", decimal.Decimal("2")),
    ],
)
def test_a_censored_value_keeps_its_operator(
    raw: str, operator: str, number: decimal.Decimal
) -> None:
    """★ ``<0.01`` is not ``0.01``.

    A troponin reported as ``<0.01`` means *below what this assay can detect* —
    clinically the opposite of an exact 0.01. Dropping the operator loses the
    only part that mattered.
    """
    parsed = parse_value(raw)
    assert parsed is not None
    assert parsed.is_censored
    assert parsed.operator == operator
    assert parsed.number == number


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Positive", "positive"), ("NOT DETECTED", "not_detected"), ("Nil", "nil")],
)
def test_qualitative_results_are_not_coerced_into_numbers(
    raw: str, expected: str
) -> None:
    parsed = parse_value(raw)
    assert parsed is not None
    assert parsed.qualitative == expected
    assert parsed.number is None


@pytest.mark.parametrize("raw", ["", "   ", "see comment", "TBA", "***", None])
def test_unparseable_values_return_none_rather_than_a_guess(raw: str | None) -> None:
    """★ A None is a review-queue entry. A guess is a clinical error."""
    assert parse_value(raw) is None


# ── 7.3 reference ranges ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "low", "high"),
    [
        ("10-20", decimal.Decimal("10"), decimal.Decimal("20")),
        ("3.5 – 5.1", decimal.Decimal("3.5"), decimal.Decimal("5.1")),
        ("< 5", None, decimal.Decimal("5")),
        (">= 3.5", decimal.Decimal("3.5"), None),
        ("up to 40", None, decimal.Decimal("40")),
    ],
)
def test_reference_ranges_parse(
    raw: str, low: decimal.Decimal | None, high: decimal.Decimal | None
) -> None:
    parsed = parse_reference_range(raw)
    assert parsed is not None
    assert parsed.low == low
    assert parsed.high == high


def test_a_sex_specific_range_is_not_resolved_without_a_sex() -> None:
    """★ Defaulting to the male range would flag healthy women as anaemic."""
    parsed = parse_reference_range("Male: 13-17 / Female: 12-15")
    assert parsed is not None
    assert parsed.low is None and parsed.high is None
    assert parsed.text == "Male: 13-17 / Female: 12-15"


def test_a_sex_specific_range_resolves_when_the_sex_is_known() -> None:
    parsed = parse_reference_range("Male: 13-17 / Female: 12-15", sex="female")
    assert parsed is not None
    assert parsed.low == decimal.Decimal("12")
    assert parsed.high == decimal.Decimal("15")
    assert parsed.sex == "female"


def test_an_unrecognised_range_is_kept_verbatim_not_dropped() -> None:
    """An admin reviewing the queue needs to see what the lab actually printed."""
    parsed = parse_reference_range("As per clinical context")
    assert parsed is not None
    assert parsed.text == "As per clinical context"


# ── 7.6 status ────────────────────────────────────────────────────────


def test_amended_beats_final_in_the_same_stamp() -> None:
    """★ 'FINAL REPORT (AMENDED)' is an amendment.

    Matching FINAL first would lose the amendment — and an amendment is the one
    thing that must re-open a closed case.
    """
    assert parse_report_status("FINAL REPORT (AMENDED)") == STATUS_AMENDED
    assert parse_report_status("Corrected report") == STATUS_AMENDED


def test_no_stamp_is_not_the_same_as_final() -> None:
    """★ An unstamped report has not told us it is complete."""
    assert parse_report_status("Haemoglobin 9.2 g/dL") is None

    decision = status_svc.decide(incoming_status=None)
    assert decision.set_stale_timer is True
    assert decision.action == status_svc.ACTION_STORE_AND_HOLD


def test_a_preliminary_holds_the_case_open_and_starts_a_timer() -> None:
    decision = status_svc.decide(incoming_status=STATUS_PRELIMINARY)
    assert decision.action == status_svc.ACTION_STORE_AND_HOLD
    assert decision.set_stale_timer is True
    assert decision.notify is True


def test_a_final_supersedes_its_preliminary_without_alerting_twice() -> None:
    """★ The clinician was already told. A second page teaches them to ignore us."""
    decision = status_svc.decide(
        incoming_status=STATUS_FINAL,
        prior_status=STATUS_PRELIMINARY,
        prior_result_id="r-1",
        prior_was_notified=True,
    )
    assert decision.action == status_svc.ACTION_SUPERSEDE
    assert decision.supersedes_result_id == "r-1"
    assert decision.notify is False


def test_a_final_does_alert_when_the_preliminary_never_did() -> None:
    decision = status_svc.decide(
        incoming_status=STATUS_FINAL,
        prior_status=STATUS_PRELIMINARY,
        prior_result_id="r-1",
        prior_was_notified=False,
    )
    assert decision.notify is True


def test_an_amendment_reopens_a_closed_case_with_distinct_wording() -> None:
    """★ 'a result you were already told about has CHANGED' requires a different
    action from the reader than 'your patient has a result'."""
    decision = status_svc.decide(
        incoming_status=STATUS_AMENDED, case_is_closed=True, prior_result_id="r-1"
    )
    assert decision.action == status_svc.ACTION_REOPEN
    assert decision.amended_wording is True
    assert decision.notify is True
    assert "re-opened" in decision.reason


# ── 7.1 classification ────────────────────────────────────────────────


def test_a_sensitivity_grid_says_microbiology() -> None:
    result = classify_text(
        "CULTURE & SENSITIVITY\nColony count >100,000 CFU/mL\n"
        "Antibiotic Sensitivity\nCeftriaxone  R"
    )
    assert result.report_type == REPORT_MICROBIOLOGY
    assert result.confidence >= CONFIDENCE_THRESHOLD
    assert not result.needs_review


def test_a_modality_and_an_impression_say_radiology() -> None:
    result = classify_text(
        "CT SCAN OF THE ABDOMEN\n\nFINDINGS:\nNo free fluid.\n\n"
        "IMPRESSION:\nUnremarkable study.\nReported by the radiologist."
    )
    assert result.report_type == REPORT_RADIOLOGY


def test_a_cbc_heading_says_haematology() -> None:
    result = classify_text("COMPLETE BLOOD COUNT\nHaemoglobin 9.2\nPlatelet 180000")
    assert result.report_type == REPORT_HAEMATOLOGY


def test_no_signal_is_unknown_not_a_default() -> None:
    """★ 7.1: below threshold → needs_review. Never a guess."""
    result = classify_text("Thank you for your referral. Please call the desk.")
    assert result.report_type == REPORT_UNKNOWN
    assert result.needs_review


def test_an_empty_document_is_unknown() -> None:
    assert classify_text("").report_type == REPORT_UNKNOWN
    assert classify_text(None).report_type == REPORT_UNKNOWN


def test_two_report_types_scoring_equally_is_unknown() -> None:
    """★ The same tie rule as 7.5. Choosing one sends the document to a parser
    built for something else, and the failure then looks like an extraction bug.
    """
    # One signal each at weight 1.0: 'platelet' (haematology) and 'creatinine'
    # (biochemistry). Deliberately *not* 'haemoglobin', which weighs 1.5 and so
    # does not tie — the first draft of this test made exactly that mistake and
    # failed for a reason that had nothing to do with the tie rule.
    result = classify_text("Platelet 180000\nCreatinine 1.1 mg/dL")
    assert result.report_type == REPORT_UNKNOWN
    assert "tied" in result.evidence


def test_node_b_being_down_is_recorded_as_retryable() -> None:
    """'We could not ask' and 'we asked and it did not know' are different
    facts, and only one is worth retrying."""
    result = unknown_because_node_b_is_down()
    assert result.report_type == REPORT_UNKNOWN
    assert result.evidence["retryable"] is True


# ── 7.3 narrative sections ────────────────────────────────────────────


def test_sections_are_segmented_by_heading() -> None:
    sections = split_sections(
        "CT ABDOMEN\n\nFINDINGS:\nNo free fluid seen.\n\n"
        "IMPRESSION:\nNormal study.\n"
    )
    assert sections["findings"].startswith("No free fluid")
    assert sections["impression"].startswith("Normal study")
    assert "CT ABDOMEN" in sections["preamble"]


def test_text_with_no_headings_is_kept_whole_not_discarded() -> None:
    """★ A finding in an unfamiliar section must still reach Phase 3's scan."""
    sections = split_sections("Large abscess in the right lobe of the liver.")
    assert sections == {"preamble": "Large abscess in the right lobe of the liver."}


def test_a_repeated_heading_appends_rather_than_replaces() -> None:
    """A second IMPRESSION block must not silently delete the first."""
    sections = split_sections(
        "IMPRESSION:\nFirst thought.\n\nFINDINGS:\nSomething.\n\n"
        "IMPRESSION:\nSecond thought.\n"
    )
    assert "First thought" in sections["impression"]
    assert "Second thought" in sections["impression"]


# ── 7.4 normalisation ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SR. CREATININE", "sr creatinine"),
        ("S. Creat.", "s creat"),
        ("  Vitamin  B12  ", "vitamin b12"),
        ("Hémoglobine", "hemoglobine"),
        ("µmol test", "μmol test"),
    ],
)
def test_test_names_normalise_consistently(raw: str, expected: str) -> None:
    assert normalise_test_name(raw) == expected


def test_normalisation_folds_the_two_micro_signs_together() -> None:
    """★ U+00B5 and U+03BC look identical and would split one analyte in two."""
    assert normalise_test_name("µg/dL") == normalise_test_name("μg/dL")
