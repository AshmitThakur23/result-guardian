"""Measuring extraction and matching against a labelled set. Phase 7.7.

> *Labelled gold set: **100 documents** with hand-written expected JSON.*
> *`make eval-extraction` reports per-field precision/recall and match accuracy.*
> ***Run on every PR touching extraction** — your only defence against silent
> regression.*

## 🔴 This harness has nothing to measure yet, and that is the honest state

Exit Gate 7 is measured on **100 labelled real documents**. The corpus stands at
**0** — the same missing corpus that holds Exit Gate 6 open. So this module is
built and tested against its own arithmetic, and will produce real numbers the
day real documents exist.

**It must never be fed synthetic documents to make a number appear.** A
generated PDF measures the generator, not the extractor: it contains exactly the
layouts the fixtures were written for, so it scores near-perfectly and proves
nothing. The build plan says synthetic reports are not gate evidence, and a
harness is the easiest place in the codebase to forget that.

## Why per-field, and why the critical fields are called out

An aggregate "94% accurate" hides which 6%. Getting a patient's name slightly
wrong costs nothing; getting a potassium value wrong can kill someone. So the
report is per-field, and :data:`CRITICAL_FIELDS` are reported separately so a
regression in a value can never be averaged away by a hundred correct units.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

#: Fields where an error is clinically dangerous rather than merely annoying.
#: Reported on their own so a regression here cannot hide inside an average.
CRITICAL_FIELDS = ("value", "unit", "test_name")


@dataclasses.dataclass(frozen=True)
class FieldScore:
    field: str
    #: Extracted and correct.
    true_positive: int = 0
    #: Extracted, but wrong or not expected. **The dangerous column.**
    false_positive: int = 0
    #: Expected and missed.
    false_negative: int = 0

    @property
    def precision(self) -> float | None:
        denominator = self.true_positive + self.false_positive
        return self.true_positive / denominator if denominator else None

    @property
    def recall(self) -> float | None:
        denominator = self.true_positive + self.false_negative
        return self.true_positive / denominator if denominator else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or (p + r) == 0:
            return None
        return 2 * p * r / (p + r)


@dataclasses.dataclass(frozen=True)
class EvaluationReport:
    documents: int
    fields: dict[str, FieldScore]
    #: 7.5's own gate clause, tracked separately from extraction accuracy.
    matches_correct: int = 0
    matches_total: int = 0
    #: ★ Exit Gate 7: *"ZERO incorrect auto-matches."* Not a rate — a count,
    #: because the only acceptable value is 0 and a percentage would let one
    #: wrong-patient match hide behind 199 right ones.
    wrong_auto_matches: int = 0

    @property
    def match_accuracy(self) -> float | None:
        return self.matches_correct / self.matches_total if self.matches_total else None

    @property
    def analyte_accuracy(self) -> float | None:
        """Micro-averaged F1 across the critical fields only."""
        scores = [self.fields[f].f1 for f in CRITICAL_FIELDS if f in self.fields]
        usable = [s for s in scores if s is not None]
        return sum(usable) / len(usable) if usable else None

    def gate_status(self) -> dict[str, Any]:
        """Exit Gate 7, answered honestly — including "not enough data".

        Returns ``passed: None`` rather than ``False`` when the corpus is too
        small to decide. **They are different answers**, and reporting an
        undecidable gate as a failure is as misleading as reporting it as a pass.
        """
        if self.documents < 100:
            return {
                "passed": None,
                "reason": (
                    f"Measured on {self.documents} documents. Exit Gate 7 is "
                    "defined over 100 labelled real documents, so this cannot "
                    "be decided yet."
                ),
                "documents": self.documents,
            }

        analyte = self.analyte_accuracy
        match = self.match_accuracy
        clauses = {
            "analyte_accuracy_at_least_95": analyte is not None and analyte >= 0.95,
            "match_accuracy_at_least_98": match is not None and match >= 0.98,
            # ★ Zero. Not "low", not "within tolerance".
            "zero_incorrect_auto_matches": self.wrong_auto_matches == 0,
        }
        return {
            "passed": all(clauses.values()),
            "clauses": clauses,
            "analyte_accuracy": analyte,
            "match_accuracy": match,
            "wrong_auto_matches": self.wrong_auto_matches,
            "documents": self.documents,
        }


def _compare(expected: Any, actual: Any) -> bool:
    """Equality, with the one comparison that needs care.

    Values are compared as **strings of their canonical form**, so ``9.20`` and
    ``9.2`` agree while ``<5`` and ``5`` do not. Censoring is part of the value,
    and an evaluator that treated them as equal would score a real extraction
    bug as a pass.
    """
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    return str(expected).strip() == str(actual).strip()


def score_documents(
    expected_docs: Sequence[dict[str, Any]],
    actual_docs: Sequence[dict[str, Any]],
) -> EvaluationReport:
    """Compare hand-written expectations against what extraction produced.

    Both sequences are keyed by ``document_id``; a document present in one and
    not the other counts as a miss rather than being skipped, or a parser that
    silently dropped half the corpus would score perfectly on the half it kept.
    """
    expected_by_id = {d["document_id"]: d for d in expected_docs}
    actual_by_id = {d["document_id"]: d for d in actual_docs}
    all_ids = set(expected_by_id) | set(actual_by_id)

    tallies: dict[str, dict[str, int]] = {}

    def bump(field: str, key: str) -> None:
        tallies.setdefault(field, {"tp": 0, "fp": 0, "fn": 0})[key] += 1

    matches_correct = 0
    matches_total = 0
    wrong_auto = 0

    for doc_id in all_ids:
        expected = expected_by_id.get(doc_id, {})
        actual = actual_by_id.get(doc_id, {})

        # ── analytes, keyed on the raw test name ──────────────────────
        exp_rows = {a.get("test_name"): a for a in expected.get("analytes", [])}
        act_rows = {a.get("test_name"): a for a in actual.get("analytes", [])}

        for name in set(exp_rows) | set(act_rows):
            exp_row = exp_rows.get(name)
            act_row = act_rows.get(name)
            if exp_row is None:
                # Extracted something that is not on the report.
                for field in CRITICAL_FIELDS:
                    bump(field, "fp")
                continue
            if act_row is None:
                for field in CRITICAL_FIELDS:
                    bump(field, "fn")
                continue
            for field in CRITICAL_FIELDS:
                if _compare(exp_row.get(field), act_row.get(field)):
                    bump(field, "tp")
                else:
                    # Wrong is counted as both: something was produced that
                    # should not have been, and something expected is missing.
                    bump(field, "fp")
                    bump(field, "fn")

        # ── matching (7.5) ────────────────────────────────────────────
        if "expected_case_id" in expected:
            matches_total += 1
            expected_case = expected.get("expected_case_id")
            actual_case = actual.get("matched_case_id")
            outcome = actual.get("match_outcome")
            if _compare(expected_case, actual_case):
                matches_correct += 1
            elif outcome == "auto_matched" and actual_case is not None:
                # ★ The clause that must be zero: it auto-matched, confidently,
                # to the wrong case. A queue entry is not this; a wrong file is.
                wrong_auto += 1

    fields = {
        field: FieldScore(
            field=field,
            true_positive=counts["tp"],
            false_positive=counts["fp"],
            false_negative=counts["fn"],
        )
        for field, counts in tallies.items()
    }

    return EvaluationReport(
        documents=len(all_ids),
        fields=fields,
        matches_correct=matches_correct,
        matches_total=matches_total,
        wrong_auto_matches=wrong_auto,
    )


def format_report(report: EvaluationReport) -> str:
    """A plain-text report for `make eval-extraction` and for a PR comment."""
    lines = [
        "Extraction evaluation",
        "=" * 60,
        f"documents: {report.documents}",
        "",
        f"{'field':<14}{'precision':>11}{'recall':>9}{'f1':>8}",
        "-" * 60,
    ]
    for name in sorted(report.fields):
        score = report.fields[name]

        def pct(v: float | None) -> str:
            return f"{v:.1%}" if v is not None else "   n/a"

        lines.append(
            f"{name:<14}{pct(score.precision):>11}{pct(score.recall):>9}"
            f"{pct(score.f1):>8}"
        )

    lines += ["", "-" * 60]
    accuracy = report.match_accuracy
    lines.append(
        f"match accuracy      : "
        f"{f'{accuracy:.1%}' if accuracy is not None else 'n/a'} "
        f"({report.matches_correct}/{report.matches_total})"
    )
    # Printed even when zero, and printed as a count. A reader must be able to
    # see that it was checked.
    lines.append(f"wrong auto-matches  : {report.wrong_auto_matches}   (gate: 0)")

    gate = report.gate_status()
    lines += ["", "Exit Gate 7"]
    if gate["passed"] is None:
        lines.append(f"  UNDECIDABLE — {gate['reason']}")
    else:
        lines.append(f"  {'PASSED' if gate['passed'] else 'FAILED'}")
        for clause, ok in gate["clauses"].items():
            lines.append(f"    {'PASS' if ok else 'FAIL'}  {clause}")
    return "\n".join(lines)
