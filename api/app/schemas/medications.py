"""Discharge medications. Phase 1.5.

*"Discharge medication entry form — required for Rule B later."* That is the
whole reason this exists now: Phase 3's Rule B compares a culture's
sensitivity grid against what the patient actually went home on, and an
organism resistant to a drug the patient is taking is the CRITICAL case the
product exists to catch. The build plan is blunt -- *"Capture it now or Rule B
has nothing to compare against."*

So this captures, and only captures. **No interaction checking, no dose
validation, no formulary lookup, no AI.** Those are clinical decisions this
phase has no mandate to make, and a half-right one would be worse than none.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The schema stores NUMERIC(5,1) and CHECKs > 0. A year of take-home course is
# already implausible; anything longer is a typo, not a prescription.
MAX_DURATION_DAYS = Decimal("365")


class DischargeMedicationCreate(BaseModel):
    """One take-home drug.

    Only ``drug_name`` is required, matching the table: a ward clerk copying a
    handwritten discharge summary often has the drug and the frequency and
    nothing else, and refusing the row would mean Rule B gets nothing rather
    than something.
    """

    model_config = ConfigDict(extra="forbid")

    drug_name: str = Field(min_length=1, max_length=300)
    drug_code: str | None = Field(default=None, max_length=64)
    atc_code: str | None = Field(default=None, max_length=16)
    dose: str | None = Field(default=None, max_length=64)
    route: str | None = Field(default=None, max_length=32)
    frequency: str | None = Field(default=None, max_length=64)
    duration_days: Decimal | None = Field(
        default=None, description="Must be greater than 0 when given."
    )
    is_antibiotic: bool = Field(
        default=False,
        description=(
            "Rule B only considers antibiotics. Flagged here so that lookup "
            "does not have to re-derive the answer from the drug name."
        ),
    )

    @field_validator("duration_days")
    @classmethod
    def _positive_duration(cls, value: Decimal | None) -> Decimal | None:
        # Mirrors ck_discharge_medications_duration_positive, so a bad value
        # comes back as a field error rather than an IntegrityError.
        if value is None:
            return value
        if value <= 0:
            raise ValueError("duration_days must be greater than 0")
        if value > MAX_DURATION_DAYS:
            raise ValueError(f"duration_days must be at most {MAX_DURATION_DAYS}")
        return value

    @field_validator("drug_name", "drug_code", "atc_code", "dose", "route", "frequency")
    @classmethod
    def _no_blank_strings(cls, value: str | None) -> str | None:
        if value is None:
            return value
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("must not be blank")
        return trimmed


class DischargeMedicationRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    encounter_id: uuid.UUID
    drug_name: str
    drug_code: str | None = None
    atc_code: str | None = None
    dose: str | None = None
    route: str | None = None
    frequency: str | None = None
    duration_days: Decimal | None = None
    is_antibiotic: bool
