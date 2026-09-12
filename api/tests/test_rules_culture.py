"""Rule B — culture and sensitivity. Phase 3.4, ★ highest clinical value.

    **R → CRITICAL** — the patient is at home on an ineffective drug.

That is the product, so it gets the most tests. The cases that matter:

* the **Exit Gate 3** case — E. coli resistant to the discharge antibiotic,
  reported under its Indian brand name, must come out **CRITICAL**;
* a **contaminant** is FOLLOW_UP and must **never** auto-close;
* an MDRO is CRITICAL **regardless** of what was prescribed, and is checked
  *before* the contaminant branch so an MRSA that looks like skin flora is
  still critical;
* a discharge drug the synonym table cannot map is **reported**, never
  silently treated as covered — that false negative is indistinguishable
  from a clean result.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.rules import SEVERITY_CRITICAL, SEVERITY_FOLLOW_UP, SEVERITY_NORMAL
from app.rules.culture import (
    REASON_CONTAMINANT,
    REASON_COVERED,
    REASON_DRUG_NOT_ON_PANEL,
    REASON_INTERMEDIATE_TO_DISCHARGE_DRUG,
    REASON_MDRO,
    REASON_NO_DISCHARGE_ANTIBIOTIC,
    REASON_NO_GROWTH,
    REASON_NO_SENSITIVITY_DATA,
    REASON_RESISTANT_TO_DISCHARGE_DRUG,
    DischargeDrug,
    classify_organism,
    normalize_antibiotic,
    parse_colony_count,
)
from scripts.seed_rules_dev import _seed as seed_rules

pytestmark = pytest.mark.integration


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


D = Decimal


# ── colony counts ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (">100,000 CFU/mL", D("100000")),
        ("100000", D("100000")),
        ("25000", D("25000")),
        ("10^5", D("100000")),
        (">10^5 CFU/mL", D("100000")),
        ("10^3", D("1000")),
        ("1.5 x 10^5", D("150000")),
        # U+00D7, the multiplication sign a lab actually prints. Built with
        # chr() so the source file stays plain ASCII.
        (f"1.5 {chr(0x00D7)} 10^5", D("150000")),
        ("10e5", D("100000")),
        ("10**4", D("10000")),
    ],
)
def test_colony_counts_parse_to_the_right_order_of_magnitude(
    raw: str, expected: Decimal
) -> None:
    """Regression. ``1.5 x 10^5`` once parsed as **1.5**, which put a heavy
    growth under the contaminant threshold -- and the contaminant branch
    returns before the resistance comparison, so a resistant organism would
    have been reported as likely contamination instead of CRITICAL."""
    assert parse_colony_count(raw) == expected


@pytest.mark.parametrize("raw", ["scanty", "moderate growth", "", None, "many"])
def test_an_unreported_colony_count_is_none_not_zero(raw: str | None) -> None:
    """None means "not reported". Treating it as a low count would call
    contamination on evidence nobody produced."""
    assert parse_colony_count(raw) is None


# ── synonym mapping ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("brand", "generic"),
    [
        ("Monocef", "ceftriaxone"),
        ("monocef", "ceftriaxone"),
        ("Augmentin", "amoxicillin-clavulanate"),
        ("Amoxyclav", "amoxicillin-clavulanate"),
        ("Ciplox", "ciprofloxacin"),
        ("Taxim", "cefotaxime"),
        ("Septran", "trimethoprim-sulfamethoxazole"),
        # A generic prescribed under its own name still resolves.
        ("Ceftriaxone", "ceftriaxone"),
    ],
)
async def test_indian_brand_names_map_to_generics(
    session: AsyncSession, brand: str, generic: str
) -> None:
    """The discharge summary says "Monocef"; the sensitivity grid says
    "Ceftriaxone". Without this the resistant case is missed silently."""
    mapped, atc = await normalize_antibiotic(session, brand)
    assert mapped == generic
    assert atc


async def test_an_unknown_drug_maps_to_nothing_rather_than_itself(
    session: AsyncSession,
) -> None:
    assert await normalize_antibiotic(session, "Fictionalcillin") == (None, None)


# ── the case the product exists for ───────────────────────────────────


async def test_resistant_to_the_discharge_antibiotic_is_critical(
    session: AsyncSession,
) -> None:
    """Exit Gate 3: *"A culture resistant to a discharge antibiotic produces
    CRITICAL."* Reported under a brand name, resisted under a generic one."""
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[
            ("Ceftriaxone", "R"),
            ("Nitrofurantoin", "S"),
            ("Meropenem", "S"),
        ],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.reason_code == REASON_RESISTANT_TO_DISCHARGE_DRUG
    assert finding.offending_drug == "Monocef"
    assert finding.auto_close is False
    # The doctor needs to know what to switch to, not just that it is wrong.
    assert finding.alternatives_available == ["meropenem", "nitrofurantoin"]


async def test_a_heavy_growth_in_scientific_notation_is_still_critical(
    session: AsyncSession,
) -> None:
    """The colony-count regression, asserted at the level that matters: the
    severity, not the parsed number."""
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count="1.5 x 10^5 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Ciprofloxacin", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Ciplox")],
    )
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.reason_code == REASON_RESISTANT_TO_DISCHARGE_DRUG


async def test_intermediate_to_the_discharge_antibiotic_is_follow_up(
    session: AsyncSession,
) -> None:
    finding = await classify_organism(
        session,
        organism_name="Klebsiella pneumoniae",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Levofloxacin", "I"), ("Meropenem", "S")],
        discharge_drugs=[DischargeDrug(drug_name="Levoflox")],
    )
    assert finding.severity == SEVERITY_FOLLOW_UP
    assert finding.reason_code == REASON_INTERMEDIATE_TO_DISCHARGE_DRUG
    assert finding.offending_drug == "Levoflox"
    assert finding.auto_close is False


async def test_every_discharge_drug_susceptible_is_normal_and_auto_closes(
    session: AsyncSession,
) -> None:
    """Step 5 -- the only place in Rule B that may auto-close a live organism,
    and it logs why."""
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Ceftriaxone", "S"), ("Nitrofurantoin", "S")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_NORMAL
    assert finding.reason_code == REASON_COVERED
    assert finding.auto_close is True
    assert finding.detail["logged_reason"]


async def test_one_resistant_drug_outranks_several_susceptible_ones(
    session: AsyncSession,
) -> None:
    """Conflicting evidence. Being covered by two drugs does not make being
    uncovered by a third acceptable -- the patient is taking all three."""
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[
            ("Nitrofurantoin", "S"),
            ("Ceftriaxone", "S"),
            ("Ciprofloxacin", "R"),
        ],
        discharge_drugs=[
            DischargeDrug(drug_name="Nitrofurantoin"),
            DischargeDrug(drug_name="Monocef"),
            DischargeDrug(drug_name="Ciplox"),
        ],
    )
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.offending_drug == "Ciplox"


# ── the quiet failure modes ───────────────────────────────────────────


async def test_a_discharge_drug_not_on_the_panel_is_reported(
    session: AsyncSession,
) -> None:
    """The dangerous silence. The lab never tested it, so the engine cannot
    say it is covered -- and "no news" must not read as "good news"."""
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Nitrofurantoin", "S")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_FOLLOW_UP
    assert finding.reason_code == REASON_DRUG_NOT_ON_PANEL
    assert finding.detail["unmatched_drugs"] == ["Monocef"]
    assert finding.auto_close is False


async def test_an_unmappable_brand_is_reported_not_assumed_covered(
    session: AsyncSession,
) -> None:
    """A brand the synonym table has never heard of looks exactly like a drug
    that was not tested, and is treated the same way."""
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Nitrofurantoin", "S")],
        discharge_drugs=[DischargeDrug(drug_name="Zoxil-XR")],
    )
    assert finding.severity == SEVERITY_FOLLOW_UP
    assert finding.reason_code == REASON_DRUG_NOT_ON_PANEL


async def test_an_organism_with_no_sensitivity_panel_is_follow_up(
    session: AsyncSession,
) -> None:
    """Something grew and nobody tested anything against it. Unanswerable is
    not the same as fine."""
    finding = await classify_organism(
        session,
        organism_name="Pseudomonas aeruginosa",
        colony_count=">100,000 CFU/mL",
        specimen_type="wound swab",
        sensitivities=[],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_FOLLOW_UP
    assert finding.reason_code == REASON_NO_SENSITIVITY_DATA
    assert finding.auto_close is False


async def test_a_significant_organism_with_no_prescription_is_follow_up(
    session: AsyncSession,
) -> None:
    """Step 4. Nobody sent the patient home on anything, and something grew."""
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Nitrofurantoin", "S"), ("Ceftriaxone", "R")],
        discharge_drugs=[],
    )
    assert finding.severity == SEVERITY_FOLLOW_UP
    assert finding.reason_code == REASON_NO_DISCHARGE_ANTIBIOTIC
    assert finding.auto_close is False
    assert finding.alternatives_available == ["nitrofurantoin"]


# ── no growth ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "organism",
    [
        "No growth",
        "NO GROWTH after 48 hours",
        "Sterile",
        "No organisms isolated",
        "Culture negative",
        "No significant growth",
    ],
)
async def test_no_growth_is_normal_and_auto_closes(
    session: AsyncSession, organism: str
) -> None:
    finding = await classify_organism(
        session,
        organism_name=organism,
        colony_count=None,
        specimen_type="urine",
        sensitivities=[],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_NORMAL
    assert finding.reason_code == REASON_NO_GROWTH
    assert finding.auto_close is True


# ── contaminants ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "organism",
    [
        "Mixed flora",
        "Coagulase negative Staphylococcus",
        "Staphylococcus epidermidis",
        "Diphtheroids",
        "Micrococcus species",
    ],
)
async def test_a_contaminant_is_follow_up_and_never_auto_closes(
    session: AsyncSession, organism: str
) -> None:
    """Exit Gate 3: *"A contaminant culture produces FOLLOW_UP and does not
    auto-close."* Both halves matter -- the severity keeps the ward's trust,
    the auto-close rule keeps the patient safe."""
    finding = await classify_organism(
        session,
        organism_name=organism,
        colony_count="30,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Vancomycin", "S")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_FOLLOW_UP
    assert finding.reason_code == REASON_CONTAMINANT
    assert finding.auto_close is False


async def test_a_low_urine_colony_count_is_a_contaminant(
    session: AsyncSession,
) -> None:
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count="5,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Ceftriaxone", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_FOLLOW_UP
    assert finding.reason_code == REASON_CONTAMINANT
    assert finding.detail["low_colony_count"] is True


async def test_a_low_count_from_a_sterile_site_is_not_dismissed(
    session: AsyncSession,
) -> None:
    """A colony-count threshold only means something for urine. Two colonies
    in a blood culture are not reassuring, and here they are also resistant
    to what the patient is taking."""
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count="500 CFU/mL",
        specimen_type="blood",
        sensitivities=[("Ceftriaxone", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.reason_code == REASON_RESISTANT_TO_DISCHARGE_DRUG


async def test_an_unreported_colony_count_does_not_imply_contamination(
    session: AsyncSession,
) -> None:
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count="scanty growth",
        specimen_type="urine",
        sensitivities=[("Ceftriaxone", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_CRITICAL


async def test_the_contaminant_threshold_comes_from_the_table(
    session: AsyncSession,
) -> None:
    """Configuration lives in tables. Raise the threshold and a count that was
    significant becomes contamination -- without touching code."""
    await session.execute(
        text(
            'UPDATE rule_config SET value = \'{"threshold": "1000000"}\'::jsonb '
            " WHERE key = 'culture_contaminant_threshold'"
        )
    )
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Ceftriaxone", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.reason_code == REASON_CONTAMINANT
    assert finding.detail["threshold"] == "1000000"


# ── MDRO ──────────────────────────────────────────────────────────────


async def test_an_mdro_is_critical_regardless_of_the_prescription(
    session: AsyncSession,
) -> None:
    """Step 6. Nothing was prescribed, everything on the panel is susceptible,
    and it is still critical -- the infection-control consequence does not
    depend on what the patient is taking."""
    finding = await classify_organism(
        session,
        organism_name="MRSA (methicillin-resistant Staphylococcus aureus)",
        colony_count=">100,000 CFU/mL",
        specimen_type="wound swab",
        sensitivities=[("Vancomycin", "S"), ("Linezolid", "S")],
        discharge_drugs=[],
    )
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.reason_code == REASON_MDRO
    assert finding.detail["mdro_code"] == "MRSA"
    assert finding.auto_close is False
    assert finding.alternatives_available == ["linezolid", "vancomycin"]


async def test_an_mdro_is_detected_from_the_panel_not_just_the_name(
    session: AsyncSession,
) -> None:
    """The lab reports "Staphylococcus aureus" and an oxacillin R. Nobody typed
    "MRSA" anywhere; the rule reads the panel."""
    finding = await classify_organism(
        session,
        organism_name="Staphylococcus aureus",
        colony_count=">100,000 CFU/mL",
        specimen_type="blood",
        sensitivities=[("Oxacillin", "R"), ("Vancomycin", "S")],
        discharge_drugs=[DischargeDrug(drug_name="Augmentin")],
    )
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.reason_code == REASON_MDRO
    assert finding.detail["mdro_code"] == "MRSA_BY_PANEL"


async def test_a_carbapenem_resistant_klebsiella_is_critical(
    session: AsyncSession,
) -> None:
    finding = await classify_organism(
        session,
        organism_name="Klebsiella pneumoniae",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Meronem", "R"), ("Colistin", "S")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.reason_code == REASON_MDRO
    assert finding.detail["mdro_code"] == "CRE_BY_PANEL"


async def test_the_same_organism_without_the_resistance_is_not_an_mdro(
    session: AsyncSession,
) -> None:
    """The negative case for the rule above. A Klebsiella susceptible to
    meropenem is not a CRE, and calling it one would be the noise problem."""
    finding = await classify_organism(
        session,
        organism_name="Klebsiella pneumoniae",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Meropenem", "S"), ("Ceftriaxone", "S")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    assert finding.reason_code == REASON_COVERED
    assert finding.severity == SEVERITY_NORMAL


async def test_an_mdro_that_looks_like_skin_flora_is_still_critical(
    session: AsyncSession,
) -> None:
    """Ordering, asserted. MDRO is checked before the contaminant branch, so a
    methicillin-resistant staphylococcus is never dismissed as flora."""
    finding = await classify_organism(
        session,
        organism_name="Methicillin-resistant Staphylococcus epidermidis",
        colony_count="2,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Vancomycin", "S")],
        discharge_drugs=[],
    )
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.reason_code == REASON_MDRO


async def test_a_broken_mdro_regex_does_not_take_the_engine_down(
    session: AsyncSession,
) -> None:
    await session.execute(
        text(
            "UPDATE mdro_rules SET organism_pattern = '((((unclosed' "
            " WHERE code = 'ESBL'"
        )
    )
    finding = await classify_organism(
        session,
        organism_name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen_type="urine",
        sensitivities=[("Ceftriaxone", "R")],
        discharge_drugs=[DischargeDrug(drug_name="Monocef")],
    )
    # The other rules still ran, and the resistance was still caught.
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.reason_code == REASON_RESISTANT_TO_DISCHARGE_DRUG


async def test_an_inactive_mdro_rule_is_not_applied(session: AsyncSession) -> None:
    """Configuration is editable, and disabling a rule must actually disable
    it -- otherwise the table is decoration."""
    await session.execute(
        text("UPDATE mdro_rules SET active = false WHERE code = 'MRSA'")
    )
    finding = await classify_organism(
        session,
        organism_name="MRSA",
        colony_count=">100,000 CFU/mL",
        specimen_type="wound swab",
        sensitivities=[("Vancomycin", "S")],
        discharge_drugs=[DischargeDrug(drug_name="Vancocin")],
    )
    assert finding.reason_code != REASON_MDRO
