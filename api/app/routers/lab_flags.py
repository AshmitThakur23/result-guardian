"""Lab-side accountability endpoints. Phase 2.3.

The lab's own view of what it owes, and the metric the admin dashboard draws.
Phase 5.4 builds the dashboard; the measurement is here so that phase is a
rendering job rather than a rendering *and* measurement job.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.lab import LAB_FLAG_TYPES
from app.db.session import get_session
from app.schemas.results import (
    LabFlagAgeBucket,
    LabFlagMetrics,
    LabFlagResolve,
    LabFlagRow,
)
from app.services.lab_flags import LabFlagNotFoundError, resolve_lab_flag

router = APIRouter(prefix="/lab-flags", tags=["lab"])

# Phase 2.3's metric is "open lab flags by age", and age only means anything
# in buckets a human reads. Seven days is the escalation ceiling, so it is the
# last boundary: anything beyond it should already be with the unit head.
AGE_BUCKETS_SQL = """
SELECT bucket, count(*) AS flag_count, min(raised_at) AS oldest_raised_at
  FROM (
    SELECT raised_at,
           CASE
             WHEN raised_at > now() - interval '24 hours' THEN 'under_24h'
             WHEN raised_at > now() - interval '3 days'   THEN '1_to_3_days'
             WHEN raised_at > now() - interval '7 days'   THEN '3_to_7_days'
             ELSE 'over_7_days'
           END AS bucket
      FROM lab_flags
     WHERE resolved_at IS NULL AND deleted_at IS NULL
  ) aged
 GROUP BY bucket
"""

BUCKET_ORDER = ("under_24h", "1_to_3_days", "3_to_7_days", "over_7_days")


@router.get(
    "",
    response_model=list[LabFlagRow],
    summary="Open lab flags — the lab's queue",
)
async def list_lab_flags(
    open_only: bool = Query(default=True),
    flag_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[LabFlagRow]:
    """Oldest first: the flag that has been ignored longest is the one that
    matters most, which is the opposite of the usual newest-first default."""
    if flag_type is not None and flag_type not in LAB_FLAG_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"flag_type must be one of {', '.join(LAB_FLAG_TYPES)}",
        )

    sql = (
        "SELECT id, case_id, flag_type, raised_at, resolved_at, resolved_by, "
        "       resolution_note "
        "  FROM lab_flags WHERE deleted_at IS NULL"
    )
    params: dict[str, object] = {"limit": limit}
    if open_only:
        sql += " AND resolved_at IS NULL"
    if flag_type is not None:
        sql += " AND flag_type = :t"
        params["t"] = flag_type
    sql += " ORDER BY raised_at LIMIT :limit"

    rows = (await session.execute(text(sql), params)).mappings().all()
    return [LabFlagRow.model_validate(dict(row)) for row in rows]


@router.get(
    "/metrics",
    response_model=LabFlagMetrics,
    summary="Open lab flags by age — the Phase 2.3 lab-side metric",
)
async def lab_flag_metrics(
    session: AsyncSession = Depends(get_session),
) -> LabFlagMetrics:
    """Counts only. No patient identifiers: this is an operational measure of
    how long the lab is leaving reports outstanding, and it is read by people
    who have no business seeing who the patients are."""
    aged = (await session.execute(text(AGE_BUCKETS_SQL))).mappings().all()
    by_bucket = {row["bucket"]: row for row in aged}

    buckets = [
        LabFlagAgeBucket(
            bucket=name,
            flag_count=int(by_bucket[name]["flag_count"]) if name in by_bucket else 0,
            oldest_raised_at=(
                by_bucket[name]["oldest_raised_at"] if name in by_bucket else None
            ),
        )
        for name in BUCKET_ORDER
    ]

    by_type_rows = (
        await session.execute(
            text(
                "SELECT flag_type, count(*) AS n FROM lab_flags "
                " WHERE resolved_at IS NULL AND deleted_at IS NULL "
                " GROUP BY flag_type"
            )
        )
    ).all()

    return LabFlagMetrics(
        open_total=sum(b.flag_count for b in buckets),
        by_age=buckets,
        by_type={row.flag_type: int(row.n) for row in by_type_rows},
    )


@router.post(
    "/{flag_id}/resolve",
    response_model=LabFlagRow,
    summary="Resolve a lab flag — the sample was found, or the report arrived",
)
async def resolve(
    flag_id: uuid.UUID,
    payload: LabFlagResolve,
    session: AsyncSession = Depends(get_session),
) -> LabFlagRow:
    """Closing a flag stops the 24-hour re-checks.

    * **404** no such flag, or it is already resolved
    """
    try:
        await resolve_lab_flag(
            session, flag_id, payload.resolved_by, payload.resolution_note
        )
    except LabFlagNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lab flag {flag_id} not found, or already resolved",
        ) from exc

    await session.commit()

    row = (
        (
            await session.execute(
                text(
                    "SELECT id, case_id, flag_type, raised_at, resolved_at, "
                    "       resolved_by, resolution_note FROM lab_flags WHERE id = :i"
                ),
                {"i": str(flag_id)},
            )
        )
        .mappings()
        .one()
    )
    return LabFlagRow.model_validate(dict(row))
