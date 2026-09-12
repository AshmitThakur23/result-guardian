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
from app.rules.numeric import RuleOutput
from app.rules.orchestrator import OrderNotFoundError as PreviewOrderNotFoundError
from app.rules.orchestrator import preview_classification
from app.schemas.results import (
    ResultContent,
    ResultCreate,
    ResultPreview,
    ResultRecorded,
    RulePreviewRow,
)
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
            content=ResultContent(
                analytes=payload.analytes,
                organisms=payload.organisms,
                narratives=payload.narratives,
            ),
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


@router.post(
    "/{order_id}/results/preview",
    response_model=ResultPreview,
    status_code=status.HTTP_200_OK,
    summary="What the rule engine would say about this content — writes nothing",
)
async def preview_result(
    order_id: uuid.UUID,
    payload: ResultContent,
    session: AsyncSession = Depends(get_session),
) -> ResultPreview:
    """Phase 3.7: *"preview panel showing predicted severity before save"*.

    A lab tech typing a culture should be able to see that the organism is
    resistant to what the patient went home on **before** committing the
    report — both because it catches typos while they are still cheap, and
    because a severity that appears out of nowhere after saving teaches nobody
    anything about why.

    **Nothing is written.** No result, no classification, no case transition,
    no timer, no notification. The reply is a prediction; the decision of
    record is made on save and stored with its engine version.

    * **200** graded
    * **404** no such order
    * **422** bad field values
    """
    try:
        preview = await preview_classification(
            session,
            order_id,
            analytes=[a.model_dump() for a in payload.analytes],
            organisms=[o.model_dump() for o in payload.organisms],
            narratives=[n.model_dump() for n in payload.narratives],
        )
    except PreviewOrderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found",
        ) from exc

    return ResultPreview(
        severity=preview.severity,
        engine_version=preview.engine_version,
        would_auto_close=preview.would_auto_close,
        discharge_antibiotics=preview.discharge_antibiotics,
        rules=[
            RulePreviewRow(
                rule_id=output.rule_id,
                severity=output.severity,
                reason_code=output.reason_code,
                subject=_subject(output),
                offending_drug=_optional_str(output.detail.get("offending_drug")),
                alternatives_available=_string_list(
                    output.detail.get("alternatives_available")
                ),
            )
            for output in preview.rule_outputs
        ],
    )


def _subject(output: RuleOutput) -> str | None:
    """What this line of the preview is about, so the tech can find the row."""
    for key in ("test_code", "organism", "section"):
        value = output.inputs_used.get(key)
        if value:
            return str(value)
    return None


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _string_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []
