"""Read-only directory lookups for the discharge gate. Phase 1.3 addendum.

No availability here. Whether a doctor is *on duty* comes from
``duty_roster`` and ``user_absences``, which are Phase 4.1 and do not exist
yet. ``is_active`` is the only liveness signal the schema currently carries,
and the gate screen must not imply more than that.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models.encounters import Encounter
from app.db.models.organisation import User
from app.db.models.patients import Patient
from app.schemas.directory import (
    EncounterDetail,
    PatientSummary,
    UserSummary,
)
from app.services.discharge_readiness import EncounterNotFoundError

MAX_DIRECTORY_RESULTS = 100


async def list_users(
    session: AsyncSession,
    role: str | None = None,
    query: str | None = None,
    active_only: bool = True,
    limit: int = 50,
) -> list[UserSummary]:
    """Search staff by name or employee code.

    ``active_only`` defaults to true: the contract endpoint refuses an
    inactive doctor anyway, so offering one in the picker would only invite a
    422 the doctor cannot explain.
    """
    stmt = select(User).where(User.deleted_at.is_(None))

    if role is not None:
        stmt = stmt.where(User.role == role)
    if active_only:
        stmt = stmt.where(User.is_active.is_(True))
    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(User.full_name.ilike(pattern), User.employee_code.ilike(pattern))
        )

    stmt = stmt.order_by(User.full_name).limit(min(limit, MAX_DIRECTORY_RESULTS))
    rows = (await session.execute(stmt)).scalars().all()
    return [UserSummary.model_validate(u) for u in rows]


async def get_encounter_detail(
    session: AsyncSession, encounter_id: uuid.UUID
) -> EncounterDetail:
    """The encounter, its patient, and its attending doctor.

    One query with two joins rather than three round trips. The attending
    doctor is an outer join -- ``attending_doctor_id`` is nullable, and a gate
    screen must still render for an encounter that has not been assigned one.
    """
    doctor = aliased(User)

    row = (
        await session.execute(
            select(Encounter, Patient, doctor)
            .join(Patient, Patient.id == Encounter.patient_id)
            .outerjoin(doctor, doctor.id == Encounter.attending_doctor_id)
            .where(
                Encounter.id == encounter_id,
                Encounter.deleted_at.is_(None),
            )
        )
    ).first()

    if row is None:
        raise EncounterNotFoundError(str(encounter_id))

    encounter, patient, attending = row

    return EncounterDetail(
        id=encounter.id,
        encounter_no=encounter.encounter_no,
        type=encounter.type,
        status=encounter.status,
        admitted_at=encounter.admitted_at,
        discharged_at=encounter.discharged_at,
        ward=encounter.ward,
        bed=encounter.bed,
        department_id=encounter.department_id,
        patient=PatientSummary.model_validate(patient),
        attending_doctor=(
            UserSummary.model_validate(attending) if attending is not None else None
        ),
    )
