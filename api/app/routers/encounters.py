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
from app.schemas.discharge import DischargeReadiness
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
