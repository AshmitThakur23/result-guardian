"""Templates before models. Phase 7.2.

The order of attempts, and 7.2 is emphatic that it is an *order*:

1. **lab-specific template** — anchors and a field map from ``extraction_templates``
2. **generic table parser** — whatever structure the text layer still has
3. **LLM on NODE B** — strict JSON, temperature 0, **only for what 1 and 2 missed**
4. **human** — the review queue

> *Templates are boring, fast, free and reproducible. A hospital sends reports
> from 5–10 labs; **10 templates cover 90% of volume. Do not skip straight to
> the LLM.***

## Why the order is load-bearing and not a preference

Every tier down costs more and explains less. A template says *"the value was in
column 3 of the row matching this regex"* — checkable, and identical next
Tuesday. A model says *"0.87"*, cannot be re-derived, and costs 20 seconds of
NODE B. Running the model first would work, and would make every extraction in
the hospital unexplainable and slow for no gain on the 90% a template handles.

## What happens when NODE B is gone

> **NODE B down → attempts 1, 2 and 4 still run. Throughput drops, correctness
> does not.**

Tier 3 is the only one that needs the network, and it sits between two tiers
that do not. Losing it removes a tier; it does not break the cascade. That is
RULE 2 at the level of a single document, and :func:`extract` takes the model
callable as an **argument** so this module has no import path to a network
client at all — the structure enforces what the sentence promises.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.extraction.parse import (
    ParsedValue,
    ReferenceRange,
    parse_reference_range,
    parse_value,
)

TIER_TEMPLATE = "template"
TIER_GENERIC = "generic"
TIER_LLM = "llm"
TIER_HUMAN = "human"


@dataclasses.dataclass(frozen=True)
class ExtractedAnalyte:
    """One row off a report, with where it came from and how sure we are."""

    test_name_raw: str
    value: ParsedValue | None
    unit_raw: str | None
    reference: ReferenceRange | None
    abnormal_flag_from_lab: str | None
    #: 7.2: *"Every extracted field records `method` and `confidence`."*
    method: str
    confidence: float
    #: 7.3: *"Every field carries its `document_span_id`."* ``None`` only when
    #: the tier genuinely cannot say — a model's answer, for instance.
    document_span_id: str | None = None
    source_page: int | None = None


@dataclasses.dataclass(frozen=True)
class ExtractionResult:
    analytes: list[ExtractedAnalyte]
    tier: str
    #: Set when the cascade ran out of tiers. The document goes to a human.
    needs_human: bool = False
    reason: str | None = None


# ── tier 1: a lab's own template ──────────────────────────────────────


async def find_template(
    session: AsyncSession, *, lab_name: str | None, report_type: str, text_layer: str
) -> dict[str, Any] | None:
    """The newest active template whose anchors **all** match.

    *All*, not any: an anchor set is a fingerprint of one layout, and a template
    that matched on one line of a different lab's report would read values out
    of the wrong columns — which looks exactly like a lab reporting nonsense.
    """
    rows = (
        await session.execute(
            text(
                "SELECT id, lab_name, report_type, version, anchors, field_map "
                "  FROM extraction_templates "
                " WHERE deleted_at IS NULL AND is_active "
                "   AND report_type = :rt "
                # Cast required: compared only to NULL, Postgres cannot infer
                # the parameter's type and asyncpg raises AmbiguousParameterError
                # at prepare time -- a failure that appears only when lab_name is
                # absent, which is the common case for an unrecognised letterhead.
                # CAST rather than `::text`: SQLAlchemy's text() parameter
                # scanner reads `:lab::text` as the bind `lab` followed by
                # stray colons and leaves the first one unbound.
                "   AND (CAST(:lab AS text) IS NULL OR lab_name = :lab) "
                " ORDER BY version DESC"
            ),
            {"lab": lab_name, "rt": report_type},
        )
    ).fetchall()

    for row in rows:
        patterns = (row.anchors or {}).get("all", [])
        if not patterns:
            continue
        try:
            if all(re.search(p, text_layer, re.I | re.M) for p in patterns):
                return {
                    "id": str(row.id),
                    "lab_name": row.lab_name,
                    "version": row.version,
                    "field_map": row.field_map or {},
                }
        except re.error:
            # An admin typed a bad regex. Skip this template rather than taking
            # the whole cascade down -- and never treat a broken anchor as a
            # match, which would apply the wrong field map.
            continue
    return None


def apply_template(template: dict[str, Any], text_layer: str) -> list[ExtractedAnalyte]:
    """Read rows using the template's ``row_pattern`` and named groups.

    The field map is a regex with named groups — ``name``, ``value``, ``unit``,
    ``reference``, ``flag`` — so a lab's layout is data an admin can edit rather
    than code someone has to deploy. Configuration lives in tables.
    """
    row_pattern = template.get("field_map", {}).get("row_pattern")
    if not row_pattern:
        return []
    try:
        compiled = re.compile(row_pattern, re.I | re.M)
    except re.error:
        return []

    analytes: list[ExtractedAnalyte] = []
    for match in compiled.finditer(text_layer):
        groups = match.groupdict()
        name = (groups.get("name") or "").strip()
        if not name:
            continue
        analytes.append(
            ExtractedAnalyte(
                test_name_raw=name,
                value=parse_value(groups.get("value")),
                unit_raw=(groups.get("unit") or "").strip() or None,
                reference=parse_reference_range(groups.get("reference")),
                abnormal_flag_from_lab=(groups.get("flag") or "").strip() or None,
                method=TIER_TEMPLATE,
                # A template that matched its anchors is as good as this gets
                # without a human: the layout was recognised, not guessed.
                confidence=0.95,
            )
        )
    return analytes


# ── tier 2: the generic parser ────────────────────────────────────────

#: `Name   value   unit   range` with two or more spaces between columns, which
#: is what a PDF text layer leaves behind once a table's rules are gone.
_GENERIC_ROW = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9 ,.()/'\-]{2,60}?)\s{2,}"
    r"(?P<value>[<>≤≥]?\s*[\d.,]+|[A-Za-z ]{3,20}?)\s{2,}"
    r"(?P<unit>[^\s]{1,20})?\s*"
    r"(?P<reference>[\d.,]+\s*[-–]\s*[\d.,]+|[<>≤≥]\s*[\d.,]+)?\s*$",
    re.M,
)


def parse_generic(text_layer: str) -> list[ExtractedAnalyte]:
    """Read whatever column structure survived the PDF.

    Deliberately conservative. A row that does not look like ``name value unit``
    is skipped rather than coerced — the cost of skipping is one more row for a
    human, and the cost of coercing is a value attached to the wrong analyte.
    """
    analytes: list[ExtractedAnalyte] = []
    for match in _GENERIC_ROW.finditer(text_layer):
        groups = match.groupdict()
        name = (groups.get("name") or "").strip(" .:")
        value = parse_value(groups.get("value"))
        # No parseable value means this was prose that happened to have two
        # spaces in it, not a table row.
        if not name or value is None:
            continue
        analytes.append(
            ExtractedAnalyte(
                test_name_raw=name,
                value=value,
                unit_raw=(groups.get("unit") or "").strip() or None,
                reference=parse_reference_range(groups.get("reference")),
                abnormal_flag_from_lab=None,
                method=TIER_GENERIC,
                # Lower than a template on purpose: the columns were inferred
                # from whitespace, not recognised from a known layout.
                confidence=0.70,
            )
        )
    return analytes


# ── the cascade ───────────────────────────────────────────────────────

#: A callable the caller supplies to reach NODE B. Taking it as an argument is
#: what keeps this module free of any import path to a network client.
LlmExtractor = Callable[[str], Awaitable[list[ExtractedAnalyte]]]


async def extract(
    session: AsyncSession,
    *,
    text_layer: str,
    report_type: str,
    lab_name: str | None = None,
    llm: LlmExtractor | None = None,
) -> ExtractionResult:
    """Run the cascade, stopping at the first tier that produces rows.

    ``llm=None`` is the NODE-B-is-down case and is **not an error**: tiers 1, 2
    and 4 still run, so a hospital with no inference node still gets its
    templates, its generic parse and its review queue. Throughput drops,
    correctness does not.
    """
    if not text_layer or not text_layer.strip():
        return ExtractionResult(
            analytes=[],
            tier=TIER_HUMAN,
            needs_human=True,
            reason="This document has no readable text, so nothing could be extracted.",
        )

    # ── 1 ── the lab's own layout
    template = await find_template(
        session, lab_name=lab_name, report_type=report_type, text_layer=text_layer
    )
    if template is not None:
        rows = apply_template(template, text_layer)
        if rows:
            return ExtractionResult(analytes=rows, tier=TIER_TEMPLATE)

    # ── 2 ── whatever structure is left
    rows = parse_generic(text_layer)
    if rows:
        return ExtractionResult(analytes=rows, tier=TIER_GENERIC)

    # ── 3 ── NODE B, only for what 1 and 2 missed
    if llm is not None:
        try:
            rows = await llm(text_layer)
        except Exception:
            rows = []
        if rows:
            return ExtractionResult(analytes=rows, tier=TIER_LLM)

    # ── 4 ── a person
    return ExtractionResult(
        analytes=[],
        tier=TIER_HUMAN,
        needs_human=True,
        reason=(
            "No template matched this report and its layout could not be read "
            "automatically. Enter the values by hand with the page alongside."
            if llm is not None
            else (
                "No template matched this report and its layout could not be "
                "read automatically. The AI assist is offline, so this one "
                "needs to be entered by hand."
            )
        ),
    )
