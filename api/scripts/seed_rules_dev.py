"""Development rule configuration. Phase 3.2.

⚠️⚠️ **THESE ARE NOT CLINICAL VALUES. DO NOT USE THEM ON A PATIENT.** ⚠️⚠️

The build plan could not be clearer:

    **Seed from your hospital's own critical value list, not from the
    internet.**

So this script seeds *plausible-shaped placeholders* whose only job is to make
the rule engine executable in development and to give the tests something
deterministic to run against. Every ``panic_thresholds`` row it writes carries
``source = 'DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE'``, which is both
a marker and a query: a hospital can find every unreviewed row with one
``WHERE``.

Phase 3.8 replaces these with real values, reviewed by a clinician, and
Exit Gate 3 cannot pass until that happens.

Refuses to run when ``RG_ENV`` is prod, like the patient seeder.

    python -m scripts.seed_rules_dev [--reset]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import uuid
import zlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

SEED_NAMESPACE = uuid.UUID("5eed0003-0000-4000-8000-00000000c0f6")
SEED_EPOCH_MS = 1577836800000

# The marker that makes every placeholder findable.
PLACEHOLDER_SOURCE = "DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE"


def seed_id(kind: str, key: str) -> uuid.UUID:
    """Stable UUIDv7 so re-running upserts rather than duplicating."""
    digest = uuid.uuid5(SEED_NAMESPACE, f"{kind}:{key}").bytes
    stamp = SEED_EPOCH_MS + (zlib.crc32(f"{kind}:{key}".encode()) % 1_000_000)
    raw = bytearray(digest)
    raw[0:6] = stamp.to_bytes(6, "big")
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(raw))


# ── panic thresholds ──────────────────────────────────────────────────
# Shape only. The numbers are illustrative and deliberately round.
# (test_code, sex, critical_low, critical_high, unit)
PANIC_PLACEHOLDERS = (
    ("POTASSIUM", "any", "2.5", "6.5", "mmol/L"),
    ("SODIUM", "any", "120", "160", "mmol/L"),
    ("GLUCOSE", "any", "40", "500", "mg/dL"),
    ("HAEMOGLOBIN", "any", "7", None, "g/dL"),
    ("PLATELET", "any", "20000", "1000000", "/uL"),
    ("CREATININE", "any", None, "5", "mg/dL"),
    ("TROPONIN", "any", None, "0.4", "ng/mL"),
    ("CALCIUM", "any", "6", "13", "mg/dL"),
)

# ── clinical keywords ─────────────────────────────────────────────────
# (term, category, severity, requires_negation_check)
KEYWORDS = (
    ("malignancy", "malignancy", "critical", True),
    ("carcinoma", "malignancy", "critical", True),
    ("metastasis", "malignancy", "critical", True),
    ("metastases", "malignancy", "critical", True),
    ("neoplasm", "malignancy", "follow_up", True),
    ("suspicious for malignancy", "malignancy", "critical", True),
    ("abscess", "infection", "critical", True),
    ("septic", "infection", "critical", True),
    ("perforation", "acute", "critical", True),
    ("haemorrhage", "acute", "critical", True),
    ("hemorrhage", "acute", "critical", True),
    ("pulmonary embolism", "acute", "critical", True),
    ("pneumothorax", "acute", "critical", True),
    ("obstruction", "acute", "follow_up", True),
    ("consolidation", "infection", "follow_up", True),
    ("nodule", "incidental", "follow_up", True),
    ("lesion", "incidental", "follow_up", True),
    ("effusion", "incidental", "follow_up", True),
)

# ── negation patterns ─────────────────────────────────────────────────
# The build plan names the cases that must work.
# (pattern, words_before, words_after, is_hedge)
NEGATIONS = (
    (r"\bno evidence of\b", 0, 6, False),
    (r"\bno\s+(?:definite\s+)?(?:evidence|sign|signs)\s+of\b", 0, 6, False),
    (r"\bnegative for\b", 0, 6, False),
    (r"\bruled out\b", 6, 0, False),
    (r"\br/o\b", 0, 6, False),
    (r"\bwithout\b", 0, 5, False),
    (r"\bfree of\b", 0, 5, False),
    (r"\bnot? (?:seen|identified|demonstrated|visualised|visualized)\b", 6, 0, False),
    (r"\bno\b", 0, 3, False),
    # Hedges: downgrade rather than discard. "cannot exclude malignancy" is a
    # finding somebody should look at, not a negative one.
    (r"\bcannot (?:be )?exclude(?:d)?\b", 0, 6, True),
    (r"\bunlikely\b", 6, 6, True),
    (r"\bequivocal\b", 6, 6, True),
    (r"\bcannot be ruled out\b", 6, 0, True),
)

# ── antibiotic synonyms ───────────────────────────────────────────────
# ⚠️ Indian brands matter -- the plan names these explicitly.
# (synonym, generic, atc)
ANTIBIOTICS = (
    ("augmentin", "amoxicillin-clavulanate", "J01CR02"),
    ("amoxyclav", "amoxicillin-clavulanate", "J01CR02"),
    ("co-amoxiclav", "amoxicillin-clavulanate", "J01CR02"),
    ("amoxicillin-clavulanate", "amoxicillin-clavulanate", "J01CR02"),
    ("mox", "amoxicillin", "J01CA04"),
    ("amoxicillin", "amoxicillin", "J01CA04"),
    ("monocef", "ceftriaxone", "J01DD04"),
    ("ceftriaxone", "ceftriaxone", "J01DD04"),
    ("taxim", "cefotaxime", "J01DD01"),
    ("cefotaxime", "cefotaxime", "J01DD01"),
    ("zifi", "cefixime", "J01DD08"),
    ("cefixime", "cefixime", "J01DD08"),
    ("azithral", "azithromycin", "J01FA10"),
    ("azithromycin", "azithromycin", "J01FA10"),
    ("ciplox", "ciprofloxacin", "J01MA02"),
    ("ciprofloxacin", "ciprofloxacin", "J01MA02"),
    ("levoflox", "levofloxacin", "J01MA12"),
    ("levofloxacin", "levofloxacin", "J01MA12"),
    ("flagyl", "metronidazole", "J01XD01"),
    ("metronidazole", "metronidazole", "J01XD01"),
    ("meropenem", "meropenem", "J01DH02"),
    ("meronem", "meropenem", "J01DH02"),
    ("vancomycin", "vancomycin", "J01XA01"),
    ("vancocin", "vancomycin", "J01XA01"),
    ("linezolid", "linezolid", "J01XX08"),
    ("nitrofurantoin", "nitrofurantoin", "J01XE01"),
    ("cotrimoxazole", "trimethoprim-sulfamethoxazole", "J01EE01"),
    ("septran", "trimethoprim-sulfamethoxazole", "J01EE01"),
)

# ── MDRO rules ────────────────────────────────────────────────────────
# (code, label, organism_pattern, resistant_to_any)
MDRO = (
    (
        "MRSA",
        "Methicillin-resistant Staphylococcus aureus",
        r"(?i)\bMRSA\b|methicillin[\s-]*resistant",
        None,
    ),
    (
        "MRSA_BY_PANEL",
        "S. aureus resistant to oxacillin/cefoxitin",
        r"(?i)staphylococcus\s+aureus",
        ["oxacillin", "cefoxitin", "methicillin"],
    ),
    ("ESBL", "Extended-spectrum beta-lactamase producer", r"(?i)\bESBL\b", None),
    (
        "CRE",
        "Carbapenem-resistant Enterobacteriaceae",
        r"(?i)\bCRE\b|carbapenem[\s-]*resistant",
        None,
    ),
    (
        "CRE_BY_PANEL",
        "Enterobacteriaceae resistant to a carbapenem",
        r"(?i)klebsiella|escherichia\s+coli|enterobacter",
        ["meropenem", "imipenem", "ertapenem"],
    ),
    ("VRE", "Vancomycin-resistant Enterococcus", r"(?i)enterococc", ["vancomycin"]),
)

# ── rule_config tunables ──────────────────────────────────────────────
RULE_CONFIG = (
    (
        "slight_abnormal_factor",
        {"factor": "1.5"},
        "Rule A step 7. Beyond the reference range by more than this is CRITICAL.",
    ),
    (
        "preliminary_hold_hours",
        {"hours": 48},
        "Phase 3.6. How long a preliminary result is held before it goes stale.",
    ),
    (
        "culture_contaminant_threshold",
        {"threshold": "10000"},
        "Rule B step 2. Urine colony count below this is treated as contamination.",
    ),
    (
        "compressed_clock_test_values",
        {"result_due_seconds": 120, "recheck_seconds": 60},
        "Test-only clock compression, so a chaos run need not wait 24 hours.",
    ),
    # ⚠️ These two are clinical lists, and until they were seeded the engine
    # fell back to constants in `app/rules/culture.py`. The code path was
    # data-driven; the data was not there, so in practice a hospital could not
    # change which organisms it treats as skin flora without editing Python.
    # §3.2 is explicit: "thresholds and delays live in tables an admin can
    # edit, never in code."
    (
        "culture_no_growth_patterns",
        {
            "values": [
                "no growth",
                "no organisms isolated",
                "no organism isolated",
                "sterile",
                "culture negative",
                "no significant growth",
            ]
        },
        "Rule B step 1. Organism names that mean nothing grew. Substring match, "
        "case-insensitive. Edit to match how your lab words its reports.",
    ),
    (
        "culture_contaminant_organisms",
        {
            "values": [
                "mixed flora",
                "mixed bacterial flora",
                "normal flora",
                "normal skin flora",
                "coagulase negative staphylococc",
                "coagulase-negative staphylococc",
                "staphylococcus epidermidis",
                "diphtheroids",
                "corynebacterium",
                "bacillus species",
                "micrococcus",
                "propionibacterium",
                "cutibacterium",
            ]
        },
        "Rule B step 2. Common skin/contaminant flora. FOLLOW_UP, never "
        "auto-closed. ⚠️ Clinical content — have a microbiologist review it.",
    ),
)

# ── unit conversions ──────────────────────────────────────────────────
# (test_code, from, to, factor)
UNIT_CONVERSIONS = (
    (None, "g/L", "g/dL", "0.1"),
    (None, "g/dL", "g/L", "10"),
    (None, "mg/L", "mg/dL", "0.1"),
    (None, "mg/dL", "mg/L", "10"),
    ("CREATININE", "umol/L", "mg/dL", "0.0113"),
    ("CREATININE", "mg/dL", "umol/L", "88.4"),
    ("GLUCOSE", "mmol/L", "mg/dL", "18.0182"),
    ("GLUCOSE", "mg/dL", "mmol/L", "0.0555"),
)


async def _seed(session: AsyncSession) -> dict[str, int]:
    counts: dict[str, int] = {}
    now = dt.datetime.now(dt.UTC)

    for code, sex, low, high, unit in PANIC_PLACEHOLDERS:
        await session.execute(
            text(
                "INSERT INTO panic_thresholds "
                "(id, test_code, sex, critical_low, critical_high, unit, source, "
                " effective_from) "
                "VALUES (:i, :c, :s, CAST(:lo AS numeric), CAST(:hi AS numeric), "
                "        :u, :src, :ef) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  critical_low = EXCLUDED.critical_low, "
                "  critical_high = EXCLUDED.critical_high, "
                "  source = EXCLUDED.source, deleted_at = NULL"
            ),
            {
                "i": str(seed_id("panic", code)),
                "c": code,
                "s": sex,
                "lo": low,
                "hi": high,
                "u": unit,
                "src": PLACEHOLDER_SOURCE,
                "ef": now - dt.timedelta(days=365),
            },
        )
    counts["panic_thresholds"] = len(PANIC_PLACEHOLDERS)

    for term, category, severity, negate in KEYWORDS:
        await session.execute(
            text(
                "INSERT INTO clinical_keywords "
                "(id, term, category, severity, requires_negation_check, active) "
                "VALUES (:i, :t, :c, :s, :n, true) "
                "ON CONFLICT (term) DO UPDATE SET "
                "  category = EXCLUDED.category, severity = EXCLUDED.severity, "
                "  active = true, deleted_at = NULL"
            ),
            {
                "i": str(seed_id("keyword", term)),
                "t": term,
                "c": category,
                "s": severity,
                "n": negate,
            },
        )
    counts["clinical_keywords"] = len(KEYWORDS)

    for pattern, before, after, hedge in NEGATIONS:
        await session.execute(
            text(
                "INSERT INTO negation_patterns "
                "(id, pattern, scope_words_before, scope_words_after, "
                " is_hedge, active) "
                "VALUES (:i, :p, :b, :a, :h, true) "
                "ON CONFLICT (pattern) DO UPDATE SET "
                "  scope_words_before = EXCLUDED.scope_words_before, "
                "  scope_words_after = EXCLUDED.scope_words_after, "
                "  is_hedge = EXCLUDED.is_hedge, active = true, deleted_at = NULL"
            ),
            {
                "i": str(seed_id("negation", pattern)),
                "p": pattern,
                "b": before,
                "a": after,
                "h": hedge,
            },
        )
    counts["negation_patterns"] = len(NEGATIONS)

    for synonym, generic, atc in ANTIBIOTICS:
        await session.execute(
            text(
                "INSERT INTO antibiotic_synonyms "
                "(id, synonym, generic_name, atc_code, active) "
                "VALUES (:i, :s, :g, :a, true) "
                "ON CONFLICT (synonym) DO UPDATE SET "
                "  generic_name = EXCLUDED.generic_name, "
                "  atc_code = EXCLUDED.atc_code, active = true, deleted_at = NULL"
            ),
            {
                "i": str(seed_id("abx", synonym)),
                "s": synonym,
                "g": generic,
                "a": atc,
            },
        )
    counts["antibiotic_synonyms"] = len(ANTIBIOTICS)

    for code, label, pattern, resistant in MDRO:
        await session.execute(
            text(
                "INSERT INTO mdro_rules "
                "(id, code, label, organism_pattern, resistant_to_any, active) "
                "VALUES (:i, :c, :l, :p, CAST(:r AS jsonb), true) "
                "ON CONFLICT (code) DO UPDATE SET "
                "  organism_pattern = EXCLUDED.organism_pattern, "
                "  resistant_to_any = EXCLUDED.resistant_to_any, "
                "  active = true, deleted_at = NULL"
            ),
            {
                "i": str(seed_id("mdro", code)),
                "c": code,
                "l": label,
                "p": pattern,
                "r": None if resistant is None else json.dumps(resistant),
            },
        )
    counts["mdro_rules"] = len(MDRO)

    for key, value, description in RULE_CONFIG:
        await session.execute(
            text(
                "INSERT INTO rule_config (id, key, value, description) "
                "VALUES (:i, :k, CAST(:v AS jsonb), :d) "
                "ON CONFLICT (key) DO UPDATE SET "
                "  value = EXCLUDED.value, description = EXCLUDED.description, "
                "  deleted_at = NULL"
            ),
            {
                "i": str(seed_id("config", key)),
                "k": key,
                "v": json.dumps(value),
                "d": description,
            },
        )
    counts["rule_config"] = len(RULE_CONFIG)

    for test_code, from_unit, to_unit, factor in UNIT_CONVERSIONS:
        await session.execute(
            text(
                "INSERT INTO unit_conversions "
                "(id, test_code, from_unit, to_unit, factor) "
                "VALUES (:i, :c, :f, :t, CAST(:x AS numeric)) "
                "ON CONFLICT (test_code, from_unit, to_unit) DO UPDATE SET "
                "  factor = EXCLUDED.factor, deleted_at = NULL"
            ),
            {
                "i": str(seed_id("unit", f"{test_code}:{from_unit}:{to_unit}")),
                "c": test_code,
                "f": from_unit,
                "t": to_unit,
                "x": factor,
            },
        )
    counts["unit_conversions"] = len(UNIT_CONVERSIONS)

    return counts


async def _reset(session: AsyncSession) -> None:
    """Soft-delete the placeholders. Never touches rows a hospital added."""
    now = dt.datetime.now(dt.UTC)
    await session.execute(
        text("UPDATE panic_thresholds SET deleted_at = :t WHERE source = :s"),
        {"t": now, "s": PLACEHOLDER_SOURCE},
    )
    for table, values in (
        ("clinical_keywords", [k[0] for k in KEYWORDS]),
        ("negation_patterns", [n[0] for n in NEGATIONS]),
        ("antibiotic_synonyms", [a[0] for a in ANTIBIOTICS]),
    ):
        column = {
            "clinical_keywords": "term",
            "negation_patterns": "pattern",
            "antibiotic_synonyms": "synonym",
        }[table]
        await session.execute(
            text(f"UPDATE {table} SET deleted_at = :t " f"WHERE {column} = ANY(:v)"),
            {"t": now, "v": values},
        )
    await session.execute(
        text("UPDATE mdro_rules SET deleted_at = :t WHERE code = ANY(:v)"),
        {"t": now, "v": [m[0] for m in MDRO]},
    )
    await session.execute(
        text("UPDATE rule_config SET deleted_at = :t WHERE key = ANY(:v)"),
        {"t": now, "v": [c[0] for c in RULE_CONFIG]},
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.is_prod:
        print(
            "REFUSING: RG_ENV is prod. These are placeholder values, not "
            "clinical ones — seed panic_thresholds from the hospital's own SOP.",
            file=sys.stderr,
        )
        return 2

    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as session:
            if args.reset:
                await _reset(session)
            counts = await _seed(session)
            await session.commit()
    finally:
        await engine.dispose()

    for label, count in counts.items():
        print(f"  {label:<22} {count}")
    print()
    print("  ⚠️  panic_thresholds are PLACEHOLDERS, not clinical values.")
    print(f"      Find them with:  source = '{PLACEHOLDER_SOURCE}'")
    print("      Phase 3.8 replaces them with the hospital's own list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
