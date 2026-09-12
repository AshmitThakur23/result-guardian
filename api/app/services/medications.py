"""Discharge medication entry. Phase 1.5.

Capture only. Rule B (Phase 3) is the consumer; nothing here interprets a
drug, checks an interaction or validates a dose. See
``app/schemas/medications.py`` for why that restraint is deliberate.

**Allowed after discharge, on purpose.** A discharge summary is frequently
typed up after the patient has left, and a medication row changes nothing the
gate reads -- it neither blocks nor unblocks a discharge. Refusing it would
mean Rule B has nothing to compare against for exactly the encounters that
were busiest. The one thing that is refused is a soft-deleted encounter.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.discharge import DischargeMedication
from app.db.models.encounters import Encounter
from app.db.types import uuid7
from app.schemas.medications import (
    DischargeMedicationCreate,
    DischargeMedicationRow,
)
from app.services.discharge_readiness import EncounterNotFoundError


class DuplicateMedicationError(Exception):
    """The same drug is already recorded on this encounter. Router -> 409."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


async def add_discharge_medication(
    session: AsyncSession,
    encounter_id: uuid.UUID,
    payload: DischargeMedicationCreate,
    actor_user_id: uuid.UUID | None = None,
) -> DischargeMedicationRow:
    """Record one take-home drug against an encounter."""
    encounter = (
        await session.execute(
            select(Encounter.id).where(
                Encounter.id == encounter_id, Encounter.deleted_at.is_(None)
            )
        )
    ).first()
    if encounter is None:
        raise EncounterNotFoundError(str(encounter_id))

    # Not a database constraint: a patient can legitimately go home on the
    # same drug twice (different dose, different course), so UNIQUE would be
    # wrong. An identical drug *and* dose *and* frequency is a double-entry,
    # and silently storing it would double that drug's weight in Rule B.
    duplicate = (
        await session.execute(
            select(DischargeMedication.id).where(
                DischargeMedication.encounter_id == encounter_id,
                DischargeMedication.deleted_at.is_(None),
                DischargeMedication.drug_name.ilike(payload.drug_name),
                DischargeMedication.dose.is_not_distinct_from(payload.dose),
                DischargeMedication.frequency.is_not_distinct_from(payload.frequency),
            )
        )
    ).first()
    if duplicate is not None:
        raise DuplicateMedicationError(
            f"'{payload.drug_name}' is already recorded on this encounter with "
            "the same dose and frequency."
        )

    medication = DischargeMedication(
        id=uuid7(),
        encounter_id=encounter_id,
        drug_name=payload.drug_name,
        drug_code=payload.drug_code,
        atc_code=payload.atc_code,
        dose=payload.dose,
        route=payload.route,
        frequency=payload.frequency,
        duration_days=payload.duration_days,
        is_antibiotic=payload.is_antibiotic,
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(medication)
    await session.commit()

    return DischargeMedicationRow.model_validate(medication)


async def list_discharge_medications(
    session: AsyncSession, encounter_id: uuid.UUID
) -> list[DischargeMedicationRow]:
    """Every medication on an encounter, antibiotics first for Rule B's sake."""
    rows = (
        (
            await session.execute(
                select(DischargeMedication)
                .where(
                    DischargeMedication.encounter_id == encounter_id,
                    DischargeMedication.deleted_at.is_(None),
                )
                .order_by(
                    DischargeMedication.is_antibiotic.desc(),
                    DischargeMedication.drug_name,
                )
            )
        )
        .scalars()
        .all()
    )
    return [DischargeMedicationRow.model_validate(row) for row in rows]
