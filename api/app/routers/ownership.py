"""Roster, absences, metrics and the delivery webhook. Phase 4.1 / 4.3 / 4.5.

The roster endpoints are what [ADR 0004] decided a unit head would use:

    **The unit head of each department maintains their department's roster,
    weekly.** … A roster nobody updates is worse than no roster.

The ADR also requires the staleness guards in the same sprint, and two of them
are endpoints here — the stale-roster badge's data and the fallthrough metric.
Phase 5.4 renders both; Phase 4 measures them, the same split Phase 2.3 used
when it shipped ``/api/lab-flags/metrics`` without a dashboard.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.db.types import uuid7
from app.schemas.ownership import (
    AbsenceCreate,
    AbsenceRow,
    DutyRosterCreate,
    DutyRosterRow,
    FallthroughMetric,
    FlagRateMetric,
    NotificationRow,
    StaleRosterDepartment,
)
from app.services.notification_policy import flag_rate_per_100_discharges
from app.services.ownership import departments_with_stale_roster, fallthrough_metrics

router = APIRouter(tags=["ownership"])


# ── 4.1 · the roster the unit head maintains ──────────────────────────


@router.post(
    "/duty-roster",
    response_model=DutyRosterRow,
    status_code=status.HTTP_201_CREATED,
    summary="Add a shift to a department's roster (unit head, weekly)",
)
async def create_roster_entry(
    payload: DutyRosterCreate,
    session: AsyncSession = Depends(get_session),
) -> DutyRosterRow:
    for table, value, label in (
        ("users", payload.user_id, "user"),
        ("departments", payload.department_id, "department"),
    ):
        found = (
            await session.execute(
                text(f"SELECT id FROM {table} WHERE id = :i AND deleted_at IS NULL"),
                {"i": str(value)},
            )
        ).first()
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No such {label}: {value}",
            )

    roster_id = uuid7()
    await session.execute(
        text(
            "INSERT INTO duty_roster "
            "(id, user_id, department_id, shift_start, shift_end, role_on_duty, "
            " created_by, updated_by) "
            "VALUES (:i, :u, :d, :s, :e, :r, :a, :a)"
        ),
        {
            "i": str(roster_id),
            "u": str(payload.user_id),
            "d": str(payload.department_id),
            "s": payload.shift_start,
            "e": payload.shift_end,
            "r": payload.role_on_duty,
            "a": str(payload.created_by) if payload.created_by else None,
        },
    )
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT r.id, r.user_id, u.full_name AS user_name, r.department_id, "
                "       r.shift_start, r.shift_end, r.role_on_duty "
                "  FROM duty_roster r JOIN users u ON u.id = r.user_id "
                " WHERE r.id = :i"
            ),
            {"i": str(roster_id)},
        )
    ).one()
    return DutyRosterRow.model_validate(row)


@router.get(
    "/departments/{department_id}/duty-roster",
    response_model=list[DutyRosterRow],
    summary="A department's roster",
)
async def list_roster(
    department_id: uuid.UUID,
    at: dt.datetime | None = Query(
        default=None, description="Only shifts covering this instant."
    ),
    session: AsyncSession = Depends(get_session),
) -> list[DutyRosterRow]:
    sql = (
        "SELECT r.id, r.user_id, u.full_name AS user_name, r.department_id, "
        "       r.shift_start, r.shift_end, r.role_on_duty "
        "  FROM duty_roster r JOIN users u ON u.id = r.user_id "
        " WHERE r.department_id = :d AND r.deleted_at IS NULL"
    )
    params: dict[str, object] = {"d": str(department_id)}
    if at is not None:
        sql += " AND r.shift_start <= :at AND r.shift_end > :at"
        params["at"] = at
    sql += " ORDER BY r.shift_start, r.role_on_duty"

    rows = (await session.execute(text(sql), params)).all()
    return [DutyRosterRow.model_validate(r) for r in rows]


@router.post(
    "/user-absences",
    response_model=AbsenceRow,
    status_code=status.HTTP_201_CREATED,
    summary="Record an absence and who covers it",
)
async def create_absence(
    payload: AbsenceCreate,
    session: AsyncSession = Depends(get_session),
) -> AbsenceRow:
    for value, label in (
        (payload.user_id, "user"),
        (payload.delegate_user_id, "delegate"),
    ):
        if value is None:
            continue
        found = (
            await session.execute(
                text("SELECT id FROM users WHERE id = :i AND deleted_at IS NULL"),
                {"i": str(value)},
            )
        ).first()
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No such {label}: {value}",
            )

    absence_id = uuid7()
    await session.execute(
        text(
            "INSERT INTO user_absences "
            "(id, user_id, absence_type, starts_at, ends_at, delegate_user_id, "
            " created_by, updated_by) "
            "VALUES (:i, :u, :t, :s, :e, :dl, :a, :a)"
        ),
        {
            "i": str(absence_id),
            "u": str(payload.user_id),
            "t": payload.absence_type,
            "s": payload.starts_at,
            "e": payload.ends_at,
            "dl": (str(payload.delegate_user_id) if payload.delegate_user_id else None),
            "a": str(payload.created_by) if payload.created_by else None,
        },
    )
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT id, user_id, absence_type, starts_at, ends_at, "
                "       delegate_user_id FROM user_absences WHERE id = :i"
            ),
            {"i": str(absence_id)},
        )
    ).one()
    return AbsenceRow.model_validate(row)


# ── ADR 0004's staleness guards ───────────────────────────────────────


@router.get(
    "/duty-roster/stale",
    response_model=list[StaleRosterDepartment],
    summary="Departments with no shift covering now — the roster-stale badge",
)
async def stale_rosters(
    session: AsyncSession = Depends(get_session),
) -> list[StaleRosterDepartment]:
    """ADR 0004 guard 3. Phase 5.4 renders the badge; this is its data."""
    rows = await departments_with_stale_roster(session)
    return [StaleRosterDepartment.model_validate(r) for r in rows]


@router.get(
    "/metrics/owner-fallthrough",
    response_model=list[FallthroughMetric],
    summary="Owner resolutions that fell through to unit head or admin",
)
async def owner_fallthrough(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> list[FallthroughMetric]:
    """ADR 0004 guard 4: *"A rising number is the measurable signature of a
    roster going stale."*"""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    rows = await fallthrough_metrics(session, since=since)
    return [FallthroughMetric.model_validate(r) for r in rows]


@router.get(
    "/metrics/flag-rate",
    response_model=FlagRateMetric,
    summary="Flag rate per 100 discharges — put this number on the wall",
)
async def flag_rate(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> FlagRateMetric:
    """Phase 4.5's metric, with the plan's own threshold attached:

    If this exceeds **~15%** you must retune thresholds before the pilot
    expands.
    """
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    return FlagRateMetric.model_validate(
        await flag_rate_per_100_discharges(session, since=since)
    )


@router.get(
    "/notifications/failed",
    response_model=list[NotificationRow],
    summary="Notifications that exhausted their retries — the admin alert",
)
async def failed_notifications(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[NotificationRow]:
    """Phase 4.3: *"retry 3x with backoff -> mark ``failed`` -> alert admin
    dashboard."*

    This is what the dashboard alerts on. A failed row is a message that was
    genuinely lost — the case behind it is still tracked, still flagged and
    still escalating, which is the degradation the plan asks for, but somebody
    should know the channel is broken.
    """
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    rows = (
        await session.execute(
            text(
                "SELECT id, case_id, user_id, patient_id, channel, template_key, "
                "       locale, status, suppression_reason, escalation_level, "
                "       attempts, sent_at, error "
                "  FROM notifications "
                " WHERE status = 'failed' AND created_at >= :since "
                "   AND deleted_at IS NULL "
                " ORDER BY created_at DESC LIMIT :lim"
            ),
            {"since": since, "lim": limit},
        )
    ).all()
    return [NotificationRow.model_validate(r) for r in rows]
