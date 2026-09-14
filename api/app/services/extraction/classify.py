"""What kind of report is this? Phase 7.1.

> **Rules first.** Header keywords, lab name, section titles, the presence of a
> sensitivity grid. The model on NODE B is **a fallback for unmatched documents
> only**.
>
> **If NODE B is unreachable, unmatched documents go to `needs_review`, never to
> a guess.**

That last line is RULE 2 applied to classification, and it is why this module
contains no network call. The rules run on NODE A, on text we already have, and
they either reach a confident answer or they do not. The LLM fallback is invoked
by the *caller* — so losing NODE B removes a tier from the cascade instead of
removing classification.

## Why scoring rather than first-match-wins

A microbiology report mentions "serum" and a biochemistry report mentions
"culture" in a comment. First-match-wins makes the answer depend on which rule
happens to be checked first, which is not a property anyone can reason about.
Scoring lets strong signals — a sensitivity grid, a section called IMPRESSION —
outweigh an incidental word, and makes "why did it decide that" answerable by
reading the evidence back.
"""

from __future__ import annotations

import dataclasses
import re

REPORT_BIOCHEMISTRY = "biochemistry"
REPORT_HAEMATOLOGY = "haematology"
REPORT_MICROBIOLOGY = "microbiology"
REPORT_RADIOLOGY = "radiology"
REPORT_HISTOPATHOLOGY = "histopathology"
REPORT_UNKNOWN = "unknown"

#: Below this, the document goes to review. 7.1: *"Below threshold →
#: `needs_review`"* — a low-confidence guess is worth less than an honest
#: "I don't know", because the guess routes a report to the wrong parser and
#: the failure then looks like an extraction bug.
CONFIDENCE_THRESHOLD = 0.60


@dataclasses.dataclass(frozen=True)
class Signal:
    pattern: re.Pattern[str]
    report_type: str
    weight: float
    #: What this signal means, in words, for the evidence record.
    label: str


def _sig(regex: str, report_type: str, weight: float, label: str) -> Signal:
    return Signal(re.compile(regex, re.I), report_type, weight, label)


#: Strong signals are *structural* — a sensitivity grid, a named section — and
#: weak ones are vocabulary. Structure survives a lab changing its wording.
SIGNALS: tuple[Signal, ...] = (
    # ── microbiology: the sensitivity grid is close to conclusive ──────
    _sig(
        r"\b(antibiotic|antimicrobial)\s+(sensitivit|susceptibilit)",
        REPORT_MICROBIOLOGY,
        3.0,
        "sensitivity grid heading",
    ),
    _sig(
        r"\b[SIR]\s*=\s*(sensitive|intermediate|resistant)",
        REPORT_MICROBIOLOGY,
        3.0,
        "S/I/R legend",
    ),
    _sig(r"\bcolony\s+count\b", REPORT_MICROBIOLOGY, 2.0, "colony count"),
    _sig(r"\borganism\s+isolated\b", REPORT_MICROBIOLOGY, 2.0, "organism isolated"),
    _sig(
        r"\bculture\s*(&|and)?\s*sensitivity\b",
        REPORT_MICROBIOLOGY,
        2.5,
        "culture and sensitivity",
    ),
    _sig(r"\bno\s+growth\b", REPORT_MICROBIOLOGY, 2.0, "no growth"),
    # ── radiology ─────────────────────────────────────────────────────
    _sig(
        r"^\s*(impression|findings)\s*[:\-]?\s*$",
        REPORT_RADIOLOGY,
        2.0,
        "IMPRESSION/FINDINGS section",
    ),
    _sig(
        r"\b(x-?ray|radiograph|ultrasound|sonograph|ct\s+scan|mri|doppler|mammograph)\b",
        REPORT_RADIOLOGY,
        2.5,
        "modality named",
    ),
    _sig(r"\bcontrast\s+(enhanced|study)\b", REPORT_RADIOLOGY, 1.5, "contrast study"),
    _sig(r"\bradiologist\b", REPORT_RADIOLOGY, 1.5, "radiologist signature"),
    # ── histopathology ────────────────────────────────────────────────
    _sig(
        r"\b(histopatholog|cytolog|biops|specimen\s+received)\b",
        REPORT_HISTOPATHOLOGY,
        2.5,
        "histopathology vocabulary",
    ),
    _sig(
        r"^\s*(microscopy|macroscopy|gross)\s*[:\-]?\s*$",
        REPORT_HISTOPATHOLOGY,
        2.0,
        "microscopy/gross section",
    ),
    _sig(r"\bpathologist\b", REPORT_HISTOPATHOLOGY, 1.5, "pathologist signature"),
    # ── haematology ───────────────────────────────────────────────────
    _sig(
        r"\b(haemogram|hemogram|complete\s+blood\s+count|\bcbc\b)\b",
        REPORT_HAEMATOLOGY,
        3.0,
        "CBC heading",
    ),
    _sig(r"\b(haemoglobin|hemoglobin)\b", REPORT_HAEMATOLOGY, 1.5, "haemoglobin"),
    _sig(
        r"\b(platelet|leucocyte|leukocyte|neutrophil|lymphocyte)\b",
        REPORT_HAEMATOLOGY,
        1.0,
        "blood cell analyte",
    ),
    _sig(
        r"\b(differential\s+count|peripheral\s+smear)\b",
        REPORT_HAEMATOLOGY,
        2.0,
        "differential/smear",
    ),
    # ── biochemistry ──────────────────────────────────────────────────
    _sig(
        r"\b(biochemistr|clinical\s+chemistr)\b",
        REPORT_BIOCHEMISTRY,
        3.0,
        "biochemistry heading",
    ),
    _sig(
        r"\b(creatinine|urea|sodium|potassium|bilirubin|albumin)\b",
        REPORT_BIOCHEMISTRY,
        1.0,
        "chemistry analyte",
    ),
    _sig(
        r"\b(liver|renal|lipid|thyroid)\s+(function|profile)\b",
        REPORT_BIOCHEMISTRY,
        2.5,
        "named profile",
    ),
    _sig(
        r"\b(fasting|random)\s+(blood\s+)?(sugar|glucose)\b",
        REPORT_BIOCHEMISTRY,
        2.0,
        "glucose panel",
    ),
)


@dataclasses.dataclass(frozen=True)
class Classification:
    report_type: str
    confidence: float
    method: str
    evidence: dict[str, object]

    @property
    def needs_review(self) -> bool:
        return (
            self.report_type == REPORT_UNKNOWN or self.confidence < CONFIDENCE_THRESHOLD
        )


def classify_text(text_layer: str | None) -> Classification:
    """Score a document's text against the signal table. No network, no model.

    Confidence is the winner's share of the total score, not its raw weight — a
    report that matches microbiology at 3.0 *and* biochemistry at 3.0 is genuinely
    ambiguous, and a share-based score says so (0.5) where a raw one would not.
    """
    if not text_layer or not text_layer.strip():
        return Classification(
            report_type=REPORT_UNKNOWN,
            confidence=0.0,
            method="rule",
            evidence={"reason": "no text layer"},
        )

    scores: dict[str, float] = {}
    hits: dict[str, list[str]] = {}
    for signal in SIGNALS:
        if signal.pattern.search(text_layer):
            scores[signal.report_type] = (
                scores.get(signal.report_type, 0.0) + signal.weight
            )
            hits.setdefault(signal.report_type, []).append(signal.label)

    if not scores:
        return Classification(
            report_type=REPORT_UNKNOWN,
            confidence=0.0,
            method="rule",
            evidence={"reason": "no signal matched"},
        )

    total = sum(scores.values())
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best_score = ranked[0]

    # ★ The same tie rule as 7.5 and 7.4. Two report types scoring identically
    # is not a question the rules can answer, and choosing one sends the
    # document to a parser built for something else.
    if len(ranked) > 1 and ranked[1][1] == best_score:
        return Classification(
            report_type=REPORT_UNKNOWN,
            confidence=0.0,
            method="rule",
            evidence={
                "reason": "two report types scored equally",
                "tied": [r[0] for r in ranked if r[1] == best_score],
                "hits": hits,
            },
        )

    return Classification(
        report_type=best_type,
        confidence=round(best_score / total, 3),
        method="rule",
        evidence={"scores": scores, "hits": hits.get(best_type, [])},
    )


def unknown_because_node_b_is_down() -> Classification:
    """What the caller records when the rules were unsure and NODE B is absent.

    7.1: *"If NODE B is unreachable, unmatched documents go to `needs_review`,
    never to a guess."* Kept as a named constructor so the reason survives into
    the database — "we could not ask" and "we asked and it did not know" are
    different facts, and only one of them is worth retrying later.
    """
    return Classification(
        report_type=REPORT_UNKNOWN,
        confidence=0.0,
        method="rule",
        evidence={
            "reason": "rules were not confident and NODE B was unreachable",
            "retryable": True,
        },
    )
