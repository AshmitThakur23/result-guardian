"""The encounter detail read. Phase 1.5. Read-only.

One transaction assembles the encounter, its patient, its attending doctor,
every order with its contract state, and the discharge medications. Three
separate calls would let those disagree -- an order can gain a contract
between them, and the screen would render a state that never existed.

The gate's answer comes from ``get_discharge_readiness``, the same function
the discharge action re-runs. It is not re-implemented here: a second copy of
the blocking rule is a second thing that can drift.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models.discharge import DischargeContract
from app.db.models.encounters import Encounter
from app.db.models.orders import ORDER_STATUSES_NOT_BLOCKING, Order
from app.db.models.organisation import User
from app.db.models.patients import Patient
from app.schemas.directory import PatientSummary, UserSummary
from app.schemas.encounter_detail import EncounterFullDetail
from app.schemas.orders import OrderRow
from app.services.discharge_readiness import (
    EncounterNotFoundError,
    get_discharge_readiness,
)
from app.services.medications import list_discharge_medications
from app.services.orders import ORDERABLE_ENCOUNTER_STATUS


async def get_encounter_full_detail(
    session: AsyncSession, encounter_id: uuid.UUID
) -> EncounterFullDetail:
    """Everything the detail screen renders."""
    attending = aliased(User)

    header = (
        await session.execute(
            select(Encounter, Patient, attending)
            .join(Patient, Patient.id == Encounter.patient_id)
            .outerjoin(attending, attending.id == Encounter.attending_doctor_id)
            .where(
                Encounter.id == encounter_id,
                Encounter.deleted_at.is_(None),
            )
        )
    ).first()

    if header is None:
        raise EncounterNotFoundError(str(encounter_id))

    encounter, patient, doctor = header

    responsible = aliased(User)
    order_rows = (
        await session.execute(
            select(Order, DischargeContract, responsible)
            .outerjoin(
                DischargeContract,
                (DischargeContract.order_id == Order.id)
                & (DischargeContract.deleted_at.is_(None)),
            )
            .outerjoin(
                responsible, responsible.id == DischargeContract.responsible_doctor_id
            )
            .where(
                Order.encounter_id == encounter_id,
                Order.deleted_at.is_(None),
            )
            .order_by(Order.ordered_at.desc())
        )
    ).all()

    orders = [
        OrderRow(
            id=order.id,
            test_code=order.test_code,
            test_name=order.test_name,
            category=order.category,
            status=order.status,
            ordered_at=order.ordered_at,
            sample_collected_at=order.sample_collected_at,
            expected_tat_hours=order.expected_tat_hours,
            external_order_id=order.external_order_id,
            is_outstanding=order.status not in ORDER_STATUSES_NOT_BLOCKING,
            contract_id=contract.id if contract is not None else None,
            responsible_doctor_id=(
                contract.responsible_doctor_id if contract is not None else None
            ),
            responsible_doctor_name=(owner.full_name if owner is not None else None),
            expected_by=contract.expected_by if contract is not None else None,
        )
        for order, contract, owner in order_rows
    ]

    readiness = await get_discharge_readiness(session, encounter_id)
    medications = await list_discharge_medications(session, encounter_id)

    return EncounterFullDetail(
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
            UserSummary.model_validate(doctor) if doctor is not None else None
        ),
        orders=orders,
        medications=medications,
        gate_applies=readiness.gate_applies,
        can_discharge=readiness.can_discharge,
        blocking_order_count=len(readiness.blocking_orders),
        can_add_orders=encounter.status == ORDERABLE_ENCOUNTER_STATUS,
    )
