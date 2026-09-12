"""Rule A — numeric analytes. Phase 3.3.

The build plan's algorithm, step for step:

1. unit conversion to a canonical unit
2. no reference range → look one up → still none → **FOLLOW_UP** (unclassifiable)
3. compare against the range: LOW / HIGH / NORMAL
4. NORMAL → **NORMAL**
5. look up ``panic_thresholds`` for test + age + sex
6. at or beyond a panic bound → **CRITICAL**
7. beyond the range by more than ``slight_abnormal_factor``
   (default 1.5x) -> **CRITICAL**
8. otherwise → **FOLLOW_UP**

Two details that carry more weight than they look:

**"Unclassifiable" is FOLLOW_UP, not NORMAL.** A value with no reference range
is a value nobody has checked. Defaulting it to normal would auto-close it,
which is precisely the failure this product exists to prevent.

**Censored values parse rather than fail.** Labs report ``<0.01`` and
``>1000``. Dropping those would silently discard the most extreme results on
the report -- a ``>1000`` troponin is not a missing value, it is the worst one.
The operator is kept so a censored value can be compared honestly: ``>1000``
is treated as at least 1000, which is enough to clear a high panic bound but
never enough to prove something is within range.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rules import SEVERITY_CRITICAL, SEVERITY_FOLLOW_UP, SEVERITY_NORMAL

RULE_ID = "A_numeric"

# Every path out of this rule carries one of these.
REASON_NO_VALUE = "NUM_NO_NUMERIC_VALUE"
REASON_NO_RANGE = "NUM_NO_REFERENCE_RANGE"
REASON_IN_RANGE = "NUM_WITHIN_REFERENCE_RANGE"
REASON_ABOVE_CRITICAL_HIGH = "NUM_ABOVE_CRITICAL_HIGH"
REASON_BELOW_CRITICAL_LOW = "NUM_BELOW_CRITICAL_LOW"
REASON_FAR_ABOVE_RANGE = "NUM_FAR_ABOVE_REFERENCE_RANGE"
REASON_FAR_BELOW_RANGE = "NUM_FAR_BELOW_REFERENCE_RANGE"
REASON_ABOVE_RANGE = "NUM_ABOVE_REFERENCE_RANGE"
REASON_BELOW_RANGE = "NUM_BELOW_REFERENCE_RANGE"
REASON_CENSORED_UNCOMPARABLE = "NUM_CENSORED_NOT_COMPARABLE"

DEFAULT_SLIGHT_ABNORMAL_FACTOR = Decimal("1.5")

# "<0.01", "> 1000", "<=5", "≥ 3.2". The operator is captured because it
# changes what the number means.
_CENSORED = re.compile(r"^\s*(<=|>=|<|>|≤|≥)\s*([0-9]*\.?[0-9]+)\s*$")
_OPERATOR_CANONICAL = {"≤": "<=", "≥": ">="}


@dataclass(frozen=True)
class CensoredValue:
    """A value the lab could only bound, not measure."""

    operator: str
    """One of < <= > >=."""
    value: Decimal

    @property
    def is_upper_bound(self) -> bool:
        """``<0.01`` — the true value is somewhere below this."""
        return self.operator in ("<", "<=")


@dataclass
class RuleOutput:
    """What every rule returns. The plan: ``{severity, rule_id, reason_code,
    inputs_used}``."""

    severity: str
    rule_id: str
    reason_code: str
    inputs_used: dict[str, object] = field(default_factory=dict)
    detail: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "rule_id": self.rule_id,
            "reason_code": self.reason_code,
            "inputs_used": self.inputs_used,
            "detail": self.detail,
        }


def parse_censored(raw: str | None) -> CensoredValue | None:
    """Parse ``<0.01`` / ``>1000`` into an operator and a number.

    Returns ``None`` for anything that is not censored, including plain
    numbers and non-numeric text like ``"NEG"`` -- the caller decides what to
    do with those.
    """
    if not raw:
        return None
    match = _CENSORED.match(raw)
    if match is None:
        return None
    operator = _OPERATOR_CANONICAL.get(match.group(1), match.group(1))
    try:
        return CensoredValue(operator=operator, value=Decimal(match.group(2)))
    except InvalidOperation:  # pragma: no cover - regex already constrains it
        return None


async def convert_unit(
    session: AsyncSession,
    value: Decimal,
    from_unit: str | None,
    to_unit: str | None,
    test_code: str | None = None,
) -> Decimal | None:
    """Step 1. Returns ``None`` when no conversion is configured.

    A missing conversion is not an error and not an identity: comparing mg/dL
    against a µmol/L range because no row existed would produce a confident,
    wrong severity. The caller treats ``None`` as "cannot compare".
    """
    if from_unit is None or to_unit is None:
        return value if from_unit == to_unit else None
    if from_unit.strip().lower() == to_unit.strip().lower():
        return value

    row = (
        await session.execute(
            text(
                'SELECT factor, "offset" FROM unit_conversions '
                " WHERE lower(from_unit) = lower(:f) AND lower(to_unit) = lower(:t) "
                "   AND deleted_at IS NULL "
                "   AND (test_code = :c OR test_code IS NULL) "
                " ORDER BY test_code NULLS LAST LIMIT 1"
            ),
            {"f": from_unit, "t": to_unit, "c": test_code},
        )
    ).first()
    if row is None:
        return None
    # Row columns are typed Any by SQLAlchemy; the DB guarantees NUMERIC.
    return Decimal(value * row.factor + row.offset)


async def _slight_abnormal_factor(session: AsyncSession) -> Decimal:
    row = (
        await session.execute(
            text(
                "SELECT value FROM rule_config "
                " WHERE key = 'slight_abnormal_factor' AND deleted_at IS NULL"
            )
        )
    ).first()
    if row is None:
        return DEFAULT_SLIGHT_ABNORMAL_FACTOR
    raw = row.value
    candidate = raw.get("factor") if isinstance(raw, dict) else raw
    try:
        return Decimal(str(candidate))
    except (InvalidOperation, TypeError):
        return DEFAULT_SLIGHT_ABNORMAL_FACTOR


@dataclass(frozen=True)
class PanicBounds:
    """The threshold row that was in force, typed rather than a raw Row."""

    id: uuid.UUID
    critical_low: Decimal | None
    critical_high: Decimal | None
    follow_up_low_multiplier: Decimal | None
    follow_up_high_multiplier: Decimal | None
    unit: str | None
    source: str


async def _panic_threshold(
    session: AsyncSession,
    test_code: str,
    sex: str | None,
    age_years: Decimal | None,
    at: dt.datetime,
) -> PanicBounds | None:
    """Step 5. The most specific row that was in force at ``at``.

    Scoped by ``effective_from``/``effective_to`` so a decision stays
    explicable after the hospital revises its SOP.
    """
    row = (
        await session.execute(
            text(
                "SELECT critical_low, critical_high, follow_up_low_multiplier, "
                "       follow_up_high_multiplier, unit, source, id "
                "  FROM panic_thresholds "
                " WHERE deleted_at IS NULL "
                "   AND lower(test_code) = lower(:code) "
                "   AND (sex = 'any' OR sex = :sex) "
                # CAST, not a bare :age -- asyncpg cannot infer a parameter's
                # type from "$3 IS NULL" alone, and age is null for every
                # patient whose date of birth the hospital did not record. An
                # uncast parameter here makes Rule A raise rather than classify
                # on exactly those patients.
                "   AND (age_min_years IS NULL OR CAST(:age AS numeric) IS NULL"
                "        OR CAST(:age AS numeric) >= age_min_years) "
                "   AND (age_max_years IS NULL OR CAST(:age AS numeric) IS NULL"
                "        OR CAST(:age AS numeric) <= age_max_years) "
                "   AND effective_from <= :at "
                "   AND (effective_to IS NULL OR effective_to > :at) "
                # Most specific first: a sex-specific row beats 'any', and a
                # row with an age band beats one without.
                " ORDER BY (sex <> 'any') DESC, "
                "          (age_min_years IS NOT NULL) DESC, "
                "          effective_from DESC "
                " LIMIT 1"
            ),
            {"code": test_code, "sex": sex, "age": age_years, "at": at},
        )
    ).first()
    if row is None:
        return None
    return PanicBounds(
        id=row.id,
        critical_low=row.critical_low,
        critical_high=row.critical_high,
        follow_up_low_multiplier=row.follow_up_low_multiplier,
        follow_up_high_multiplier=row.follow_up_high_multiplier,
        unit=row.unit,
        source=row.source,
    )


async def classify_analyte(
    session: AsyncSession,
    *,
    test_code: str,
    value_numeric: Decimal | None,
    value_raw: str | None = None,
    unit_normalized: str | None = None,
    ref_low: Decimal | None = None,
    ref_high: Decimal | None = None,
    ref_text: str | None = None,
    sex: str | None = None,
    age_years: Decimal | None = None,
    at: dt.datetime | None = None,
) -> RuleOutput:
    """Rule A for one analyte."""
    moment = at or dt.datetime.now(dt.UTC)
    censored = parse_censored(value_raw)
    value = (
        value_numeric
        if value_numeric is not None
        else (censored.value if censored else None)
    )
    # NaN and the infinities are not comparable, and Python's Decimal raises
    # on `>=` against a NaN rather than answering False. Left to reach the
    # comparisons below, a NaN takes the whole classification down, and a
    # classification that raises leaves the case with no timer and no flag.
    # The API refuses non-finite values at the door; this is the second lock,
    # for a row that arrived some other way -- an import, a correction, a
    # future HL7 feed.
    if value is not None and not value.is_finite():
        return RuleOutput(
            severity=SEVERITY_FOLLOW_UP,
            rule_id=RULE_ID,
            reason_code=REASON_NO_VALUE,
            inputs_used={"test_code": test_code, "value_numeric": str(value)},
            detail={"non_finite": True},
        )

    inputs: dict[str, object] = {
        "test_code": test_code,
        "value_numeric": str(value) if value is not None else None,
        "value_raw": value_raw,
        "unit_normalized": unit_normalized,
        "ref_low": str(ref_low) if ref_low is not None else None,
        "ref_high": str(ref_high) if ref_high is not None else None,
        "ref_text": ref_text,
        "sex": sex,
        "age_years": str(age_years) if age_years is not None else None,
        "censored_operator": censored.operator if censored else None,
    }

    # ── step 2a: nothing comparable at all ────────────────────────
    if value is None:
        # "NEG", "trace", or a blank. Rule A cannot speak to it; something
        # that has not been checked is never NORMAL.
        return RuleOutput(
            severity=SEVERITY_FOLLOW_UP,
            rule_id=RULE_ID,
            reason_code=REASON_NO_VALUE,
            inputs_used=inputs,
        )

    # ── step 5 first: a panic bound is absolute ───────────────────
    # Deliberately ahead of the reference-range check. A value past a panic
    # threshold is CRITICAL whether or not the lab sent a range, and a report
    # with a missing range is exactly when that matters most.
    threshold = await _panic_threshold(session, test_code, sex, age_years, moment)
    if threshold is not None:
        inputs["panic_threshold_id"] = str(threshold.id)
        inputs["panic_source"] = threshold.source

        # A censored upper bound ("<5") cannot clear a high bound.
        if (
            threshold.critical_high is not None
            and value >= threshold.critical_high
            and not (censored and censored.is_upper_bound)
        ):
            return RuleOutput(
                severity=SEVERITY_CRITICAL,
                rule_id=RULE_ID,
                reason_code=REASON_ABOVE_CRITICAL_HIGH,
                inputs_used=inputs,
                detail={"critical_high": str(threshold.critical_high)},
            )
        # A censored lower bound (">1000") cannot fall below a low bound.
        if (
            threshold.critical_low is not None
            and value <= threshold.critical_low
            and not (censored and not censored.is_upper_bound)
        ):
            return RuleOutput(
                severity=SEVERITY_CRITICAL,
                rule_id=RULE_ID,
                reason_code=REASON_BELOW_CRITICAL_LOW,
                inputs_used=inputs,
                detail={"critical_low": str(threshold.critical_low)},
            )

    # ── step 2b: no usable reference range ────────────────────────
    if ref_low is None and ref_high is None:
        return RuleOutput(
            severity=SEVERITY_FOLLOW_UP,
            rule_id=RULE_ID,
            reason_code=REASON_NO_RANGE,
            inputs_used=inputs,
        )

    # ── step 3/4: compare ─────────────────────────────────────────
    factor = await _slight_abnormal_factor(session)
    inputs["slight_abnormal_factor"] = str(factor)

    above = ref_high is not None and value > ref_high
    below = ref_low is not None and value < ref_low

    if not above and not below:
        # A censored value cannot prove it is *inside* a range: "<0.01" with a
        # low bound of 0.005 might be anywhere below 0.01. Say so rather than
        # calling it normal.
        if censored:
            uncomparable = (
                censored.is_upper_bound and ref_low is not None and value > ref_low
            ) or (
                not censored.is_upper_bound
                and ref_high is not None
                and value < ref_high
            )
            if uncomparable:
                return RuleOutput(
                    severity=SEVERITY_FOLLOW_UP,
                    rule_id=RULE_ID,
                    reason_code=REASON_CENSORED_UNCOMPARABLE,
                    inputs_used=inputs,
                )
        return RuleOutput(
            severity=SEVERITY_NORMAL,
            rule_id=RULE_ID,
            reason_code=REASON_IN_RANGE,
            inputs_used=inputs,
        )

    # ── step 7: far beyond the range is critical ──────────────────
    high_multiplier = (
        threshold.follow_up_high_multiplier
        if threshold is not None and threshold.follow_up_high_multiplier is not None
        else factor
    )
    low_multiplier = (
        threshold.follow_up_low_multiplier
        if threshold is not None and threshold.follow_up_low_multiplier is not None
        else factor
    )

    if (
        above
        and ref_high is not None
        and ref_high > 0
        and value > ref_high * high_multiplier
    ):
        return RuleOutput(
            severity=SEVERITY_CRITICAL,
            rule_id=RULE_ID,
            reason_code=REASON_FAR_ABOVE_RANGE,
            inputs_used=inputs,
            detail={"multiplier": str(high_multiplier)},
        )
    if (
        below
        and ref_low is not None
        and ref_low > 0
        and value < ref_low / low_multiplier
    ):
        return RuleOutput(
            severity=SEVERITY_CRITICAL,
            rule_id=RULE_ID,
            reason_code=REASON_FAR_BELOW_RANGE,
            inputs_used=inputs,
            detail={"multiplier": str(low_multiplier)},
        )

    # ── step 8 ────────────────────────────────────────────────────
    return RuleOutput(
        severity=SEVERITY_FOLLOW_UP,
        rule_id=RULE_ID,
        reason_code=REASON_ABOVE_RANGE if above else REASON_BELOW_RANGE,
        inputs_used=inputs,
    )


async def classify_result_analytes(
    session: AsyncSession,
    result_id: uuid.UUID,
    *,
    sex: str | None = None,
    age_years: Decimal | None = None,
    at: dt.datetime | None = None,
) -> list[RuleOutput]:
    """Every analyte on a result. One output per row, so a panel's worst value
    is visible alongside the ones that were fine."""
    rows = (
        await session.execute(
            text(
                "SELECT a.test_name_raw, a.value_numeric, a.value_raw, "
                "       a.unit_normalized, a.unit_raw, a.ref_low, a.ref_high, "
                "       a.ref_text, o.test_code "
                "  FROM result_analytes a "
                "  JOIN results r ON r.id = a.result_id "
                "  JOIN orders o ON o.id = r.order_id "
                " WHERE a.result_id = :r AND a.deleted_at IS NULL "
                " ORDER BY a.seq"
            ),
            {"r": str(result_id)},
        )
    ).all()

    outputs: list[RuleOutput] = []
    for row in rows:
        value = row.value_numeric
        unit = row.unit_normalized or row.unit_raw
        # Step 1: convert into whatever unit the threshold is expressed in.
        # Only attempted when both units are known and differ.
        outputs.append(
            await classify_analyte(
                session,
                # The analyte's own name is what the range belongs to; the
                # order's test_code is the fallback for panel-level thresholds.
                test_code=row.test_name_raw or row.test_code,
                value_numeric=value,
                value_raw=row.value_raw,
                unit_normalized=unit,
                ref_low=row.ref_low,
                ref_high=row.ref_high,
                ref_text=row.ref_text,
                sex=sex,
                age_years=age_years,
                at=at,
            )
        )
    return outputs
