"""``/api/worklist`` and case detail/closure endpoints. Phase 5.2 / 5.3.

Every endpoint here carries ``Depends(require_role(...))``, per 5.1's *"RBAC
dependency on **every** endpoint"*, and every query is row-scoped inside the
service. Neither is optional and neither can be added later by remembering.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cases import CASE_SEVERITIES, CASE_STATES
from app.db.session import get_session
from app.schemas.dashboard import (
    AddNoteRequest,
    BulkCloseRequest,
    BulkCloseResult,
    CaseDetailOut,
    CloseCaseRequest,
    CloseCaseResult,
    ReopenRequest,
    ReopenResult,
    WorklistOut,
    WorklistRowOut,
)
from app.security import client_ip, require_role
from app.services import closure as closure_service
from app.services import worklist as worklist_service
from app.services.auth import AuthenticatedUser

router = APIRouter(tags=["worklist"])

CLINICAL_ROLES = ("doctor", "unit_head", "admin", "auditor")


def _validate_enum(
    values: list[str] | None, allowed: tuple[str, ...], name: str
) -> None:
    if not values:
        return
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown {name}: {', '.join(unknown)}. "
            f"Expected one of: {', '.join(allowed)}.",
        )


@router.get(
    "/worklist",
    response_model=WorklistOut,
    summary="My open flags — CRITICAL first, then oldest",
)
async def get_worklist(
    severity: list[str] | None = Query(default=None),
    state: list[str] | None = Query(default=None),
    department_id: uuid.UUID | None = None,
    opened_from: dt.datetime | None = None,
    opened_to: dt.datetime | None = None,
    owner_id: uuid.UUID | None = None,
    mine_only: bool = False,
    include_closed: bool = False,
    overdue_only: bool = False,
    search: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=worklist_service.DEFAULT_PAGE_SIZE, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=512),
    with_total: bool = False,
    user: AuthenticatedUser = Depends(require_role(*CLINICAL_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> WorklistOut:
    """Cursor-paginated. Pass ``next_cursor`` back as ``cursor`` for page 2.

    ``with_total`` is opt-in because the count is a second full scan of the
    filtered set: the dashboard header wants it, the polling loop every 30
    seconds does not.
    """
    _validate_enum(severity, CASE_SEVERITIES, "severity")
    _validate_enum(state, CASE_STATES, "state")

    filters = worklist_service.WorklistFilters(
        severity=severity,
        state=state,
        department_id=department_id,
        opened_from=opened_from,
        opened_to=opened_to,
        owner_id=owner_id,
        mine_only=mine_only,
        include_closed=include_closed,
        overdue_only=overdue_only,
        search=search,
    )
    try:
        page = await worklist_service.fetch_worklist(
            session, user, filters=filters, limit=limit, cursor=cursor
        )
    except worklist_service.InvalidCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    total = (
        await worklist_service.count_open(session, user, filters=filters)
        if with_total
        else None
    )
    return WorklistOut(
        rows=[WorklistRowOut(**vars(r)) for r in page.rows],
        next_cursor=page.next_cursor,
        total_open=total,
    )


@router.get(
    "/cases/{case_id}/detail",
    response_model=CaseDetailOut,
    summary="The case page: result, rule output in plain language, timeline",
)
async def get_case_detail(
    case_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_role(*CLINICAL_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> CaseDetailOut:
    """**404** for a case outside the caller's scope.

    Not 403: "this exists but is not yours" confirms a case exists for a
    patient the caller named, which is the disclosure the scoping prevents.
    """
    try:
        detail = await worklist_service.fetch_case_detail(session, case_id, user)
    except worklist_service.CaseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        ) from exc

    return CaseDetailOut(
        case_id=detail.case_id,
        state=detail.state,
        severity=detail.severity,
        opened_at=detail.opened_at,
        flagged_at=detail.flagged_at,
        acknowledged_at=detail.acknowledged_at,
        closed_at=detail.closed_at,
        closure_reason=detail.closure_reason,
        closure_note=detail.closure_note,
        reopened_count=detail.reopened_count,
        patient=detail.patient,
        encounter=detail.encounter,
        order=detail.order,
        owner=detail.owner,
        result=detail.result,
        analytes=[vars(a) for a in detail.analytes],
        organisms=[vars(o) for o in detail.organisms],
        narratives=detail.narratives,
        explanations=[e.as_dict() for e in detail.explanations],
        timeline=[vars(t) for t in detail.timeline],
        escalation_level=detail.escalation_level,
        next_escalation_at=detail.next_escalation_at,
        can_acknowledge=detail.can_acknowledge,
    )


async def _guard_scope(
    session: AsyncSession, case_id: uuid.UUID, user: AuthenticatedUser
) -> None:
    """Refuse a write to a case the caller cannot see.

    The read path scopes in SQL; the write path has to check separately, or a
    doctor could close a case in a ward they have never seen by knowing its id.
    """
    try:
        await worklist_service.fetch_case_detail(session, case_id, user)
    except worklist_service.CaseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        ) from exc


@router.post(
    "/cases/{case_id}/close",
    response_model=CloseCaseResult,
    summary="Acknowledge and close — closure reason mandatory",
)
async def close_case(
    request: Request,
    case_id: uuid.UUID,
    payload: CloseCaseRequest,
    user: AuthenticatedUser = Depends(require_role("doctor", "unit_head")),
    session: AsyncSession = Depends(get_session),
) -> CloseCaseResult:
    """Phase 5.3's closure, superseding Phase 4's bare acknowledgement.

    * **200** closed (or already closed — idempotent)
    * **404** no such case, or outside your scope
    * **422** unknown reason, or a required note that is missing or too short
    """
    await _guard_scope(session, case_id, user)
    try:
        result = await closure_service.acknowledge_and_close(
            session,
            case_id,
            closure_reason=payload.closure_reason,
            closure_note=payload.closure_note,
            actor_user_id=user.id,
            actor_ip=client_ip(request),
            duplicate_of_case_id=payload.duplicate_of_case_id,
        )
    except closure_service.CaseNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        ) from exc
    except closure_service.ClosureValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await session.commit()
    return CloseCaseResult(**vars(result))


@router.post(
    "/cases/bulk-close",
    response_model=BulkCloseResult,
    summary="Close several cases — refused outright if any is CRITICAL",
)
async def bulk_close(
    request: Request,
    payload: BulkCloseRequest,
    user: AuthenticatedUser = Depends(require_role("doctor", "unit_head")),
    session: AsyncSession = Depends(get_session),
) -> BulkCloseResult:
    """*"Bulk acknowledge is not available for CRITICAL cases — deliberate
    friction."*

    **409**, not 422: the request is well-formed and the refusal is a policy,
    so the body names the offending cases and the UI sends the clinician to
    open each one.
    """
    for case_id in payload.case_ids:
        await _guard_scope(session, case_id, user)

    try:
        results = await closure_service.bulk_acknowledge(
            session,
            payload.case_ids,
            closure_reason=payload.closure_reason,
            closure_note=payload.closure_note,
            actor_user_id=user.id,
            actor_ip=client_ip(request),
        )
    except closure_service.BulkCriticalRefusedError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "title": "CRITICAL cases must be closed individually",
                "detail": str(exc),
                "critical_case_ids": [str(c) for c in exc.case_ids],
            },
        ) from exc
    except closure_service.CaseNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        ) from exc
    except closure_service.ClosureValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await session.commit()
    return BulkCloseResult(closed=[CloseCaseResult(**vars(r)) for r in results])


@router.post(
    "/cases/{case_id}/reopen",
    response_model=ReopenResult,
    summary="Reopen a closed case — reason mandatory, history preserved",
)
async def reopen(
    request: Request,
    case_id: uuid.UUID,
    payload: ReopenRequest,
    user: AuthenticatedUser = Depends(require_role("doctor", "unit_head", "admin")),
    session: AsyncSession = Depends(get_session),
) -> ReopenResult:
    await _guard_scope(session, case_id, user)
    try:
        result = await closure_service.reopen_case(
            session,
            case_id,
            reason=payload.reason,
            actor_user_id=user.id,
            actor_ip=client_ip(request),
            amended_result_id=payload.amended_result_id,
        )
    except closure_service.CaseNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        ) from exc
    except closure_service.ClosureValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await session.commit()
    return ReopenResult(**vars(result))


@router.post(
    "/cases/{case_id}/notes",
    status_code=status.HTTP_201_CREATED,
    summary="Add a note to the case timeline",
)
async def add_note(
    request: Request,
    case_id: uuid.UUID,
    payload: AddNoteRequest,
    user: AuthenticatedUser = Depends(require_role("doctor", "unit_head", "admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    await _guard_scope(session, case_id, user)
    try:
        await closure_service.add_note(
            session,
            case_id,
            note=payload.note,
            actor_user_id=user.id,
            actor_ip=client_ip(request),
        )
    except closure_service.CaseNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        ) from exc
    except closure_service.ClosureValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await session.commit()
    return {"case_id": str(case_id)}
