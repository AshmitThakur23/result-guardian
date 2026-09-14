"""Deciding which pending case a result belongs to. Phase 7.5 ★

> **AI never decides which patient a result belongs to. This is scoring
> arithmetic and thresholds. A wrong match puts a result on a stranger's file.**
>
> **Wrong-patient matching must be zero. Prefer the review queue every time.**

There is no model in this file and there must never be one. Every decision here
is arithmetic over fields that either match or do not, and the thresholds are
read from configuration rather than compiled in.

## The asymmetry that shapes every decision below

The two ways to be wrong are **not** equally bad:

* **A missed auto-match** costs a human thirty seconds in the review queue.
* **A wrong auto-match** puts a patient's result on another patient's file,
  where it may be acted on. There is no recovery from that; the clinician has
  no reason to doubt what they are shown.

So every ambiguity resolves *towards* the queue. A tie between two candidates is
never broken — not by recency, not by row order, not by "the higher one wins".
It goes to a person, because the one thing the arithmetic cannot know is which
of two equally-scoring patients is the right one.

## Why ties are refused rather than sorted

A sort always produces a winner. That is the problem: given two candidates
scoring 0.90, *any* tie-break invents a distinction the evidence does not
support, and it will be right about half the time. Half of "which patient is
this" is not a number this product may ship.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import decimal
import uuid
from typing import Any

# ── thresholds ────────────────────────────────────────────────────────
#
# 7.5's table, as written. These are *defaults*: `match_result` takes them as
# arguments so an admin-editable configuration row can override them without a
# code change, per the standing rule that configuration lives in tables.

#: Scores at or above this may match without a human.
DEFAULT_AUTO_THRESHOLD = decimal.Decimal("0.90")
#: Below `auto` and at or above this: the review queue, with candidates ranked.
DEFAULT_REVIEW_THRESHOLD = decimal.Decimal("0.70")

#: 7.5's signal table. Ordered strongest first; the first signal that applies to
#: a candidate is the one that scores it, so a weaker coincidence cannot add to
#: a stronger identifier.
SIGNAL_ORDER_ID_EXACT = ("order_id_exact", decimal.Decimal("1.00"))
SIGNAL_MRN_TEST_DATE = ("mrn_test_date", decimal.Decimal("0.90"))
SIGNAL_MRN_LOINC_DATE = ("mrn_loinc_date", decimal.Decimal("0.85"))
SIGNAL_NAME_DOB_TEST_DATE = ("name_dob_test_date", decimal.Decimal("0.70"))

OUTCOME_AUTO = "auto_matched"
OUTCOME_REVIEW = "needs_review"
OUTCOME_UNMATCHED = "unmatched"
OUTCOME_ORPHAN = "orphan"

#: Trigram similarity a name must clear before it counts at all. Below this the
#: name signal does not fire, so two different people with a shared surname
#: cannot reach the review queue on a name alone.
NAME_SIMILARITY_FLOOR = 0.85


@dataclasses.dataclass(frozen=True)
class IncomingResult:
    """What arrived, normalised. Every field is optional — reports are messy."""

    external_order_id: str | None = None
    mrn: str | None = None
    patient_name: str | None = None
    date_of_birth: dt.date | None = None
    test_code: str | None = None
    loinc_code: str | None = None
    collected_at: dt.date | None = None


@dataclasses.dataclass(frozen=True)
class CandidateCase:
    """An open case this result might belong to."""

    case_id: uuid.UUID
    order_id: uuid.UUID | None = None
    external_order_id: str | None = None
    mrn: str | None = None
    patient_name: str | None = None
    date_of_birth: dt.date | None = None
    test_code: str | None = None
    loinc_code: str | None = None
    expected_collected_at: dt.date | None = None
    #: Trigram similarity of the names, computed in SQL by the repository.
    #: ``None`` when it was not computed, which is not the same as 0.0.
    name_similarity: float | None = None


@dataclasses.dataclass(frozen=True)
class ScoredCandidate:
    case_id: uuid.UUID
    score: decimal.Decimal
    method: str
    #: Which fields actually agreed. Written to `match_decisions.candidates` so a
    #: near-miss can be read back months later.
    evidence: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class MatchOutcome:
    outcome: str
    chosen_case_id: uuid.UUID | None
    score: decimal.Decimal
    method: str
    candidates: list[ScoredCandidate]
    #: Why, in a sentence a reviewer can read. Never a stack trace.
    reason: str


def _same_day(a: dt.date | None, b: dt.date | None, *, tolerance_days: int) -> bool:
    """Dates agree within a tolerance. A missing date never agrees.

    ``None`` returning False is deliberate. An absent collection date is an
    absence of evidence, and treating it as a match would let a result with no
    date at all score against every case for that patient.
    """
    if a is None or b is None:
        return False
    return abs((a - b).days) <= tolerance_days


def _norm(value: str | None) -> str | None:
    """Casefold and strip. MRNs are printed inconsistently across labs."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned.casefold() if cleaned else None


def score_candidate(
    incoming: IncomingResult, candidate: CandidateCase
) -> ScoredCandidate | None:
    """Score one candidate against 7.5's signal table.

    Returns ``None`` when no signal fires at all — an unscored candidate is not
    a zero-scoring one, and keeping them out of the list stops the review queue
    filling with cases that share nothing with the result.

    **The first signal that applies wins.** Signals are not summed: two weak
    coincidences are still two weak coincidences, and adding them would let
    ``name + date`` creep over the auto-match threshold without an identifier
    ever agreeing.
    """
    # ── 1.00 — the accession number. The only signal strong enough alone. ──
    order_in = _norm(incoming.external_order_id)
    order_cand = _norm(candidate.external_order_id)
    if order_in and order_cand and order_in == order_cand:
        method, score = SIGNAL_ORDER_ID_EXACT
        return ScoredCandidate(
            case_id=candidate.case_id,
            score=score,
            method=method,
            evidence={"external_order_id": incoming.external_order_id},
        )

    mrn_in = _norm(incoming.mrn)
    mrn_cand = _norm(candidate.mrn)
    mrn_agrees = bool(mrn_in and mrn_cand and mrn_in == mrn_cand)

    # ── 0.90 — MRN + test code + same-day collection ──────────────────
    if mrn_agrees:
        test_in = _norm(incoming.test_code)
        test_cand = _norm(candidate.test_code)
        if (
            test_in
            and test_cand
            and test_in == test_cand
            and _same_day(
                incoming.collected_at, candidate.expected_collected_at, tolerance_days=0
            )
        ):
            method, score = SIGNAL_MRN_TEST_DATE
            return ScoredCandidate(
                case_id=candidate.case_id,
                score=score,
                method=method,
                evidence={
                    "mrn": incoming.mrn,
                    "test_code": incoming.test_code,
                    "collected_at": str(incoming.collected_at),
                },
            )

        # ── 0.85 — MRN + LOINC + date ±1 day ──────────────────────────
        loinc_in = _norm(incoming.loinc_code)
        loinc_cand = _norm(candidate.loinc_code)
        if (
            loinc_in
            and loinc_cand
            and loinc_in == loinc_cand
            and _same_day(
                incoming.collected_at, candidate.expected_collected_at, tolerance_days=1
            )
        ):
            method, score = SIGNAL_MRN_LOINC_DATE
            return ScoredCandidate(
                case_id=candidate.case_id,
                score=score,
                method=method,
                evidence={
                    "mrn": incoming.mrn,
                    "loinc_code": incoming.loinc_code,
                    "collected_at": str(incoming.collected_at),
                },
            )

    # ── 0.70 — name similarity + DOB + test + date ±2 days ────────────
    #
    # Never enough to auto-match, by construction: 0.70 is the review floor, so
    # this signal can only ever put a case in front of a person. That is the
    # intent — a name and a birthday are not an identifier.
    similarity = candidate.name_similarity
    if (
        similarity is not None
        and similarity >= NAME_SIMILARITY_FLOOR
        and incoming.date_of_birth is not None
        and incoming.date_of_birth == candidate.date_of_birth
        and _norm(incoming.test_code)
        and _norm(incoming.test_code) == _norm(candidate.test_code)
        and _same_day(
            incoming.collected_at, candidate.expected_collected_at, tolerance_days=2
        )
    ):
        method, score = SIGNAL_NAME_DOB_TEST_DATE
        return ScoredCandidate(
            case_id=candidate.case_id,
            score=score,
            method=method,
            evidence={
                "name_similarity": round(similarity, 3),
                "date_of_birth": str(incoming.date_of_birth),
                "test_code": incoming.test_code,
            },
        )

    return None


def match_result(
    incoming: IncomingResult,
    candidates: list[CandidateCase],
    *,
    auto_threshold: decimal.Decimal = DEFAULT_AUTO_THRESHOLD,
    review_threshold: decimal.Decimal = DEFAULT_REVIEW_THRESHOLD,
) -> MatchOutcome:
    """Decide. Pure arithmetic, no I/O, no model.

    Being a pure function is not tidiness: it means the decision can be replayed
    from a stored `match_decisions` row and produce the same answer, which is
    what makes a wrong match investigable rather than merely regrettable.
    """
    scored = [
        s for s in (score_candidate(incoming, c) for c in candidates) if s is not None
    ]

    if not scored:
        return MatchOutcome(
            outcome=OUTCOME_UNMATCHED,
            chosen_case_id=None,
            score=decimal.Decimal("0.000"),
            method="none",
            candidates=[],
            reason=(
                "No open case shares an accession number, MRN or name with this "
                "report. It needs a person to say who it belongs to."
            ),
        )

    # Sort for *presentation* only. The decision below never relies on which of
    # two equal scores sorted first.
    scored.sort(key=lambda s: s.score, reverse=True)
    best = scored[0]
    tied = [s for s in scored if s.score == best.score]

    # ★ The tie rule. A sort always produces a winner, and that is exactly the
    # danger: with two candidates at 0.90, any tie-break invents a distinction
    # the evidence does not support and is right about half the time.
    if len(tied) > 1:
        return MatchOutcome(
            outcome=OUTCOME_REVIEW,
            chosen_case_id=None,
            score=best.score,
            method=best.method,
            candidates=scored,
            reason=(
                f"{len(tied)} open cases match this report equally well. "
                "Only a person can say which patient it belongs to."
            ),
        )

    if best.score >= auto_threshold:
        return MatchOutcome(
            outcome=OUTCOME_AUTO,
            chosen_case_id=best.case_id,
            score=best.score,
            method=best.method,
            candidates=scored,
            reason=f"Matched on {best.method.replace('_', ' ')}.",
        )

    if best.score >= review_threshold:
        return MatchOutcome(
            outcome=OUTCOME_REVIEW,
            chosen_case_id=None,
            score=best.score,
            method=best.method,
            candidates=scored,
            reason=(
                "This report probably belongs to one of the cases below, but not "
                "certainly enough to file it without a person looking."
            ),
        )

    return MatchOutcome(
        outcome=OUTCOME_UNMATCHED,
        chosen_case_id=None,
        score=best.score,
        method=best.method,
        candidates=scored,
        reason=(
            "Nothing matched this report closely enough to suggest a case. "
            "It needs a person to say who it belongs to."
        ),
    )


def candidates_as_json(scored: list[ScoredCandidate]) -> list[dict[str, Any]]:
    """Shape for `match_decisions.candidates`.

    Stored for **every** outcome, including the ones that matched cleanly. The
    near-misses are the interesting rows when a match later turns out to be
    wrong: they show what else was in contention and by how much it lost.
    """
    return [
        {
            "case_id": str(s.case_id),
            "score": str(s.score),
            "method": s.method,
            "evidence": s.evidence,
        }
        for s in scored
    ]
