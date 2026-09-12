"""Patient lookup. Read-only. Phase 1.5.

Search and read only. Patient registration belongs to the HIS and its manual
fallback is Phase 9 -- there is deliberately no POST here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.patients import PatientSearchRow, PatientWithEncounters
from app.services.patients import (
    PatientNotFoundError,
    get_patient_with_encounters,
    search_patients,
)

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get(
    "",
    response_model=list[PatientSearchRow],
    summary="Search patients by MRN, name or phone",
)
async def search(
    q: str = Query(
        min_length=1,
        max_length=200,
        description="MRN, name or phone. One field for all three.",
    ),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> list[PatientSearchRow]:
    """Read-only. Returns identity only -- never address or consent state.

    Ranked so an unambiguous identifier wins: exact MRN, then MRN prefix, then
    phone, then fuzzy name via the Phase 1.2 trigram index.

    `q` is required and non-empty by design. An endpoint that returns the
    whole patient index for an empty query is a patient-index dump waiting to
    happen; LIKE wildcards inside `q` are escaped for the same reason.
    """
    return await search_patients(session, query=q, limit=limit)


@router.get(
    "/{patient_id}",
    response_model=PatientWithEncounters,
    summary="One patient and their encounters",
)
async def detail(
    patient_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> PatientWithEncounters:
    """Newest admission first, so the encounter that needs work is on top.

    * **404** patient not found
    """
    try:
        return await get_patient_with_encounters(session, patient_id)
    except PatientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient {patient_id} not found",
        ) from exc
