"""Order-scoped endpoints. Phase 2.4.

Currently one: result intake. The build plan is emphatic that it is permanent
-- *"This endpoint stays forever. Phases 6-7 just add an automatic caller for
it. It is also the permanent fallback when extraction fails."* -- so it is
built as a production endpoint rather than a convenience for tests.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.results import ResultCreate, ResultRecorded
from app.services.results import (
    DuplicateResultError,
    OrderNotFoundError,
    record_result,
)

router = APIRouter(prefix="/orders", tags=["results"])


@router.post(
    "/{order_id}/results",
    response_model=ResultRecorded,
    status_code=status.HTTP_201_CREATED,
    summary="Record a result for an order — the permanent intake path",
)
async def create_result(
    order_id: uuid.UUID,
    payload: ResultCreate,
    session: AsyncSession = Depends(get_session),
) -> ResultRecorded:
    """Take in one report and move its case on.

    In one transaction: the result is stored, the ``result_due`` timer is
    superseded so it can never fire for a report that has arrived, the case
    moves to ``result_received``, an event is appended, and the result is
    handed to the Phase 3 rule engine. All of it commits together.

    **The payload is not interpreted.** Whether the value is critical is Phase
    3's decision; this endpoint's only clinical claim is *"a result arrived"*.

    * **201** recorded
    * **404** no such order
    * **409** a report with this `source` + `source_ref` is already on file
    * **422** bad field values

    An order with no tracking case is accepted and stored: a result can
    legitimately arrive for an investigation that never went through a
    discharge, and refusing it would lose it.
    """
    try:
        intake = await record_result(
            session,
            order_id,
            report_status=payload.report_status,
            source=payload.source,
            source_ref=payload.source_ref,
            reported_at=payload.reported_at,
            raw_payload=payload.raw_payload,
            actor_user_id=payload.recorded_by,
        )
    except OrderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found",
        ) from exc
    except DuplicateResultError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "title": exc.detail,
                "existing_result_id": str(exc.result_id),
            },
        ) from exc

    await session.commit()

    return ResultRecorded(
        result_id=intake.result_id,
        order_id=intake.order_id,
        case_id=intake.case_id,
        case_state=intake.case_state,
        superseded_timer_ids=intake.superseded_timer_ids,
        classification_enqueued=intake.classification_msg_id is not None,
        late=intake.late,
    )
