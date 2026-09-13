"""Case ownership and acknowledgement. Phase 4.2 / 4.4.

Two endpoints that the escalation ladder turns on:

``POST /api/cases/{id}/reassign`` hands a case to someone else **with a
mandatory reason**, and deliberately does not touch the escalation timers. The
plan: *"Reassignment does not reset the escalation clock, otherwise it becomes
a dodge."*

``POST /api/cases/{id}/acknowledge`` is Exit Gate 4's second clause —
*"Acknowledging at any rung stops everything."* It cancels every remaining rung
in one transaction.

Scope: acknowledgement here means "a human has seen this and the ladder should
stop". The full closure workflow — reason codes, outcomes, reopening — is
Phase 5.3 and is not built here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.ownership import (
    AcknowledgeRequest,
    AcknowledgeResult,
    OwnerResolutionOut,
    PatientContactCreate,
    PatientContactRow,
    ReassignRequest,
    ReassignResult,
)
from app.services.escalation import acknowledge_case
from app.services.ownership import (
    CaseNotFoundError,
    ReassignmentReasonRequiredError,
    reassign_case,
    resolve_owner,
)

router = APIRouter(prefix="/cases", tags=["ownership"])


@router.post(
    "/{case_id}/reassign",
    response_model=ReassignResult,
    summary="Reassign a case — reason mandatory, escalation clock untouched",
)
async def reassign(
    case_id: uuid.UUID,
    payload: ReassignRequest,
    session: AsyncSession = Depends(get_session),
) -> ReassignResult:
    """Move a case to a different owner.

    **The escalation clock keeps running.** A case handed round three doctors
    escalates on the schedule set when it was flagged — passing it on is not
    progress, and the plan is explicit that resetting the clock here would turn
    the endpoint into a way to avoid the ladder.

    * **200** reassigned
    * **404** no such case
    * **422** missing or blank reason, or an unknown user
    """
    owner = (
        await session.execute(
            text("SELECT id FROM users WHERE id = :u AND deleted_at IS NULL"),
            {"u": str(payload.new_owner_id)},
        )
    ).first()
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No such user: {payload.new_owner_id}",
        )

    try:
        result = await reassign_case(
            session,
            case_id,
            new_owner_id=payload.new_owner_id,
            reason=payload.reason,
            actor_user_id=payload.reassigned_by,
        )
    except CaseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Case {case_id} not found"
        ) from exc
    except ReassignmentReasonRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await session.commit()
    return ReassignResult(
        case_id=result.case_id,
        previous_owner_id=result.previous_owner_id,
        new_owner_id=result.new_owner_id,
        reason=result.reason,
    )


@router.post(
    "/{case_id}/acknowledge",
    response_model=AcknowledgeResult,
    summary="Acknowledge a flag — stops every remaining escalation rung",
)
async def acknowledge(
    case_id: uuid.UUID,
    payload: AcknowledgeRequest,
    session: AsyncSession = Depends(get_session),
) -> AcknowledgeResult:
    """Exit Gate 4: *"Acknowledging at **any** rung stops everything."*

    Idempotent: acknowledging twice returns ``already_acknowledged`` rather
    than raising, because a double-click must not be an error.
    """
    try:
        result = await acknowledge_case(
            session,
            case_id,
            acknowledged_by=payload.acknowledged_by,
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Case {case_id} not found"
        ) from exc

    await session.commit()
    return AcknowledgeResult(
        case_id=result.case_id,
        cancelled_timer_ids=result.cancelled_timer_ids,
        already_acknowledged=result.already_acknowledged,
    )


@router.get(
    "/{case_id}/owner-resolution",
    response_model=OwnerResolutionOut,
    summary="Who this case would be routed to, and why — read-only",
)
async def owner_resolution(
    case_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> OwnerResolutionOut:
    """The six-step fallthrough, without side effects.

    ``record=False``: answering a question must not append to an append-only
    log. The ladder's own resolutions are what get recorded.
    """
    try:
        resolution = await resolve_owner(session, case_id, record=False)
    except CaseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Case {case_id} not found"
        ) from exc

    return OwnerResolutionOut(
        user_id=resolution.user_id,
        step=resolution.step,
        level=resolution.level,
        reason=resolution.reason,
        unresolved=resolution.unresolved,
        roster_fallthrough=resolution.is_fallthrough,
    )


@router.post(
    "/{case_id}/patient-contacts",
    response_model=PatientContactRow,
    status_code=status.HTTP_201_CREATED,
    summary="Record that the patient called back — Phase 4.6's inbound half",
)
async def record_patient_contact(
    case_id: uuid.UUID,
    payload: PatientContactCreate,
    session: AsyncSession = Depends(get_session),
) -> PatientContactRow:
    """*"Inbound: ``patient_contacts`` table so front desk can mark 'patient
    called back'."*

    Without this the ladder has no way to know the SMS worked, and the only
    evidence a patient responded lives in somebody's memory of a phone call.
    """
    case = (
        await session.execute(
            text(
                "SELECT id, patient_id FROM pending_cases "
                " WHERE id = :c AND deleted_at IS NULL"
            ),
            {"c": str(case_id)},
        )
    ).first()
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Case {case_id} not found"
        )
    if str(case.patient_id) != str(payload.patient_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="patient_id does not match the patient on this case",
        )

    from app.db.types import uuid7

    contact_id = uuid7()
    await session.execute(
        text(
            "INSERT INTO patient_contacts "
            "(id, case_id, patient_id, direction, recorded_by_user_id, note, "
            " created_by, updated_by) "
            "VALUES (:i, :c, :p, :d, :r, :n, :r, :r)"
        ),
        {
            "i": str(contact_id),
            "c": str(case_id),
            "p": str(payload.patient_id),
            "d": payload.direction,
            "r": (
                str(payload.recorded_by_user_id)
                if payload.recorded_by_user_id
                else None
            ),
            "n": payload.note,
        },
    )

    from app.services.lab_flags import record_event

    await record_event(
        session,
        case_id,
        "patient_contact_recorded",
        {"direction": payload.direction, "note": payload.note},
        actor_user_id=payload.recorded_by_user_id,
    )
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT id, case_id, patient_id, direction, contacted_at, note "
                "  FROM patient_contacts WHERE id = :i"
            ),
            {"i": str(contact_id)},
        )
    ).one()
    return PatientContactRow.model_validate(row)
