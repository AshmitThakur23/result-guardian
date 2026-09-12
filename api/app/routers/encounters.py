"""Encounter endpoints. Phase 1.3.

Only the readiness read lives here so far. The three actions that follow it in
the build plan -- bulk contract creation, the discharge action itself, and the
override path -- are separate 1.3 tasks and are deliberately absent.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.discharge import (
    DischargeContractsCreate,
    DischargeContractsCreated,
    DischargeReadiness,
    DischargeResult,
)
from app.services.discharge_action import (
    DischargeBlockedError,
    EncounterNotDischargeableError,
    discharge_encounter,
)
from app.services.discharge_contracts import (
    ContractConflictError,
    ContractValidationError,
    create_discharge_contracts,
)
from app.services.discharge_readiness import (
    EncounterNotFoundError,
    get_discharge_readiness,
)

router = APIRouter(prefix="/encounters", tags=["encounters"])


@router.get(
    "/{encounter_id}/discharge-readiness",
    response_model=DischargeReadiness,
    summary="Can this encounter be discharged, and what is outstanding?",
)
async def discharge_readiness(
    encounter_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> DischargeReadiness:
    """Read-only. Derived from current database state on every call.

    Side-effect free by construction: the service issues two SELECTs and
    nothing else. No contract is created here, no pending case is opened and
    no order is touched -- those belong to the POST endpoints that follow in
    Phase 1.3.

    ``can_discharge`` is computed server-side and cannot be supplied,
    overridden or cached by the caller.
    """
    try:
        return await get_discharge_readiness(session, encounter_id)
    except EncounterNotFoundError as exc:
        # Handled into RFC 7807 problem+json by the global handler in
        # app/errors.py, like every other error in this service.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Encounter {encounter_id} not found",
        ) from exc


@router.post(
    "/{encounter_id}/discharge-contracts",
    response_model=DischargeContractsCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create discharge contracts in bulk — all-or-nothing",
)
async def create_contracts(
    encounter_id: uuid.UUID,
    payload: DischargeContractsCreate,
    session: AsyncSession = Depends(get_session),
) -> DischargeContractsCreated:
    """Give every named outstanding order an owner and a deadline.

    Atomic: the whole batch commits or none of it does. Validation runs over
    the entire request before anything is written, so the response lists every
    problem rather than only the first.

    * **404** encounter not found
    * **409** an order already has a contract -- including the case where a
      concurrent request won the race and ``UNIQUE (order_id)`` refused ours
    * **422** any validation failure, with the full list of violations
    """
    try:
        return await create_discharge_contracts(
            session, encounter_id, payload.contracts
        )
    except EncounterNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Encounter {encounter_id} not found",
        ) from exc
    except ContractConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.detail
        ) from exc
    except ContractValidationError as exc:
        # RFC 7807 with a machine-readable violation list, mirroring the shape
        # the global validation handler already produces.
        raise HTTPException(
            status_code=422,  # constant name differs across starlette versions
            detail={
                "title": "Discharge contracts were not created",
                "violations": [
                    {
                        "order_id": str(v.order_id) if v.order_id else None,
                        "code": v.code,
                        "detail": v.detail,
                    }
                    for v in exc.violations
                ],
            },
        ) from exc


@router.post(
    "/{encounter_id}/discharge",
    response_model=DischargeResult,
    status_code=status.HTTP_200_OK,
    summary="Discharge the encounter — re-checks readiness server-side",
)
async def discharge(
    encounter_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> DischargeResult:
    """Complete the discharge, opening a tracking case per pending order.

    Takes **no body**. There is deliberately nothing a client can send that
    influences the decision: readiness is re-derived from the database inside
    the transaction, under a row lock on the encounter. A readiness response
    the caller fetched a minute ago has no authority here.

    * **200** discharged; returns the cases opened and their queued timers
    * **404** encounter not found
    * **409** already discharged, not a gated encounter type, or an
      outstanding investigation still has no contract -- the blocking list is
      included, as the build plan requires
    """
    try:
        return await discharge_encounter(session, encounter_id)
    except EncounterNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Encounter {encounter_id} not found",
        ) from exc
    except EncounterNotDischargeableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.detail
        ) from exc
    except DischargeBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "title": "Discharge blocked by outstanding investigations",
                "blocking_orders": exc.blocking,
            },
        ) from exc
