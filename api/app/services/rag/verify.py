"""Refusing citations the source does not contain. Phase 8.5 ★

    for each evidence item:
        chunk = fetch(chunk_id) → missing? reject
        normalise whitespace on both sides
        quoted_span in chunk.text? → no? try fuzzy ratio >= 0.95
        still no? → reject this evidence item

    if zero evidence items survive → reject the whole response

> **Target: zero unverified citations ever displayed.**

## There is no AI in this file, and there must never be

This is the guard, and a guard implemented with the thing it guards against is
not a guard. Every decision here is string comparison: the quote is either in
the chunk or it is not. That is why 8.5 says *"plain code, no AI"*, and why this
module imports nothing that can reach NODE B.

## Why this exists, in one measured example

Asked *"what is a critical potassium level?"*, `qwen3:4b` answered **"6.0 mEq/L
or higher"** — fluent, confident, clinically plausible, and **from no source this
system holds**. Measured on 2026-09-14 against the live NODE B. A model is a
phrasing engine over retrieved text; left unchecked it will phrase things that
were never retrieved, and a clinician has no way to tell the difference.

## Why 0.95 and not lower

The fuzzy threshold exists for *whitespace and punctuation drift* — a model
re-typing a quote with a different dash, or collapsing a line break. It does not
exist to accept paraphrase. At 0.95 a quote may differ by roughly one character
in twenty, which covers re-typing and excludes rewriting. Lower it and the
verifier starts approving sentences the source does not contain, which is the
one thing it is for.

## Why a rejection is logged rather than silently dropped

`ai_rejections` is the **hallucination rate metric**. A verifier that quietly
discards bad citations is indistinguishable from a model that never produces
any, and those are very different systems to be running in a hospital.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 8.5's threshold. See the module docstring — this is a typo allowance, not a
#: paraphrase allowance.
FUZZY_THRESHOLD = 0.95

#: A quote shorter than this is not evidence of anything. "the" appears in every
#: document ever written, and would verify against all of them.
MIN_QUOTE_CHARS = 20

REASON_CHUNK_MISSING = "chunk_missing"
REASON_QUOTE_NOT_FOUND = "quote_not_found"
REASON_FUZZY_BELOW = "quote_fuzzy_below_threshold"
REASON_NO_EVIDENCE = "no_evidence_items"
REASON_MALFORMED = "malformed_response"

_WHITESPACE = re.compile(r"\s+")


def normalise(value: str) -> str:
    """Collapse whitespace and fold the punctuation a model re-types differently.

    A PDF gives ``"organism–specific"`` with an en dash; a model returns a
    hyphen. Both mean the same thing and only one of them is in the source, so
    the comparison folds them rather than failing on typography.

    **Case is deliberately preserved.** A quote that differs only in case is
    still a re-typing rather than a citation, and this is the last check before
    text reaches a clinician — it should be strict where it can afford to be.
    """
    folded = (
        value.replace("–", "-")
        .replace("—", "-")
        .replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace(" ", " ")
    )
    return _WHITESPACE.sub(" ", folded).strip()


@dataclasses.dataclass(frozen=True)
class EvidenceItem:
    """One citation the model offered."""

    chunk_id: uuid.UUID
    quoted_text: str


@dataclasses.dataclass(frozen=True)
class VerifiedEvidence:
    chunk_id: uuid.UUID
    quoted_text: str
    #: Where the quote sits inside the chunk, so the UI can highlight it rather
    #: than asking the reader to find it.
    char_start: int
    char_end: int
    #: 1.0 for an exact substring match; the ratio for a fuzzy one.
    ratio: float
    document_title: str
    section_path: str | None
    page_no: int | None


@dataclasses.dataclass(frozen=True)
class Rejection:
    chunk_id: uuid.UUID | None
    quoted_text: str | None
    reason: str
    best_ratio: float | None = None


@dataclasses.dataclass(frozen=True)
class VerificationResult:
    verified: list[VerifiedEvidence]
    rejections: list[Rejection]

    @property
    def accepted(self) -> bool:
        """★ Zero surviving evidence items rejects the **whole** response.

        Not "show it with fewer citations". An explanation whose every citation
        failed verification is a paragraph of confident prose with nothing
        behind it, which is precisely the thing that must never reach a
        clinician.
        """
        return bool(self.verified)


def _locate(quote: str, chunk_text: str) -> tuple[int, int, float] | None:
    """Find ``quote`` in ``chunk_text``. Exact first, then fuzzy.

    Returns offsets **into the normalised chunk**, which is what the UI
    highlights against. Returning raw-text offsets would be wrong: normalisation
    changes lengths, so an offset computed on one string and applied to the
    other points at the wrong characters.
    """
    n_quote = normalise(quote)
    n_chunk = normalise(chunk_text)

    if len(n_quote) < MIN_QUOTE_CHARS:
        return None

    at = n_chunk.find(n_quote)
    if at >= 0:
        return (at, at + len(n_quote), 1.0)

    # Fuzzy, over windows the length of the quote. `difflib` alone would compare
    # the quote against the *whole* chunk and score low simply because the chunk
    # is longer, so the comparison has to be window-by-window.
    best_ratio = 0.0
    best_at = -1
    window = len(n_quote)
    if window > len(n_chunk):
        return None

    matcher = difflib.SequenceMatcher(autojunk=False, b=n_quote)
    # Step by a quarter of the window: fine enough not to straddle a match,
    # coarse enough not to be quadratic on a long chunk.
    step = max(1, window // 4)
    for start in range(0, len(n_chunk) - window + 1, step):
        matcher.set_seq1(n_chunk[start : start + window])
        ratio = matcher.quick_ratio()
        if ratio < best_ratio:
            continue
        exact = matcher.ratio()
        if exact > best_ratio:
            best_ratio, best_at = exact, start

    if best_ratio >= FUZZY_THRESHOLD and best_at >= 0:
        return (best_at, best_at + window, best_ratio)
    return None


async def verify_evidence(
    session: AsyncSession,
    items: list[EvidenceItem],
    *,
    case_id: uuid.UUID | None = None,
) -> VerificationResult:
    """Check every citation against the chunk it claims to come from.

    Rejections are **written to `ai_rejections` before returning**, including
    when every item fails. The metric must count the responses that were thrown
    away, or it measures only the ones that happened to survive.
    """
    verified: list[VerifiedEvidence] = []
    rejections: list[Rejection] = []

    if not items:
        rejections.append(
            Rejection(chunk_id=None, quoted_text=None, reason=REASON_NO_EVIDENCE)
        )

    for item in items:
        row = (
            await session.execute(
                text(
                    "SELECT c.id, c.chunk_text, c.section_path, c.page_no, "
                    "       d.title "
                    "  FROM kb_chunks c "
                    "  JOIN kb_documents d ON d.id = c.kb_document_id "
                    " WHERE c.id = :id AND d.deleted_at IS NULL "
                    # ★ Approval is enforced here too, not only at retrieval.
                    # A chunk cited from a document whose approval was revoked
                    # between retrieval and verification must not be shown.
                    "   AND d.approved_at IS NOT NULL"
                ),
                {"id": str(item.chunk_id)},
            )
        ).first()

        if row is None:
            rejections.append(
                Rejection(
                    chunk_id=item.chunk_id,
                    quoted_text=item.quoted_text,
                    reason=REASON_CHUNK_MISSING,
                )
            )
            continue

        located = _locate(item.quoted_text, row.chunk_text)
        if located is None:
            # Recompute the best ratio for the log even when it failed: a
            # threshold can only be tuned against real near-misses, and "it was
            # rejected" without "by how much" is not tunable.
            n_quote = normalise(item.quoted_text)
            n_chunk = normalise(row.chunk_text)
            ratio = (
                difflib.SequenceMatcher(None, n_quote, n_chunk).quick_ratio()
                if n_quote
                else 0.0
            )
            rejections.append(
                Rejection(
                    chunk_id=item.chunk_id,
                    quoted_text=item.quoted_text,
                    reason=(
                        REASON_QUOTE_NOT_FOUND
                        if len(n_quote) < MIN_QUOTE_CHARS
                        else REASON_FUZZY_BELOW
                    ),
                    best_ratio=ratio,
                )
            )
            continue

        start, end, ratio = located
        verified.append(
            VerifiedEvidence(
                chunk_id=item.chunk_id,
                quoted_text=item.quoted_text,
                char_start=start,
                char_end=end,
                ratio=ratio,
                document_title=row.title,
                section_path=row.section_path,
                page_no=row.page_no,
            )
        )

    for rejection in rejections:
        await _log_rejection(session, rejection, case_id=case_id)

    return VerificationResult(verified=verified, rejections=rejections)


async def _log_rejection(
    session: AsyncSession, rejection: Rejection, *, case_id: uuid.UUID | None
) -> None:
    """One row per refused citation. The hallucination rate, made countable."""
    detail: dict[str, Any] = {"threshold": FUZZY_THRESHOLD}
    await session.execute(
        text(
            "INSERT INTO ai_rejections "
            "  (id, case_id, kb_chunk_id, reason, quoted_text, best_ratio, "
            "   detail, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :case, :chunk, :reason, :quote, "
            "        :ratio, CAST(:detail AS jsonb), now(), now())"
        ),
        {
            "case": str(case_id) if case_id else None,
            "chunk": str(rejection.chunk_id) if rejection.chunk_id else None,
            "reason": rejection.reason,
            "quote": rejection.quoted_text,
            "ratio": rejection.best_ratio,
            "detail": json.dumps(detail),
        },
    )
