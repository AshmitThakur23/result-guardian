"""Ingestion thresholds, resolved from ``system_settings``. Phase 6.

CLAUDE.md: *"Configuration lives in tables, never in code. Thresholds …
an admin must be able to edit them. Hardcoding any of these turns the product
back into a demo."*

Three numbers decide how ingestion behaves, and all three are the kind a real
deployment discovers it needs to move:

* **``ocr_min_confidence``** — below this a scanned page goes to
  ``needs_review`` rather than being believed. 6.4 fixes the default at 0.70.
* **``scanned_char_density``** — characters per 1000 pt² below which a page is
  treated as scanned. 6.2 states the rule but not the number, because the
  number depends on what the local labs print.
* **``ingest_timeout_s``** — 6.5's per-document budget, default 120.

The defaults here are the plan's. The table overrides them, so a hospital that
finds 0.70 rejects too much of its own paperwork can move it — and the move is
audited, because ``admin.py`` pairs every write with an audit row.

⚠️ **Raising ``ocr_min_confidence`` is safe; lowering it is a clinical
decision.** Below the threshold the OCR is guessing, and a guessed lab value
is worse than an honest gap: the gap goes to a human, the guess goes to a
chart.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import settings_store

KEY_OCR_MIN_CONFIDENCE = "ocr_min_confidence"
KEY_SCANNED_CHAR_DENSITY = "scanned_char_density"
KEY_INGEST_TIMEOUT_S = "ingest_timeout_s"

# 6.4: "page mean confidence < 0.70 → route to `needs_review` instead of
# guessing".
DEFAULT_OCR_MIN_CONFIDENCE = 0.70

# Characters per 1000 pt². A4 is ~501,000 pt², so 0.1 means a page needs
# roughly **50 extractable characters** to count as native text.
#
# ⚠️ This started at 0.5 (~250 characters) and was **wrong**, caught by
# `test_native_pdf_is_not_scanned`. A short real report — "HIV 1 & 2
# antibody: Non-reactive" under a letterhead — is well under 250 characters,
# and 0.5 sent it down the OCR path even though it had a perfect text layer.
#
# 50 is where the two populations actually separate: an image-only page has
# **zero** extractable characters, and the worst case in between is a scanner
# stamping "Scanned by CamScanner" plus a page number onto an image — about
# 25. Below 50 there is no report; above it there is text worth reading.
#
# Which way this errs matters. Too low and a scanned page is called native:
# it yields no text, and the pipeline routes it to `needs_review` anyway, so
# a human still sees it. Too high and a native page is called scanned: a
# perfectly good text layer is thrown away and replaced with an OCR guess.
# The second is the one that corrupts data, so the threshold errs low.
DEFAULT_SCANNED_CHAR_DENSITY = 0.1

# 6.5: "Timeout per document (**default 120s**), then fail cleanly."
DEFAULT_INGEST_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class IngestSettings:
    ocr_min_confidence: float
    scanned_char_density: float
    ingest_timeout_s: float


async def load(session: AsyncSession) -> IngestSettings:
    """Read all three at once. One cache hit, not three."""
    return IngestSettings(
        ocr_min_confidence=float(
            await settings_store.get(
                session, KEY_OCR_MIN_CONFIDENCE, default=DEFAULT_OCR_MIN_CONFIDENCE
            )
        ),
        scanned_char_density=float(
            await settings_store.get(
                session, KEY_SCANNED_CHAR_DENSITY, default=DEFAULT_SCANNED_CHAR_DENSITY
            )
        ),
        ingest_timeout_s=float(
            await settings_store.get(
                session, KEY_INGEST_TIMEOUT_S, default=DEFAULT_INGEST_TIMEOUT_S
            )
        ),
    )
