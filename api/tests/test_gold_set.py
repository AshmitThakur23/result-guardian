"""Phase 3.8 — the gold-set harness.

    Build a gold set: 100+ real anonymised results spanning all three rule
    types. Store as ``tests/fixtures/gold_set.yaml`` with expected severity.
    Run as a pytest parametrised suite — **this becomes your regression net
    forever**.

This file is that suite. What it is **not** is clinical validation.

⚠️ **NO CLINICIAN HAS REVIEWED THE GOLD SET.** Every case in the fixture is
synthetic, written by the engineering team, and its ``expected_severity`` is
an engineering expectation. Exit Gate 3's *"gold set passes at ≥95% agreement
with the clinician"* is **open** and cannot be closed by anything in this
repository.

Two of the tests below exist purely to keep that honest:

* ``test_the_gold_set_does_not_claim_clinician_validation`` fails the moment
  ``clinician_validated`` is flipped to true without a named reviewer and a
  date. Flipping a flag is easier than booking a clinician, so the flag alone
  is not allowed to be enough.
* ``test_no_agreement_rate_is_reported_for_synthetic_cases`` is why this suite
  prints a pass count and never a percentage. A percentage next to the words
  "gold set" reads as clinical agreement no matter what the prose around it
  says.

Each case runs the *whole* path — content is saved through the real intake
service and classified by the real orchestrator — so a case that passes here
is a case the product actually gets right, not one a helper function does.
"""

from __future__ import annotations

import ast
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.rules.orchestrator import classify_result
from app.schemas.results import ResultContent
from app.services.results import record_result
from scripts.seed_rules_dev import _seed as seed_rules

pytestmark = pytest.mark.integration

GOLD_SET_PATH = Path(__file__).parent / "fixtures" / "gold_set.yaml"


def _load() -> dict[str, Any]:
    with GOLD_SET_PATH.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    assert isinstance(loaded, dict)
    return loaded


GOLD_SET = _load()
CASES: list[dict[str, Any]] = GOLD_SET["cases"]
META: dict[str, Any] = GOLD_SET["meta"]


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


async def _stage(session: AsyncSession, case: dict[str, Any]) -> uuid.UUID:
    """Build a real patient, encounter, order and case for one gold entry."""
    tag = uuid.uuid4().hex[:10]
    order_spec = case.get("order") or {}
    encounter_spec = case.get("encounter") or {}
    ids = {
        k: str(uuid.uuid4())
        for k in ("dept", "user", "pat", "enc", "order", "contract", "case")
    }

    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role) "
            "VALUES (:u, :uc, 'Dr Gold', 'doctor')"
        ),
        {"u": ids["user"], "uc": f"G{tag}"},
    )
    await session.execute(
        text("INSERT INTO departments (id, code, name) VALUES (:i, :c, 'Gold')"),
        {"i": ids["dept"], "c": f"G{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO patients (id, mrn, name, sex, dob) "
            "VALUES (:i, :m, 'Gold Patient', 'female', DATE '1980-01-01')"
        ),
        {"i": ids["pat"], "m": f"GOLD{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            "status, department_id) "
            "VALUES (:i, :p, :n, 'ipd', now(), 'discharged', :d)"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"GENC{tag}", "d": ids["dept"]},
    )
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            "category, ordered_at, status) "
            "VALUES (:i, :e, :p, :code, :name, :cat, now(), 'in_lab')"
        ),
        {
            "i": ids["order"],
            "e": ids["enc"],
            "p": ids["pat"],
            "code": order_spec.get("test_code", "MISC"),
            "name": order_spec.get("test_name", "Miscellaneous"),
            "cat": order_spec.get("category", "lab"),
        },
    )
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": ids["contract"], "e": ids["enc"], "o": ids["order"], "u": ids["user"]},
    )
    await session.execute(
        text(
            "INSERT INTO pending_cases "
            "(id, order_id, encounter_id, patient_id, contract_id, current_owner_id) "
            "VALUES (:i, :o, :e, :p, :c, :u)"
        ),
        {
            "i": ids["case"],
            "o": ids["order"],
            "e": ids["enc"],
            "p": ids["pat"],
            "c": ids["contract"],
            "u": ids["user"],
        },
    )
    for drug in encounter_spec.get("discharge_antibiotics", []):
        await session.execute(
            text(
                "INSERT INTO discharge_medications "
                "(id, encounter_id, drug_name, is_antibiotic) "
                "VALUES (:i, :e, :d, true)"
            ),
            {"i": str(uuid.uuid4()), "e": ids["enc"], "d": drug},
        )

    # Through the real intake path, so the fixture exercises what a lab tech's
    # Save button does rather than a shortcut nobody uses in production.
    intake = await record_result(
        session,
        uuid.UUID(ids["order"]),
        report_status=case.get("report_status", "final"),
        content=ResultContent.model_validate(case.get("content") or {}),
    )
    return intake.result_id


@pytest.mark.parametrize("case", CASES, ids=[f"{c['id']}-{c['rule']}" for c in CASES])
async def test_gold_case(session: AsyncSession, case: dict[str, Any]) -> None:
    """One gold-set entry, end to end.

    A failure here is a **disagreement**, not necessarily a bug. The build
    plan is specific about what to do with one: *"sit with a clinician, walk
    every disagreement, tune thresholds, not code."* A rule edited to satisfy
    one case is a rule nobody can predict.
    """
    result_id = await _stage(session, case)
    classification = await classify_result(session, result_id)

    assert classification.severity == case["expected_severity"], (
        f"{case['id']}: expected {case['expected_severity']}, "
        f"got {classification.severity} — {case['description']}"
    )

    expected_reasons = case.get("expected_reason_codes")
    if expected_reasons:
        actual = {output.reason_code for output in classification.rule_outputs}
        missing = set(expected_reasons) - actual
        assert (
            not missing
        ), f"{case['id']}: reason codes {missing} not emitted ({actual})"

    if "expected_auto_close" in case:
        assert (
            classification.auto_closed == case["expected_auto_close"]
        ), f"{case['id']}: auto_close expected {case['expected_auto_close']}"


# ── the honesty guards ────────────────────────────────────────────────


def test_the_gold_set_does_not_claim_clinician_validation() -> None:
    """Exit Gate 3 needs a clinician. Nothing in this repository is one.

    Flipping ``clinician_validated`` to true is a one-character edit and
    booking a clinician is not, so the flag on its own is not accepted: a
    named reviewer and a date have to come with it, and every case has to
    stop being marked synthetic.
    """
    if not META["clinician_validated"]:
        # The honest current state.
        assert META["reviewed_by"] is None
        assert META["reviewed_on"] is None
        assert all(c["provenance"] == "synthetic" for c in CASES)
        return

    # If somebody has flipped it, these are the things that must also be true.
    assert META["reviewed_by"], "clinician_validated needs a named reviewer"
    assert META["reviewed_on"], "clinician_validated needs a date"
    synthetic = [c["id"] for c in CASES if c["provenance"] == "synthetic"]
    assert not synthetic, (
        "these cases are still marked synthetic and cannot be part of a "
        f"clinician-validated set: {synthetic}"
    )


def test_no_agreement_rate_is_reported_for_synthetic_cases() -> None:
    """Why this suite prints a pass count and never a percentage.

    *"Gold set passes at ≥95% agreement with the clinician"* is a claim about
    a clinician's judgement. A percentage computed from engineering
    expectations would look identical in a report and mean nothing — so no
    code here computes one while the set is synthetic.
    """
    if META["clinician_validated"]:
        pytest.skip("the set is clinician-validated; an agreement rate is meaningful")

    # Parsed, not grepped: this test's own prose says the forbidden words, and
    # a substring search would flag the warning as the violation.
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))

    # Names as *used*, not as defined: this test's own name says the words it
    # forbids, and a function nobody calls computes nothing. Anything that
    # actually produces a proportion has to be referenced somewhere, and a
    # reference is a Name or an Attribute.
    named: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            named.append(node.id)
        elif isinstance(node, ast.Attribute):
            named.append(node.attr)
    offenders = [n for n in named if "rate" in n.lower() or "pct" in n.lower()]
    assert not offenders, (
        f"{offenders} looks like an agreement rate computed from synthetic "
        "expectations — that number would be read as clinical validation"
    )

    def _is_path_join(node: ast.BinOp) -> bool:
        # pathlib overloads `/`, and the fixture path is built with it.
        return isinstance(node.right, ast.Constant) and isinstance(
            node.right.value, str
        )

    divisions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Div)
        and not _is_path_join(node)
    ]
    assert not divisions, (
        "a division in this module is almost certainly a proportion of the "
        "gold set, and a proportion here reads as clinical agreement"
    )


def test_the_gold_set_covers_all_three_rules() -> None:
    """*"spanning all three rule types"*. The one part of 3.8's shape this
    file can satisfy without a clinician."""
    rules = {case["rule"] for case in CASES}
    assert {"A", "B", "C"} <= rules
    assert "mixed" in rules, "a report is often more than one rule at once"


def test_the_exit_gate_cases_are_present_and_named() -> None:
    """The three cases Exit Gate 3 names by hand, kept findable."""
    by_id = {case["id"]: case for case in CASES}

    resistant = by_id["B01"]
    assert resistant["expected_severity"] == "critical"

    negated = by_id["C01"]
    assert negated["expected_severity"] != "critical"
    assert (
        "no evidence of malignancy"
        in negated["content"]["narratives"][0]["text"].lower()
    )

    contaminant = by_id["B05"]
    assert contaminant["expected_severity"] == "follow_up"
    assert contaminant["expected_auto_close"] is False


def test_the_set_is_short_of_the_hundred_real_results_the_plan_asks_for() -> None:
    """Stated as a test so the gap cannot quietly stop being mentioned.

    3.8 asks for **100+ real anonymised results**. Padding to 100 with more
    synthetic cases would satisfy a number and nothing else, so the set is
    the size it needs to be to cover the rules, and the shortfall is recorded
    here and in docs/clinical-validation.md.
    """
    real = [c for c in CASES if c["provenance"] != "synthetic"]
    if len(real) >= 100:
        return
    assert len(real) == 0, (
        "partially real gold sets are fine, but update this test and "
        "docs/clinical-validation.md to say how many real results there are"
    )
