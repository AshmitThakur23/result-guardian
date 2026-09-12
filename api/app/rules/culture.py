"""Rule B — culture and sensitivity. Phase 3.4. ★ highest clinical value.

    **R → CRITICAL** — the patient is at home on an ineffective drug.

That sentence is the product. Everything else in Result Guardian exists so
that this comparison happens at all: a culture came back, the organism is
resistant to the antibiotic the patient was discharged on, and nobody noticed.

The build plan's algorithm, step for step:

1. no growth / sterile → **NORMAL**, auto-close
2. contaminant pattern (mixed flora, low colony count, common skin flora) →
   **FOLLOW_UP**, **never auto-close**
3. for each discharge antibiotic: map name → code, find the sensitivity row,
   **R → CRITICAL**, **I → FOLLOW_UP**, **S → covered**
4. no discharge antibiotic at all and the organism is significant → **FOLLOW_UP**
5. all discharge antibiotics S → **NORMAL**, auto-close with a logged reason
6. multi-drug-resistant organism → **CRITICAL regardless**

Two places where getting it wrong is expensive in opposite directions:

**Step 2 is an adoption problem, not a clinical one.** *"Skipping this floods
doctors with noise and kills adoption."* A skin contaminant flagged as urgent,
repeatedly, teaches a ward to ignore the product — and then the one real
result is ignored too. But it is still FOLLOW_UP and still never auto-closes,
because a contaminant call is a judgement a human should confirm.

**Step 3 depends entirely on the synonym table.** The discharge summary says
"Monocef"; the sensitivity grid says "Ceftriaxone". Without
``antibiotic_synonyms`` the lookup silently finds nothing and the resistant
case is missed — a false negative that looks exactly like a clean result. So
an unmatched discharge drug is reported explicitly rather than skipped.

No AI. Every decision here is a table lookup and a comparison.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rules import SEVERITY_CRITICAL, SEVERITY_FOLLOW_UP, SEVERITY_NORMAL
from app.rules.numeric import RuleOutput

RULE_ID = "B_culture"

REASON_NO_GROWTH = "CULT_NO_GROWTH"
REASON_CONTAMINANT = "CULT_LIKELY_CONTAMINANT"
REASON_RESISTANT_TO_DISCHARGE_DRUG = "CULT_RESISTANT_TO_DISCHARGE_DRUG"
REASON_INTERMEDIATE_TO_DISCHARGE_DRUG = "CULT_INTERMEDIATE_TO_DISCHARGE_DRUG"
REASON_NO_DISCHARGE_ANTIBIOTIC = "CULT_NO_DISCHARGE_ANTIBIOTIC"
REASON_COVERED = "CULT_COVERED_BY_DISCHARGE_DRUGS"
REASON_MDRO = "CULT_MULTI_DRUG_RESISTANT_ORGANISM"
REASON_NO_SENSITIVITY_DATA = "CULT_NO_SENSITIVITY_DATA"
REASON_DRUG_NOT_ON_PANEL = "CULT_DISCHARGE_DRUG_NOT_ON_PANEL"

# Defaults, overridable in rule_config. Nothing clinical is hardcoded; these
# are the fallbacks used when a hospital has not configured its own.
DEFAULT_NO_GROWTH_PATTERNS = (
    "no growth",
    "no organisms isolated",
    "no organism isolated",
    "sterile",
    "culture negative",
    "no significant growth",
)
DEFAULT_CONTAMINANT_ORGANISMS = (
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
)
# Below this colony count a urine isolate is treated as likely contamination.
DEFAULT_CONTAMINANT_THRESHOLD = Decimal("10000")

# Colony counts arrive in two shapes and they must not be confused:
#   scientific -- "10^5", ">10^5 CFU/mL", "1.5 x 10^5", "10e5", "1.5e5", ">10\u2075"
#   plain      -- ">100,000 CFU/mL", "25000"
#
# Every miss here fails in the same dangerous direction. An under-read puts a
# heavy growth below the contaminant threshold, and the contaminant branch
# returns *before* the resistance comparison \u2014 so the organism the patient's
# antibiotic does not cover is reported as likely contamination. Three forms
# have already been caught doing exactly that: "1.5 x 10^5" read as 1.5,
# "10^5" read as a million, and "1.5e5" read as 1.5.
#
# Two separate shapes, because they mean different things:
#   ``10^5`` / ``10e5``  -> the base IS ten, the mantissa is implicit 1
#   ``1.5e5`` / ``1.5 x 10^5`` -> an explicit mantissa
_COLONY_POWER_OF_TEN = re.compile(
    r"(?:(?P<mantissa>[0-9]+(?:\.[0-9]+)?)\s*[x*\u00d7]\s*)?"
    r"10\s*(?:\^|\*\*|e)\s*(?P<exponent>[0-9]+)",
    re.IGNORECASE,
)
# "1.5e5" \u2014 mantissa and exponent with no literal 10 between them. Checked
# only after the form above, so "10e5" is read as 10**5 rather than 10 x 10**5.
_COLONY_E_NOTATION = re.compile(
    r"(?P<mantissa>[0-9]+(?:\.[0-9]+)?)\s*e\s*(?P<exponent>[0-9]+)",
    re.IGNORECASE,
)
# Unicode superscripts: labs print ">10⁵", and a PDF export keeps
# them. Read as a plain number, "10⁵" is ten -- four orders of
# magnitude under the truth, and under the contaminant threshold.
_SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUPERSCRIPTS = str.maketrans(_SUPERSCRIPT_DIGITS, "0123456789")
_COLONY_SUPERSCRIPT = re.compile(r"10\s*(?P<exponent>[" + _SUPERSCRIPT_DIGITS + r"]+)")
_COLONY_PLAIN = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")
# A reported range ("50,000-100,000 CFU/mL"). The upper bound is what decides
# whether the growth could be significant, and dismissing a range whose top
# end is significant is the under-read this module exists to avoid.
_COLONY_RANGE = re.compile(
    r"(?P<low>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:-|\u2013|to)\s*(?P<high>[0-9][0-9,]*(?:\.[0-9]+)?)",
    re.IGNORECASE,
)


@dataclass
class DischargeDrug:
    """One antibiotic the patient actually went home on."""

    drug_name: str
    normalized: str | None = None
    atc_code: str | None = None


@dataclass
class CultureFinding:
    """Rule B's verdict for one organism."""

    organism: str
    severity: str
    reason_code: str
    offending_drug: str | None = None
    alternatives_available: list[str] = field(default_factory=list)
    auto_close: bool = False
    detail: dict[str, object] = field(default_factory=dict)


async def _config_list(
    session: AsyncSession, key: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    row = (
        await session.execute(
            text("SELECT value FROM rule_config WHERE key = :k AND deleted_at IS NULL"),
            {"k": key},
        )
    ).first()
    if row is None:
        return default
    raw = row.value
    values = raw.get("values") if isinstance(raw, dict) else raw
    if isinstance(values, list) and values:
        return tuple(str(v).lower() for v in values)
    return default


async def _contaminant_threshold(session: AsyncSession) -> Decimal:
    row = (
        await session.execute(
            text(
                "SELECT value FROM rule_config "
                " WHERE key = 'culture_contaminant_threshold' AND deleted_at IS NULL"
            )
        )
    ).first()
    if row is None:
        return DEFAULT_CONTAMINANT_THRESHOLD
    raw = row.value
    candidate = raw.get("threshold") if isinstance(raw, dict) else raw
    try:
        return Decimal(str(candidate))
    except (InvalidOperation, TypeError):
        return DEFAULT_CONTAMINANT_THRESHOLD


def parse_colony_count(raw: str | None) -> Decimal | None:
    """Turn ">100,000 CFU/mL", "10^5", "25000" into a number.

    Returns ``None`` for anything unparseable -- "scanty", "moderate growth",
    or a blank. The caller must not treat that as a low count: an unreported
    count is not evidence of contamination.

    Getting the exponent wrong is not cosmetic. Reading "1.5 x 10^5" as 1.5
    puts a heavy growth under the contaminant threshold, and the contaminant
    branch returns *before* the resistance comparison -- so a resistant
    organism would be reported as likely contamination instead of CRITICAL.
    """
    if not raw:
        return None
    text_value = raw.strip().lower()

    # Order matters. "10e5" must be read as a power of ten, not as a mantissa
    # of 10 with an exponent of 5, so the power-of-ten form is tried first.
    power = _COLONY_POWER_OF_TEN.search(text_value)
    if power is not None:
        return _scaled(power.group("mantissa"), power.group("exponent"))

    superscript = _COLONY_SUPERSCRIPT.search(text_value)
    if superscript is not None:
        return _scaled(None, superscript.group("exponent").translate(_SUPERSCRIPTS))

    e_notation = _COLONY_E_NOTATION.search(text_value)
    if e_notation is not None:
        return _scaled(e_notation.group("mantissa"), e_notation.group("exponent"))

    # A range: take the upper bound. "50,000-100,000" reaching significance
    # must not be dismissed because its lower end does not.
    spread = _COLONY_RANGE.search(text_value)
    if spread is not None:
        return _decimal(spread.group("high"))

    plain = _COLONY_PLAIN.search(text_value)
    return _decimal(plain.group(0)) if plain is not None else None


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _scaled(mantissa_raw: str | None, exponent_raw: str) -> Decimal | None:
    """``mantissa x 10**exponent``, with an implicit mantissa of 1."""
    try:
        mantissa = Decimal(mantissa_raw) if mantissa_raw is not None else Decimal(1)
        return mantissa * (Decimal(10) ** int(exponent_raw))
    except (InvalidOperation, ValueError):
        return None


async def normalize_antibiotic(
    session: AsyncSession, drug_name: str
) -> tuple[str | None, str | None]:
    """brand → (generic, ATC). Step 3's first move.

    Returns ``(None, None)`` when the name is not in the synonym table, which
    the caller reports rather than swallows: an unmapped discharge drug means
    Rule B could not check it, and silence there looks identical to "covered".
    """
    cleaned = drug_name.strip().lower()
    if not cleaned:
        return (None, None)

    row = (
        await session.execute(
            text(
                "SELECT generic_name, atc_code FROM antibiotic_synonyms "
                " WHERE lower(synonym) = :s AND active AND deleted_at IS NULL "
                " LIMIT 1"
            ),
            {"s": cleaned},
        )
    ).first()
    if row is not None:
        return (row.generic_name.lower(), row.atc_code)

    # A generic name that is its own synonym ("Amoxicillin" prescribed and
    # "Amoxicillin" on the panel) need not be in the table to match.
    row = (
        await session.execute(
            text(
                "SELECT generic_name, atc_code FROM antibiotic_synonyms "
                " WHERE lower(generic_name) = :s AND active AND deleted_at IS NULL "
                " LIMIT 1"
            ),
            {"s": cleaned},
        )
    ).first()
    if row is not None:
        return (row.generic_name.lower(), row.atc_code)
    return (None, None)


async def _is_mdro(
    session: AsyncSession, organism_name: str, resistant_generics: set[str]
) -> tuple[bool, str | None]:
    """Step 6. MRSA, ESBL, CRE — critical regardless of the prescription.

    ``ORDER BY code`` because more than one rule can match the same organism
    — an "MRSA (methicillin-resistant Staphylococcus aureus)" with an
    oxacillin R matches both the name rule and the panel rule. The severity is
    the same either way, but the ``mdro_code`` recorded in the explanation is
    not, and an unordered ``SELECT`` made it change between runs. *"You must
    be able to explain a 6-month-old decision"* rules that out.
    """
    rows = (
        await session.execute(
            text(
                "SELECT code, label, organism_pattern, resistant_to_any "
                "  FROM mdro_rules WHERE active AND deleted_at IS NULL "
                " ORDER BY code"
            )
        )
    ).all()
    for row in rows:
        try:
            matches = re.search(row.organism_pattern, organism_name, re.IGNORECASE)
        except re.error:  # a bad admin-entered regex must not break the engine
            continue
        if not matches:
            continue
        required = row.resistant_to_any
        if not required:
            return (True, row.code)
        if any(str(drug).lower() in resistant_generics for drug in required):
            return (True, row.code)
    return (False, None)


async def classify_organism(
    session: AsyncSession,
    *,
    organism_name: str,
    colony_count: str | None,
    specimen_type: str | None,
    sensitivities: list[tuple[str, str]],
    discharge_drugs: list[DischargeDrug],
) -> CultureFinding:
    """Rule B for one organism. ``sensitivities`` is [(antibiotic, S|I|R)]."""
    name = organism_name.strip().lower()

    # ── step 1: no growth ─────────────────────────────────────────
    no_growth = await _config_list(
        session, "culture_no_growth_patterns", DEFAULT_NO_GROWTH_PATTERNS
    )
    if any(pattern in name for pattern in no_growth):
        return CultureFinding(
            organism=organism_name,
            severity=SEVERITY_NORMAL,
            reason_code=REASON_NO_GROWTH,
            auto_close=True,
        )

    # Normalise the panel once: generic name -> interpretation.
    panel: dict[str, str] = {}
    resistant_generics: set[str] = set()
    for antibiotic, interpretation in sensitivities:
        generic, _atc = await normalize_antibiotic(session, antibiotic)
        key = generic or antibiotic.strip().lower()
        panel[key] = interpretation
        if interpretation == "R":
            resistant_generics.add(key)

    # ── step 6: MDRO overrides everything below ───────────────────
    # Checked before contaminant and before the drug comparison: an MRSA is
    # critical whether or not it looks like skin flora and whether or not the
    # patient is on anything, because the infection-control consequence does
    # not depend on the prescription.
    is_mdro, mdro_code = await _is_mdro(session, organism_name, resistant_generics)
    if is_mdro:
        return CultureFinding(
            organism=organism_name,
            severity=SEVERITY_CRITICAL,
            reason_code=REASON_MDRO,
            alternatives_available=sorted(
                drug for drug, value in panel.items() if value == "S"
            ),
            auto_close=False,
            detail={"mdro_code": mdro_code},
        )

    # ── step 2: contaminant ───────────────────────────────────────
    contaminants = await _config_list(
        session, "culture_contaminant_organisms", DEFAULT_CONTAMINANT_ORGANISMS
    )
    looks_like_flora = any(pattern in name for pattern in contaminants)

    count = parse_colony_count(colony_count)
    threshold = await _contaminant_threshold(session)
    # Only urine has a meaningful colony-count threshold. A low count from a
    # sterile site is not reassuring, so the count alone never calls
    # contamination outside urine.
    low_count = (
        count is not None
        and count < threshold
        and (specimen_type or "").strip().lower().startswith("urine")
    )

    if looks_like_flora or low_count:
        return CultureFinding(
            organism=organism_name,
            severity=SEVERITY_FOLLOW_UP,
            reason_code=REASON_CONTAMINANT,
            # NEVER auto-close. The plan is explicit, and a contaminant call
            # is a judgement a human should confirm.
            auto_close=False,
            detail={
                "matched_flora": looks_like_flora,
                "low_colony_count": low_count,
                "colony_count_parsed": str(count) if count is not None else None,
                "threshold": str(threshold),
            },
        )

    alternatives = sorted(drug for drug, value in panel.items() if value == "S")

    # ── step 4: nothing was prescribed ────────────────────────────
    if not discharge_drugs:
        return CultureFinding(
            organism=organism_name,
            severity=SEVERITY_FOLLOW_UP,
            reason_code=REASON_NO_DISCHARGE_ANTIBIOTIC,
            alternatives_available=alternatives,
            auto_close=False,
        )

    if not panel:
        # An organism grew and nobody tested anything against it. That is not
        # "covered"; it is unanswerable, and unanswerable is FOLLOW_UP.
        return CultureFinding(
            organism=organism_name,
            severity=SEVERITY_FOLLOW_UP,
            reason_code=REASON_NO_SENSITIVITY_DATA,
            auto_close=False,
        )

    # ── step 3: compare each discharge drug ───────────────────────
    intermediate_drug: str | None = None
    unmatched: list[str] = []
    covered = False

    for drug in discharge_drugs:
        generic, _atc = await normalize_antibiotic(session, drug.drug_name)
        key = generic or drug.drug_name.strip().lower()
        # Named distinctly from the panel-building loop above: reusing
        # `interpretation` narrows to str there and makes this Optional
        # assignment a type error.
        result = panel.get(key)

        if result is None:
            # Either the synonym table does not know this brand, or the lab
            # did not test it. Both mean "not checked", and both are reported.
            unmatched.append(drug.drug_name)
            continue
        if result == "R":
            # The whole reason this product exists.
            return CultureFinding(
                organism=organism_name,
                severity=SEVERITY_CRITICAL,
                reason_code=REASON_RESISTANT_TO_DISCHARGE_DRUG,
                offending_drug=drug.drug_name,
                alternatives_available=alternatives,
                auto_close=False,
                detail={"normalized_drug": key, "interpretation": "R"},
            )
        if result == "I":
            intermediate_drug = intermediate_drug or drug.drug_name
        elif result == "S":
            covered = True

    if intermediate_drug is not None:
        return CultureFinding(
            organism=organism_name,
            severity=SEVERITY_FOLLOW_UP,
            reason_code=REASON_INTERMEDIATE_TO_DISCHARGE_DRUG,
            offending_drug=intermediate_drug,
            alternatives_available=alternatives,
            auto_close=False,
        )

    if unmatched:
        # Not covered and not checkable. Saying NORMAL here would be the
        # dangerous kind of quiet.
        return CultureFinding(
            organism=organism_name,
            severity=SEVERITY_FOLLOW_UP,
            reason_code=REASON_DRUG_NOT_ON_PANEL,
            alternatives_available=alternatives,
            auto_close=False,
            detail={"unmatched_drugs": unmatched},
        )

    # ── step 5: every discharge drug is S ─────────────────────────
    if covered:
        return CultureFinding(
            organism=organism_name,
            severity=SEVERITY_NORMAL,
            reason_code=REASON_COVERED,
            alternatives_available=alternatives,
            auto_close=True,
            detail={
                "covered_by": [d.drug_name for d in discharge_drugs],
                "logged_reason": "every discharge antibiotic is susceptible",
            },
        )

    return CultureFinding(
        organism=organism_name,
        severity=SEVERITY_FOLLOW_UP,
        reason_code=REASON_NO_SENSITIVITY_DATA,
        alternatives_available=alternatives,
        auto_close=False,
    )


async def classify_result_culture(
    session: AsyncSession, result_id: uuid.UUID
) -> list[RuleOutput]:
    """Rule B over every organism on a result.

    Discharge medications come from the case's encounter -- the drugs the
    patient is *actually taking*, which is the whole point of having captured
    them in Phase 1.5.
    """
    organisms = (
        await session.execute(
            text(
                "SELECT id, organism_name, colony_count, specimen_type "
                "  FROM result_organisms "
                " WHERE result_id = :r AND deleted_at IS NULL "
                " ORDER BY organism_name"
            ),
            {"r": str(result_id)},
        )
    ).all()
    if not organisms:
        return []

    drugs_rows = (
        await session.execute(
            text(
                "SELECT dm.drug_name FROM discharge_medications dm "
                " WHERE dm.is_antibiotic AND dm.deleted_at IS NULL "
                "   AND dm.encounter_id = ( "
                "         SELECT o.encounter_id FROM results r "
                "           JOIN orders o ON o.id = r.order_id "
                "          WHERE r.id = :r) "
                " ORDER BY dm.drug_name"
            ),
            {"r": str(result_id)},
        )
    ).all()
    discharge_drugs = [DischargeDrug(drug_name=row.drug_name) for row in drugs_rows]

    outputs: list[RuleOutput] = []
    for organism in organisms:
        sens = (
            await session.execute(
                text(
                    "SELECT antibiotic_name, interpretation "
                    "  FROM result_sensitivities "
                    " WHERE organism_id = :o AND deleted_at IS NULL"
                ),
                {"o": str(organism.id)},
            )
        ).all()

        finding = await classify_organism(
            session,
            organism_name=organism.organism_name,
            colony_count=organism.colony_count,
            specimen_type=organism.specimen_type,
            sensitivities=[(r.antibiotic_name, r.interpretation) for r in sens],
            discharge_drugs=discharge_drugs,
        )
        outputs.append(
            RuleOutput(
                severity=finding.severity,
                rule_id=RULE_ID,
                reason_code=finding.reason_code,
                inputs_used={
                    "organism": finding.organism,
                    "colony_count": organism.colony_count,
                    "specimen_type": organism.specimen_type,
                    "discharge_antibiotics": [d.drug_name for d in discharge_drugs],
                    "panel_size": len(sens),
                },
                detail={
                    "offending_drug": finding.offending_drug,
                    "alternatives_available": finding.alternatives_available,
                    "auto_close": finding.auto_close,
                    **finding.detail,
                },
            )
        )
    return outputs
