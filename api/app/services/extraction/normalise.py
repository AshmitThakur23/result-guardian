"""``S. Creat`` → ``2160-0``, and ``mg/dL`` → ``µmol/L``. Phase 7.4.

The cascade, in 7.4's order:

1. **exact synonym** — someone has mapped this exact string before
2. **normalised string** — lowercase, strip punctuation, unaccent, then exact
3. **trigram similarity ≥ 0.85** — the lab changed its spelling slightly
4. **LOINC search** — the term exists in LOINC under a name we have not seen
5. **unmapped** — an admin queue entry

> *Unmapped terms land in an admin queue; **mapping them once fixes them
> forever**.*

That last sentence is the design. Every step that is *not* step 1 writes back a
synonym when a human confirms it, so the cascade gets shorter over time and a
hospital's tenth week costs less than its first.

## Why the similarity floor is 0.85 and not lower

Trigram similarity does not know clinical meaning. ``Vitamin B12`` and
``Vitamin D`` share a lot of trigrams; so do ``Total Protein`` and
``Total Bilirubin``. Below ~0.85 the neighbours stop being spelling variants and
start being *different tests*, and mapping a result to the wrong analyte is the
same class of error as mapping it to the wrong patient. Anything under the floor
goes to the queue, which is a person's half-minute rather than a silent mistake.
"""

from __future__ import annotations

import dataclasses
import decimal
import re
import unicodedata

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 7.4's threshold. Below this the cascade stops and a human decides.
SIMILARITY_FLOOR = 0.85

METHOD_EXACT = "exact_synonym"
METHOD_NORMALISED = "normalised_synonym"
METHOD_TRIGRAM = "trigram_similarity"
METHOD_LOINC = "loinc_search"
METHOD_UNMAPPED = "unmapped"

_PUNCTUATION = re.compile(r"[^\w\s]+")
_WHITESPACE = re.compile(r"\s+")


def normalise_test_name(raw: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace.

    ``"SR. CREATININE"``, ``"S. Creat."`` and ``"s creat"`` all become
    ``"sr creatinine"`` / ``"s creat"`` — close enough that step 2 catches most
    of what step 1 misses without any similarity search at all.

    NFKD before stripping, so ``µ`` and ``μ`` do not survive as different
    characters and split one analyte into two.
    """
    decomposed = unicodedata.normalize("NFKD", raw)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = without_accents.casefold()
    depunctuated = _PUNCTUATION.sub(" ", lowered)
    return _WHITESPACE.sub(" ", depunctuated).strip()


@dataclasses.dataclass(frozen=True)
class Mapping:
    loinc_code: str | None
    method: str
    #: 1.0 for exact matches; the similarity score for fuzzy ones.
    confidence: float
    #: What it matched against, for the admin queue to show.
    matched_text: str | None = None

    @property
    def is_mapped(self) -> bool:
        return self.loinc_code is not None


UNMAPPED = Mapping(loinc_code=None, method=METHOD_UNMAPPED, confidence=0.0)


async def map_test_name(
    session: AsyncSession, raw: str, *, lab_name: str | None = None
) -> Mapping:
    """Run the cascade. Returns :data:`UNMAPPED` rather than guessing.

    ``lab_name`` narrows steps 1–3: a lab-specific mapping beats a global one,
    because two labs genuinely do use the same abbreviation for different tests.
    """
    if not raw or not raw.strip():
        return UNMAPPED

    normalised = normalise_test_name(raw)
    if not normalised:
        return UNMAPPED

    # ── 1 & 2: exact, then normalised. One query; a lab-specific row sorts
    # first so it wins without a second round trip.
    row = (
        await session.execute(
            text(
                "SELECT loinc_code, raw_text, normalised_text "
                "  FROM test_synonyms "
                " WHERE deleted_at IS NULL "
                "   AND (lab_name = :lab OR lab_name IS NULL) "
                "   AND (normalised_text = :norm OR raw_text = :raw) "
                " ORDER BY (lab_name IS NOT NULL) DESC, "
                "          (raw_text = :raw) DESC "
                " LIMIT 1"
            ),
            {"lab": lab_name, "norm": normalised, "raw": raw.strip()},
        )
    ).first()
    if row is not None:
        exact = row.raw_text == raw.strip()
        return Mapping(
            loinc_code=row.loinc_code,
            method=METHOD_EXACT if exact else METHOD_NORMALISED,
            confidence=1.0,
            matched_text=row.raw_text,
        )

    # ── 3: trigram similarity over known synonyms.
    fuzzy = (
        await session.execute(
            text(
                "SELECT loinc_code, raw_text, "
                "       similarity(normalised_text, :norm) AS score "
                "  FROM test_synonyms "
                " WHERE deleted_at IS NULL "
                "   AND (lab_name = :lab OR lab_name IS NULL) "
                "   AND similarity(normalised_text, :norm) >= :floor "
                " ORDER BY score DESC, (lab_name IS NOT NULL) DESC "
                " LIMIT 2"
            ),
            {"lab": lab_name, "norm": normalised, "floor": SIMILARITY_FLOOR},
        )
    ).fetchall()

    if fuzzy:
        best = fuzzy[0]
        # ★ The same tie rule as Phase 7.5, for the same reason. Two analytes
        # equally similar to the text is not a question arithmetic can answer,
        # and picking one maps a result onto the wrong test.
        if len(fuzzy) > 1 and fuzzy[1].score == best.score:
            return UNMAPPED
        return Mapping(
            loinc_code=best.loinc_code,
            method=METHOD_TRIGRAM,
            confidence=float(best.score),
            matched_text=best.raw_text,
        )

    # ── 4: the term may exist in LOINC under a name we have never been sent.
    loinc = (
        await session.execute(
            text(
                "SELECT loinc_code, long_name, "
                "       similarity(long_name, :norm) AS score "
                "  FROM loinc_terms "
                " WHERE similarity(long_name, :norm) >= :floor "
                " ORDER BY score DESC "
                " LIMIT 2"
            ),
            {"norm": normalised, "floor": SIMILARITY_FLOOR},
        )
    ).fetchall()

    if loinc:
        best = loinc[0]
        if len(loinc) > 1 and loinc[1].score == best.score:
            return UNMAPPED
        return Mapping(
            loinc_code=best.loinc_code,
            method=METHOD_LOINC,
            confidence=float(best.score),
            matched_text=best.long_name,
        )

    # ── 5: an admin queue entry. Mapping it once fixes it forever.
    return UNMAPPED


async def remember_mapping(
    session: AsyncSession,
    *,
    raw: str,
    loinc_code: str,
    lab_name: str | None = None,
    source: str = "manual",
) -> None:
    """Write back a confirmed mapping so the cascade never re-derives it.

    ``ON CONFLICT DO UPDATE`` rather than ``DO NOTHING``: a human correcting an
    earlier wrong mapping must win, and silently keeping the old code is how a
    mis-mapped analyte becomes permanent.
    """
    await session.execute(
        text(
            "INSERT INTO test_synonyms "
            "  (id, raw_text, normalised_text, loinc_code, lab_name, source, "
            "   created_at, updated_at) "
            "VALUES (gen_random_uuid(), :raw, :norm, :code, :lab, :src, "
            "        now(), now()) "
            "ON CONFLICT (normalised_text, lab_name) DO UPDATE "
            "   SET loinc_code = EXCLUDED.loinc_code, "
            "       source = EXCLUDED.source, "
            "       deleted_at = NULL, "
            "       updated_at = now()"
        ),
        {
            "raw": raw.strip(),
            "norm": normalise_test_name(raw),
            "code": loinc_code,
            "lab": lab_name,
            "src": source,
        },
    )


# ── units ─────────────────────────────────────────────────────────────


async def convert_unit(
    session: AsyncSession,
    *,
    value: decimal.Decimal,
    from_unit: str,
    to_unit: str,
    test_code: str | None = None,
) -> decimal.Decimal | None:
    """Convert using the ``unit_conversions`` table. ``None`` when unknown.

    **Conversions are per test, not global.** mg/dL → µmol/L depends on the
    analyte's molar mass: creatinine multiplies by 88.4 and glucose by 0.0555.
    A single global factor would be wrong for everything except whichever
    analyte it was derived from — which is why ``test_code`` is part of the key
    and a row without one is only used as a last resort.
    """
    if from_unit == to_unit:
        return value

    row = (
        await session.execute(
            text(
                'SELECT factor, "offset" FROM unit_conversions '
                " WHERE deleted_at IS NULL "
                "   AND from_unit = :f AND to_unit = :t "
                "   AND (test_code = :c OR test_code IS NULL) "
                " ORDER BY (test_code IS NOT NULL) DESC "
                " LIMIT 1"
            ),
            {"f": from_unit, "t": to_unit, "c": test_code},
        )
    ).first()
    if row is None:
        return None
    # Cast rather than trust the row: SQLAlchemy types a raw-text row as Any,
    # and a NUMERIC column arriving as a float instead of a Decimal is exactly
    # the silent precision loss the base conventions ban floats to prevent.
    factor = decimal.Decimal(str(row.factor))
    offset = (
        decimal.Decimal(str(row.offset))
        if row.offset is not None
        else decimal.Decimal(0)
    )
    return (value * factor) + offset
