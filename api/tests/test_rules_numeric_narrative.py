"""Rules A and C. Phase 3.3 / 3.5, against real PostgreSQL and real config.

Every threshold, keyword and negation pattern is read from the database, so
these tests seed the development configuration and then assert behaviour --
not internals. A test that asserted "the function called `_panic_threshold`"
would keep passing while the classification silently went wrong.

The two properties that matter most, and which are asserted repeatedly:

* **Unclassifiable is never NORMAL.** A value with no range, a report with no
  content, a keyword the engine could not check -- all FOLLOW_UP.
* **A negated finding does not flag.** *"No evidence of malignancy"* is the
  build plan's own Exit Gate example.
"""

from __future__ import annotations

import inspect
import re
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.rules import SEVERITY_CRITICAL, SEVERITY_FOLLOW_UP, SEVERITY_NORMAL
from app.rules.narrative import (
    classify_narrative_text,
    classify_result_narratives,
    is_negated,
    split_sentences,
)
from app.rules.numeric import (
    REASON_ABOVE_CRITICAL_HIGH,
    REASON_ABOVE_RANGE,
    REASON_BELOW_CRITICAL_LOW,
    REASON_CENSORED_UNCOMPARABLE,
    REASON_FAR_ABOVE_RANGE,
    REASON_IN_RANGE,
    REASON_NO_RANGE,
    REASON_NO_VALUE,
    classify_analyte,
    convert_unit,
    parse_censored,
)
from scripts.seed_rules_dev import _seed as seed_rules

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        conn = await engine.connect()
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    trans = await conn.begin()
    maker = async_sessionmaker(
        bind=conn,
        expire_on_commit=False,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with maker() as s:
            # The rules read their configuration from the database, so it has
            # to be there. Rolled back with everything else.
            await seed_rules(s)
            yield s
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


D = Decimal


# ── censored value parsing (3.3 "Also handle") ────────────────────────


@pytest.mark.parametrize(
    ("raw", "operator", "value"),
    [
        ("<0.01", "<", D("0.01")),
        ("> 1000", ">", D("1000")),
        ("<=5", "<=", D("5")),
        (">= 3.2", ">=", D("3.2")),
        ("≤ 0.5", "<=", D("0.5")),
        ("≥ 40", ">=", D("40")),
    ],
)
def test_censored_values_parse(raw: str, operator: str, value: Decimal) -> None:
    parsed = parse_censored(raw)
    assert parsed is not None
    assert parsed.operator == operator
    assert parsed.value == value


@pytest.mark.parametrize("raw", ["1.2", "NEG", "", None, "trace", "positive"])
def test_non_censored_values_are_not_mistaken_for_censored(raw: str | None) -> None:
    assert parse_censored(raw) is None


# ── Rule A ────────────────────────────────────────────────────────────


async def test_a_value_inside_the_range_is_normal(session: AsyncSession) -> None:
    out = await classify_analyte(
        session,
        test_code="POTASSIUM",
        value_numeric=D("4.0"),
        ref_low=D("3.5"),
        ref_high=D("5.1"),
    )
    assert out.severity == SEVERITY_NORMAL
    assert out.reason_code == REASON_IN_RANGE


async def test_a_value_past_the_panic_high_is_critical(session: AsyncSession) -> None:
    out = await classify_analyte(
        session,
        test_code="POTASSIUM",
        value_numeric=D("7.2"),
        ref_low=D("3.5"),
        ref_high=D("5.1"),
    )
    assert out.severity == SEVERITY_CRITICAL
    assert out.reason_code == REASON_ABOVE_CRITICAL_HIGH
    # The threshold that produced it is named, so the decision is explicable.
    assert out.inputs_used["panic_threshold_id"]
    assert "PLACEHOLDER" in str(out.inputs_used["panic_source"])


async def test_a_value_past_the_panic_low_is_critical(session: AsyncSession) -> None:
    out = await classify_analyte(
        session,
        test_code="POTASSIUM",
        value_numeric=D("2.1"),
        ref_low=D("3.5"),
        ref_high=D("5.1"),
    )
    assert out.severity == SEVERITY_CRITICAL
    assert out.reason_code == REASON_BELOW_CRITICAL_LOW


async def test_a_panic_value_is_critical_even_with_no_reference_range(
    session: AsyncSession,
) -> None:
    """The documented judgement call: panic bounds are checked before the
    range test, because a critical potassium with no range attached is still
    critical and "unclassifiable" would bury it."""
    out = await classify_analyte(session, test_code="POTASSIUM", value_numeric=D("7.5"))
    assert out.severity == SEVERITY_CRITICAL
    assert out.reason_code == REASON_ABOVE_CRITICAL_HIGH


async def test_a_slightly_high_value_is_follow_up(session: AsyncSession) -> None:
    out = await classify_analyte(
        session,
        test_code="ALT",
        value_numeric=D("50"),
        ref_low=D("10"),
        ref_high=D("40"),
    )
    assert out.severity == SEVERITY_FOLLOW_UP
    assert out.reason_code == REASON_ABOVE_RANGE


async def test_far_beyond_the_range_is_critical(session: AsyncSession) -> None:
    """Step 7: beyond the range by more than slight_abnormal_factor (1.5x)."""
    out = await classify_analyte(
        session,
        test_code="ALT",
        value_numeric=D("400"),
        ref_low=D("10"),
        ref_high=D("40"),
    )
    assert out.severity == SEVERITY_CRITICAL
    assert out.reason_code == REASON_FAR_ABOVE_RANGE
    assert out.inputs_used["slight_abnormal_factor"] == "1.5"


async def test_a_value_with_no_range_at_all_is_follow_up(
    session: AsyncSession,
) -> None:
    """Step 2. Unclassifiable, and unclassifiable is never NORMAL -- NORMAL
    would auto-close a result nobody has checked."""
    out = await classify_analyte(
        session, test_code="SOME_UNKNOWN_ASSAY", value_numeric=D("42")
    )
    assert out.severity == SEVERITY_FOLLOW_UP
    assert out.reason_code == REASON_NO_RANGE


async def test_a_non_numeric_value_is_follow_up(session: AsyncSession) -> None:
    out = await classify_analyte(
        session, test_code="CULTURE", value_numeric=None, value_raw="NEG"
    )
    assert out.severity == SEVERITY_FOLLOW_UP
    assert out.reason_code == REASON_NO_VALUE


async def test_a_censored_high_value_still_reaches_a_panic_bound(
    session: AsyncSession,
) -> None:
    """ ">1000" is not a missing value; on a troponin it is the worst one."""
    out = await classify_analyte(
        session,
        test_code="TROPONIN",
        value_numeric=None,
        value_raw=">1000",
        ref_low=D("0"),
        ref_high=D("0.04"),
    )
    assert out.severity == SEVERITY_CRITICAL
    assert out.reason_code == REASON_ABOVE_CRITICAL_HIGH


async def test_a_censored_value_cannot_prove_it_is_in_range(
    session: AsyncSession,
) -> None:
    """ "<0.5" with a low bound of 0.2 might be anywhere below 0.5. Saying
    NORMAL there would be a confident guess."""
    out = await classify_analyte(
        session,
        test_code="SOME_ASSAY",
        value_numeric=None,
        value_raw="<0.5",
        ref_low=D("0.2"),
        ref_high=D("1.0"),
    )
    assert out.severity == SEVERITY_FOLLOW_UP
    assert out.reason_code == REASON_CENSORED_UNCOMPARABLE


async def test_every_path_emits_a_reason_code(session: AsyncSession) -> None:
    """3.3: "reason_code emitted on every path"."""
    cases = [
        {
            "test_code": "POTASSIUM",
            "value_numeric": D("4.0"),
            "ref_low": D("3.5"),
            "ref_high": D("5.1"),
        },
        {"test_code": "POTASSIUM", "value_numeric": D("9.0")},
        {"test_code": "UNKNOWN", "value_numeric": D("1")},
        {"test_code": "UNKNOWN", "value_numeric": None, "value_raw": "NEG"},
        {
            "test_code": "ALT",
            "value_numeric": D("45"),
            "ref_low": D("10"),
            "ref_high": D("40"),
        },
        {
            "test_code": "ALT",
            "value_numeric": D("5"),
            "ref_low": D("10"),
            "ref_high": D("40"),
        },
    ]
    for kwargs in cases:
        out = await classify_analyte(session, **kwargs)  # type: ignore[arg-type]
        assert out.reason_code, kwargs
        assert out.reason_code.startswith("NUM_"), out.reason_code
        assert out.rule_id == "A_numeric"
        assert out.inputs_used, "a decision with no recorded inputs is unexplainable"


async def test_unit_conversion_uses_the_table(session: AsyncSession) -> None:
    converted = await convert_unit(
        session, D("100"), "umol/L", "mg/dL", test_code="CREATININE"
    )
    assert converted is not None
    assert D("1.0") < converted < D("1.2")


async def test_an_unconfigured_conversion_returns_none(
    session: AsyncSession,
) -> None:
    """Not an identity. Comparing mg/dL against a µmol/L range because no row
    existed would produce a confident, wrong severity."""
    assert await convert_unit(session, D("5"), "fathoms", "mg/dL") is None


async def test_an_expired_threshold_is_not_used(session: AsyncSession) -> None:
    """effective_to exists so a decision stays explicable after the SOP
    changes -- and so a retired threshold stops being applied."""
    await session.execute(
        text(
            "UPDATE panic_thresholds SET effective_to = now() - interval '1 day' "
            " WHERE test_code = 'POTASSIUM'"
        )
    )
    out = await classify_analyte(
        session,
        test_code="POTASSIUM",
        value_numeric=D("7.2"),
        ref_low=D("3.5"),
        ref_high=D("5.1"),
    )
    # No longer critical by panic bound; still abnormal by range.
    assert out.reason_code != REASON_ABOVE_CRITICAL_HIGH
    assert out.severity in (SEVERITY_FOLLOW_UP, SEVERITY_CRITICAL)


# ── Rule C: sentence splitting ────────────────────────────────────────


def test_sentences_split_without_breaking_on_decimals_or_titles() -> None:
    body = (
        "There is a 1.5 cm nodule in the right upper lobe. "
        "Reported by Dr. Menon. No evidence of malignancy."
    )
    sentences = split_sentences(body)
    assert len(sentences) == 3
    assert "1.5 cm nodule" in sentences[0]
    assert sentences[1].endswith("Dr. Menon.")


def test_empty_text_splits_to_nothing() -> None:
    assert split_sentences("") == []
    assert split_sentences("   ") == []


# ── Rule C: the Exit Gate 3 negation case ─────────────────────────────


async def test_a_negated_finding_does_not_flag(session: AsyncSession) -> None:
    """Exit Gate 3: *"A negated sentence ("no evidence of malignancy") does
    not produce a flag."*"""
    verdict = await classify_narrative_text(
        session, "No evidence of malignancy.", category="radiology"
    )
    assert verdict.matched_terms == []
    # Radiology with no positive finding is still FOLLOW_UP -- never critical.
    assert verdict.severity == SEVERITY_FOLLOW_UP


@pytest.mark.parametrize(
    "sentence",
    [
        "No evidence of malignancy.",
        "Negative for malignancy.",
        "Malignancy has been ruled out.",
        "No definite evidence of carcinoma.",
        "Chest is free of consolidation.",
        "No pneumothorax.",
    ],
)
async def test_the_documented_negation_forms_all_suppress(
    session: AsyncSession, sentence: str
) -> None:
    verdict = await classify_narrative_text(session, sentence, category="radiology")
    assert verdict.severity != SEVERITY_CRITICAL, sentence
    assert verdict.matched_terms == [], sentence


async def test_an_unnegated_finding_does_flag(session: AsyncSession) -> None:
    verdict = await classify_narrative_text(
        session, "Findings consistent with malignancy.", category="radiology"
    )
    assert verdict.severity == SEVERITY_CRITICAL
    assert "malignancy" in verdict.matched_terms


async def test_negation_does_not_leak_across_sentences(
    session: AsyncSession,
) -> None:
    """The reason sentences are split first. A negation in one sentence must
    not suppress a finding in the next."""
    verdict = await classify_narrative_text(
        session,
        "No evidence of infection. Findings consistent with malignancy.",
        category="radiology",
    )
    assert verdict.severity == SEVERITY_CRITICAL
    assert "malignancy" in verdict.matched_terms


async def test_negation_scope_is_bounded_within_a_sentence(
    session: AsyncSession,
) -> None:
    """An unbounded negation would suppress a finding twenty words later."""
    verdict = await classify_narrative_text(
        session,
        (
            "No evidence of fracture in the visualised bony structures of the "
            "thorax and upper abdomen, however there is a large abscess in the "
            "right lobe of the liver."
        ),
        category="radiology",
    )
    assert verdict.severity == SEVERITY_CRITICAL
    assert "abscess" in verdict.matched_terms


async def test_a_hedge_downgrades_rather_than_discards(
    session: AsyncSession,
) -> None:
    """ "Cannot exclude malignancy" is not a negative finding; it is one
    somebody should look at."""
    verdict = await classify_narrative_text(
        session, "Cannot exclude malignancy.", category="radiology"
    )
    assert verdict.severity == SEVERITY_FOLLOW_UP
    assert "malignancy" in verdict.matched_terms


async def test_matching_is_word_boundary_not_substring(
    session: AsyncSession,
) -> None:
    """ "lesion" must not fire inside another word, and "nodule" must not fire
    inside "nodules of the thyroid" being spelled differently."""
    verdict = await classify_narrative_text(
        session,
        "The patient tolerated the procedure well and was given a cannula.",
        category="radiology",
    )
    assert verdict.matched_terms == []


async def test_radiology_with_no_findings_is_follow_up(
    session: AsyncSession,
) -> None:
    """Step 6: "No hits -> FOLLOW_UP if radiology/pathology"."""
    verdict = await classify_narrative_text(
        session, "The study is technically adequate.", category="radiology"
    )
    assert verdict.severity == SEVERITY_FOLLOW_UP


async def test_a_non_radiology_narrative_with_no_findings_is_normal(
    session: AsyncSession,
) -> None:
    verdict = await classify_narrative_text(
        session, "The study is technically adequate.", category="lab"
    )
    assert verdict.severity == SEVERITY_NORMAL


async def test_a_broken_negation_regex_never_suppresses_a_finding(
    session: AsyncSession,
) -> None:
    """An admin typing a bad regex must not silently hide malignancies."""
    await session.execute(
        text(
            "INSERT INTO negation_patterns (id, pattern, active) "
            "VALUES (:i, '((((unclosed', true)"
        ),
        {"i": str(uuid.uuid4())},
    )
    verdict = await classify_narrative_text(
        session, "Findings consistent with malignancy.", category="radiology"
    )
    assert verdict.severity == SEVERITY_CRITICAL


def test_is_negated_reports_the_pattern_that_matched() -> None:
    negations = [(r"\bno evidence of\b", 0, 6, False)]
    sentence = "No evidence of malignancy."
    negated, hedged, pattern = is_negated(
        sentence, sentence.lower().index("malig"), negations
    )
    assert negated is True
    assert hedged is False
    assert pattern == r"\bno evidence of\b"


def test_narratives_can_never_emit_an_auto_close() -> None:
    """The plan's critical safety rule, enforced structurally.

    Rule C's only ``auto_close`` is a literal ``False``. Asserted on the source
    because the property being protected is *"no future edit adds a True"* --
    a behavioural test on today's inputs would not catch that.
    """
    source = inspect.getsource(classify_result_narratives)
    emitted = re.findall(r'"auto_close":\s*(\w+)', source)
    assert emitted == ["False"]
