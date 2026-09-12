"""Regression tests for the defects the Phase 3 audit found.

One test per finding, each written so it fails if the fix is reverted. They
live together rather than being scattered into the rule suites because what
they have in common is the thing worth remembering: **every one of them was
found by running the code adversarially, not by reading it**, and four of the
five fail in the same direction — a result that should reach a human quietly
does not.

* **A1** — ``is_negated`` returned on the first pattern that matched, and the
  patterns came back from an unordered ``SELECT``. A sentence carrying both a
  hedge and a denial classified differently depending on row order, and one of
  those orders silenced the finding entirely.
* **A2** — ``1.5e5`` parsed as **1.5** and ``>10⁵`` as **10**. Both put a
  heavy growth under the contaminant threshold, and the contaminant branch
  returns before the resistance comparison.
* **A3** — a rule that raised took the whole classification with it. Intake
  has already cancelled ``result_due``, so the case had no timer and no flag:
  silence, which is the one thing THE ONE RULE forbids.
* **A4** — a non-finite value made Rule A raise rather than answer.
* **A5** — the no-growth and contaminant organism lists were read from
  ``rule_config`` but never seeded, so in practice they lived in Python.
* **A6** — two MDRO rules can match one organism; which code was recorded
  depended on row order.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.rules import SEVERITY_CRITICAL, SEVERITY_FOLLOW_UP
from app.rules.culture import (
    REASON_CONTAMINANT,
    REASON_MDRO,
    REASON_RESISTANT_TO_DISCHARGE_DRUG,
    DischargeDrug,
    classify_organism,
    parse_colony_count,
)
from app.rules.narrative import classify_narrative_text, is_negated
from app.rules.numeric import REASON_NO_VALUE, classify_analyte
from scripts.seed_rules_dev import _seed as seed_rules

pytestmark = pytest.mark.integration

D = Decimal


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
            await seed_rules(s)
            yield s
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


# ── A1 · negation resolution must not depend on row order ─────────────

BOTH_IN_SCOPE = "Malignancy is unlikely; ruled out on the prior imaging."


async def test_a1_negation_does_not_depend_on_config_row_order(
    session: AsyncSession,
) -> None:
    """The bug: ``\\bruled out\\b`` (denial) and ``\\bunlikely\\b`` (hedge) are
    both in scope of the same term. Returning on the first match made the
    answer depend on the order PostgreSQL happened to return the rows in —
    which is not stable across vacuums or plan changes."""
    rows = (
        await session.execute(
            text(
                "SELECT pattern, scope_words_before, scope_words_after, is_hedge "
                "  FROM negation_patterns WHERE active AND deleted_at IS NULL"
            )
        )
    ).all()
    negations = [
        (r.pattern, r.scope_words_before, r.scope_words_after, r.is_hedge) for r in rows
    ]
    term = BOTH_IN_SCOPE.lower().index("malignancy")

    forward = is_negated(BOTH_IN_SCOPE, term, negations)
    backward = is_negated(BOTH_IN_SCOPE, term, list(reversed(negations)))
    shuffled = is_negated(BOTH_IN_SCOPE, term, sorted(negations, key=lambda n: n[3]))

    assert forward == backward == shuffled


async def test_a1_a_hedge_outranks_a_denial_rather_than_racing_it(
    session: AsyncSession,
) -> None:
    """And the winner is the reading that does not silence the finding. A
    report saying a malignancy is *unlikely* is still a report somebody should
    look at."""
    verdict = await classify_narrative_text(
        session, BOTH_IN_SCOPE, category="radiology"
    )
    assert verdict.severity == SEVERITY_FOLLOW_UP
    assert (
        "malignancy" in verdict.matched_terms
    ), "the finding was discarded — a denial silenced a hedged finding"


async def test_a1_a_plain_denial_still_suppresses(session: AsyncSession) -> None:
    """The fix must not turn every negation into a hedge. With no hedge in
    scope, a denial still denies."""
    verdict = await classify_narrative_text(
        session, "No evidence of malignancy.", category="radiology"
    )
    assert verdict.matched_terms == []


async def test_a1_configuration_is_read_in_a_stable_order(
    session: AsyncSession,
) -> None:
    """Belt and braces: the queries that feed the rule are ordered, so two
    runs see the same list even before the tie-break applies.

    Stability is the property, not any particular order — PostgreSQL sorts
    under the database collation, which is not Python's codepoint order, and
    asserting against ``sorted()`` would be testing the locale.
    """
    for query in (
        "SELECT term FROM clinical_keywords WHERE active AND deleted_at IS NULL"
        " ORDER BY term",
        "SELECT pattern FROM negation_patterns WHERE active AND deleted_at IS NULL"
        " ORDER BY pattern",
    ):
        first = [r[0] for r in (await session.execute(text(query))).all()]
        second = [r[0] for r in (await session.execute(text(query))).all()]
        assert first == second
        assert len(first) > 1, "an ordering test on one row proves nothing"


# ── A2 · colony counts must never under-read ──────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.5e5", D("150000")),
        ("3.2E4", D("32000")),
        (">10⁵", D("100000")),
        ("10⁶", D("1000000")),
        ("50,000-100,000 CFU/mL", D("100000")),
        ("5,000 to 50,000", D("50000")),
        # The forms that already worked, kept so a rewrite cannot lose them.
        ("10^5", D("100000")),
        ("10e5", D("100000")),
        ("1.5 x 10^5", D("150000")),
        (">100,000 CFU/mL", D("100000")),
    ],
)
def test_a2_colony_counts_are_not_under_read(raw: str, expected: Decimal) -> None:
    """Every entry here once parsed to something smaller than the truth, or
    guards a form that does. An under-read is the dangerous direction: it puts
    a heavy growth under the contaminant threshold."""
    assert parse_colony_count(raw) == expected


async def test_a2_an_e_notation_growth_still_reaches_the_resistance_check(
    session: AsyncSession,
) -> None:
    """The severity, not the number. ``1.5e5`` read as 1.5 would have been
    dismissed as contamination before Rule B ever compared the organism
    against what the patient is taking."""
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count="1.5e5 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Ceftriaxone", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.reason_code == REASON_RESISTANT_TO_DISCHARGE_DRUG


async def test_a2_a_superscript_growth_still_reaches_the_resistance_check(
    session: AsyncSession,
) -> None:
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count=">10⁵ CFU/mL",
        specimen_type="urine",
        sensitivities=[("Ciprofloxacin", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Ciplox")],
    )
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.reason_code == REASON_RESISTANT_TO_DISCHARGE_DRUG


async def test_a2_a_genuinely_low_count_is_still_a_contaminant(
    session: AsyncSession,
) -> None:
    """The fix must not stop the contaminant branch working — that branch is
    what keeps the ward from being flooded."""
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count="2.5e3 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Ceftriaxone", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.reason_code == REASON_CONTAMINANT


# ── A4 · a non-finite value must not take Rule A down ─────────────────


@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D("-Infinity")])
async def test_a4_a_non_finite_value_is_follow_up_not_an_exception(
    session: AsyncSession, value: Decimal
) -> None:
    """``Decimal('NaN') >= Decimal('6.5')`` raises rather than returning False,
    so a NaN reaching the comparisons took the whole classification with it."""
    out = await classify_analyte(
        session,
        test_code="POTASSIUM",
        value_numeric=value,
        ref_low=D("3.5"),
        ref_high=D("5.1"),
    )
    assert out.severity == SEVERITY_FOLLOW_UP
    assert out.reason_code == REASON_NO_VALUE


def test_a4_the_api_refuses_non_finite_values_at_the_door() -> None:
    """The first lock. Rule A's guard is the second, for a row that arrived
    some other way."""
    from pydantic import ValidationError

    from app.schemas.results import AnalyteIn

    for raw in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValidationError):
            AnalyteIn(test_name="POTASSIUM", value_numeric=raw)  # type: ignore[arg-type]


# ── A5 · the clinical lists must live in the table ────────────────────


@pytest.mark.parametrize(
    "key", ["culture_no_growth_patterns", "culture_contaminant_organisms"]
)
async def test_a5_the_clinical_lists_are_seeded_as_configuration(
    session: AsyncSession, key: str
) -> None:
    """Both were read from ``rule_config`` and never written to it, so the
    engine always fell through to constants in Python. §3.2: *"thresholds and
    delays live in tables an admin can edit, never in code."*"""
    row = (
        await session.execute(
            text("SELECT value FROM rule_config WHERE key = :k AND deleted_at IS NULL"),
            {"k": key},
        )
    ).first()
    assert row is not None, f"{key} is not seeded — the list still lives in code"
    assert isinstance(row.value.get("values"), list)
    assert row.value["values"]


async def test_a5_editing_the_contaminant_list_changes_the_answer(
    session: AsyncSession,
) -> None:
    """Proof the table is what the rule reads, not decoration beside a
    hardcoded list: remove an organism from the list and it stops being
    treated as a contaminant."""
    before = await classify_organism(
        session,
        organism_name="Staphylococcus epidermidis",
        colony_count=">100,000 CFU/mL",
        specimen_type="blood",
        sensitivities=[("Ceftriaxone", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert before.reason_code == REASON_CONTAMINANT

    await session.execute(
        text(
            'UPDATE rule_config SET value = \'{"values": ["mixed flora"]}\'::jsonb '
            " WHERE key = 'culture_contaminant_organisms'"
        )
    )

    after = await classify_organism(
        session,
        organism_name="Staphylococcus epidermidis",
        colony_count=">100,000 CFU/mL",
        specimen_type="blood",
        sensitivities=[("Ceftriaxone", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert after.reason_code == REASON_RESISTANT_TO_DISCHARGE_DRUG


async def test_a5_editing_the_no_growth_list_changes_the_answer(
    session: AsyncSession,
) -> None:
    await session.execute(
        text(
            'UPDATE rule_config SET value = \'{"values": ["axenic"]}\'::jsonb '
            " WHERE key = 'culture_no_growth_patterns'"
        )
    )
    finding = await classify_organism(
        session,
        organism_name="Axenic",
        colony_count=None,
        specimen_type="urine",
        sensitivities=[],
        discharge_drugs=[],
    )
    assert finding.reason_code == "CULT_NO_GROWTH"
    assert finding.auto_close is True


# ── A6 · one organism, two MDRO rules, one stable answer ──────────────


async def test_a6_the_recorded_mdro_code_is_deterministic(
    session: AsyncSession,
) -> None:
    """ "MRSA (methicillin-resistant Staphylococcus aureus)" with an oxacillin
    R matches both ``MRSA`` and ``MRSA_BY_PANEL``. The severity was never in
    doubt; the code written into the explanation was.

    A third matching rule is inserted whose code sorts *first*, because that
    is what separates "ordered by code" from "the unordered scan happened to
    come back the same way twice". Without ``ORDER BY code`` the seeded rules
    are returned in insertion order and ``AAA_TEST_MDRO`` — inserted last —
    would not win.
    """
    await session.execute(
        text(
            "INSERT INTO mdro_rules (id, code, label, organism_pattern, active) "
            "VALUES (:i, 'AAA_TEST_MDRO', 'Sorts first', "
            "        '(?i)staphylococcus', true)"
        ),
        {"i": str(uuid.uuid4())},
    )

    codes = set()
    for _ in range(4):
        finding = await classify_organism(
            session,
            organism_name="MRSA (methicillin-resistant Staphylococcus aureus)",
            colony_count="moderate growth",
            specimen_type="wound swab",
            sensitivities=[("Oxacillin", "R"), ("Vancomycin", "S")],
            discharge_drugs=[DischargeDrug(drug_name="Augmentin")],
        )
        assert finding.reason_code == REASON_MDRO
        codes.add(str(finding.detail["mdro_code"]))

    assert codes == {"AAA_TEST_MDRO"}, (
        "the MDRO rule was not selected by code order, so the explanation "
        f"depends on how the rows came back: {codes}"
    )


# ── A3 · a failing rule must not silence the case ─────────────────────


async def _case_with_a_result(session: AsyncSession) -> dict[str, str]:
    tag = uuid.uuid4().hex[:8]
    ids = {
        k: str(uuid.uuid4()) for k in ("u", "d", "p", "e", "o", "c", "case", "result")
    }
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role) "
            "VALUES (:u, :uc, 'D', 'doctor')"
        ),
        {"u": ids["u"], "uc": f"AF{tag}"},
    )
    await session.execute(
        text("INSERT INTO departments (id, code, name) VALUES (:i, :c, 'D')"),
        {"i": ids["d"], "c": f"AF{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO patients (id, mrn, name, sex, dob) "
            "VALUES (:i, :m, 'P', 'female', DATE '1980-01-01')"
        ),
        {"i": ids["p"], "m": f"AF{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            "status, department_id) "
            "VALUES (:i, :p, :n, 'ipd', now(), 'discharged', :d)"
        ),
        {"i": ids["e"], "p": ids["p"], "n": f"AF{tag}", "d": ids["d"]},
    )
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            "category, ordered_at, status) "
            "VALUES (:i, :e, :p, 'T', 'T', 'lab', now(), 'in_lab')"
        ),
        {"i": ids["o"], "e": ids["e"], "p": ids["p"]},
    )
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": ids["c"], "e": ids["e"], "o": ids["o"], "u": ids["u"]},
    )
    await session.execute(
        text(
            "INSERT INTO pending_cases "
            "(id, order_id, encounter_id, patient_id, contract_id, current_owner_id) "
            "VALUES (:i, :o, :e, :p, :c, :u)"
        ),
        {
            "i": ids["case"],
            "o": ids["o"],
            "e": ids["e"],
            "p": ids["p"],
            "c": ids["c"],
            "u": ids["u"],
        },
    )
    await session.execute(
        text(
            "INSERT INTO results (id, order_id, case_id, report_status, source, "
            " reported_at) VALUES (:i, :o, :c, 'final', 'manual', now())"
        ),
        {"i": ids["result"], "o": ids["o"], "c": ids["case"]},
    )
    await session.execute(
        text(
            "INSERT INTO result_analytes (id, result_id, seq, test_name_raw, "
            " value_numeric) VALUES (:i, :r, 1, 'POTASSIUM', 4.2)"
        ),
        {"i": str(uuid.uuid4()), "r": ids["result"]},
    )
    await session.commit()
    return ids


async def test_a3_a_failing_rule_still_flags_the_case(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the fix this raised out of ``classify_result``, the consumer
    retried five times, the message went to the DLQ — and the case sat in
    ``result_received`` with no pending timer and no flag. Nothing would ever
    have woken anybody."""
    from app.rules import orchestrator

    ids = await _case_with_a_result(session)

    async def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("a malformed configuration row")

    monkeypatch.setattr(orchestrator, "classify_result_analytes", _explode)

    classification = await orchestrator.classify_result(
        session, uuid.UUID(ids["result"])
    )

    assert classification.severity == SEVERITY_FOLLOW_UP
    assert classification.auto_closed is False
    assert any(
        o.reason_code == orchestrator.REASON_RULE_FAILED
        for o in classification.rule_outputs
    )

    case = (
        await session.execute(
            text("SELECT state, severity FROM pending_cases WHERE id = :i"),
            {"i": ids["case"]},
        )
    ).one()
    assert case.state == "flagged"
    assert case.severity == SEVERITY_FOLLOW_UP


async def test_a3_the_other_rules_still_run_when_one_fails(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degrade the failing rule, not the report. A broken Rule B must not cost
    the potassium its answer."""
    from app.rules import orchestrator

    ids = await _case_with_a_result(session)

    async def _explode(*args: object, **kwargs: object) -> object:
        raise ValueError("boom")

    monkeypatch.setattr(orchestrator, "classify_result_culture", _explode)

    classification = await orchestrator.classify_result(
        session, uuid.UUID(ids["result"])
    )
    rule_ids = {o.rule_id for o in classification.rule_outputs}
    assert "A_numeric" in rule_ids, "the healthy rule was lost with the broken one"
    assert "B_culture" in rule_ids


async def test_a3_a_database_failure_is_re_raised_so_the_queue_retries(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the fix. A lost connection is not a classification
    outcome — swallowing it would record FOLLOW_UP for a result nobody
    actually read, and the retry would never happen."""
    from sqlalchemy.exc import OperationalError

    from app.rules import orchestrator

    ids = await _case_with_a_result(session)

    async def _db_down(*args: object, **kwargs: object) -> object:
        raise OperationalError("SELECT 1", {}, Exception("connection lost"))

    monkeypatch.setattr(orchestrator, "classify_result_analytes", _db_down)

    with pytest.raises(OperationalError):
        await orchestrator.classify_result(session, uuid.UUID(ids["result"]))
