"""``/api/audit`` — the auditor's view and chain verification. Phase 5.5.

    ``GET /api/audit/verify?from=&to=`` → recomputes the chain, returns the
    first break if any
    Audit viewer UI for the ``auditor`` role, exportable to CSV

Read-only by construction: there is no write endpoint here, and the auditor
role is refused any non-GET request by :func:`app.security.require_role`
regardless of what a future endpoint declares.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AUDIT_ENTITY_TYPES
from app.db.session import get_session
from app.schemas.audit import (
    AuditAnchorOut,
    AuditPage,
    AuditRowOut,
    ChainVerificationOut,
)
from app.security import require_role
from app.services import audit as audit_service
from app.services.auth import AuthenticatedUser

router = APIRouter(prefix="/audit", tags=["audit"])

# Who may read the audit trail. A doctor may not: the log carries every
# patient's case activity across every department, which is a wider view than
# any clinical role has a reason for.
AUDIT_READER_ROLES = ("auditor", "admin")

MAX_EXPORT_ROWS = 50_000


@router.get(
    "/verify",
    response_model=ChainVerificationOut,
    summary="Recompute the hash chain and report the first break",
)
async def verify(
    from_: dt.datetime | None = Query(default=None, alias="from"),
    to: dt.datetime | None = Query(default=None),
    user: AuthenticatedUser = Depends(require_role(*AUDIT_READER_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ChainVerificationOut:
    """Returns **200 with ``intact: false``** when the chain is broken, not an
    error status.

    A broken chain is an answer, not a failure to answer — a 500 would make a
    monitoring system report "the verifier is down" when what it actually
    found is tampering.

    Verifying a *window* that does not start at seq 1 cannot check its own
    first ``prev_hash`` against anything earlier; it takes that row's recorded
    ``prev_hash`` as the starting point. ``window_anchored`` says which kind of
    answer this is.
    """
    result = await audit_service.verify_chain(session, since=from_, until=to)
    return ChainVerificationOut(
        intact=result.intact,
        rows_checked=result.rows_checked,
        first_break_seq=result.first_break_seq,
        first_break_reason=result.first_break_reason,
        head_seq=result.head_seq,
        head_hash=result.head_hash,
        window_anchored=from_ is None and to is None,
        verified_at=dt.datetime.now(dt.UTC),
    )


def _where(
    action: str | None,
    entity_type: str | None,
    entity_id: str | None,
    actor_user_id: uuid.UUID | None,
    from_: dt.datetime | None,
    to: dt.datetime | None,
    break_glass_only: bool,
) -> tuple[str, dict[str, Any]]:
    clauses = ["TRUE"]
    params: dict[str, Any] = {}
    if action:
        clauses.append("a.action = :action")
        params["action"] = action
    if entity_type:
        if entity_type not in AUDIT_ENTITY_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown entity_type. Expected one of: "
                f"{', '.join(AUDIT_ENTITY_TYPES)}.",
            )
        clauses.append("a.entity_type = :etype")
        params["etype"] = entity_type
    if entity_id:
        clauses.append("a.entity_id = :eid")
        params["eid"] = entity_id
    if actor_user_id is not None:
        clauses.append("a.actor_user_id = :actor")
        params["actor"] = str(actor_user_id)
    if from_ is not None:
        clauses.append("a.occurred_at >= :from_ts")
        params["from_ts"] = from_
    if to is not None:
        clauses.append("a.occurred_at <= :to_ts")
        params["to_ts"] = to
    if break_glass_only:
        clauses.append("a.break_glass_reason IS NOT NULL")
    return " AND ".join(clauses), params


_SELECT = """
    SELECT a.seq, a.occurred_at, a.actor_user_id, u.full_name AS actor_name,
           u.employee_code AS actor_employee_code, HOST(a.actor_ip) AS actor_ip,
           a.action, a.entity_type, a.entity_id, a.before, a.after,
           a.prev_hash, a.row_hash, a.break_glass_reason
      FROM audit_log a
      LEFT JOIN users u ON u.id = a.actor_user_id
"""


@router.get("", response_model=AuditPage, summary="Browse the audit trail")
async def list_audit(
    action: str | None = Query(default=None, max_length=80),
    entity_type: str | None = Query(default=None, max_length=40),
    entity_id: str | None = Query(default=None, max_length=64),
    actor_user_id: uuid.UUID | None = None,
    from_: dt.datetime | None = Query(default=None, alias="from"),
    to: dt.datetime | None = Query(default=None),
    break_glass_only: bool = False,
    after_seq: int | None = Query(
        default=None, ge=0, description="Cursor: the last seq you have seen."
    ),
    limit: int = Query(default=100, ge=1, le=500),
    user: AuthenticatedUser = Depends(require_role(*AUDIT_READER_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> AuditPage:
    """Paginated on ``seq`` — gapless and monotonic, so it is its own cursor."""
    where, params = _where(
        action, entity_type, entity_id, actor_user_id, from_, to, break_glass_only
    )
    if after_seq is not None:
        where += " AND a.seq > :after_seq"
        params["after_seq"] = after_seq
    params["lim"] = limit + 1

    rows = (
        await session.execute(
            text(f"{_SELECT} WHERE {where} ORDER BY a.seq LIMIT :lim"), params
        )
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    return AuditPage(
        rows=[
            AuditRowOut(
                seq=int(r.seq),
                occurred_at=r.occurred_at,
                actor_user_id=r.actor_user_id,
                actor_name=r.actor_name,
                actor_employee_code=r.actor_employee_code,
                actor_ip=r.actor_ip,
                action=r.action,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                before=r.before,
                after=r.after,
                prev_hash=r.prev_hash,
                row_hash=r.row_hash,
                break_glass_reason=r.break_glass_reason,
            )
            for r in rows
        ],
        next_after_seq=int(rows[-1].seq) if has_more and rows else None,
    )


def _csv_cell(value: Any) -> str:
    """Neutralise spreadsheet formula injection.

    A cell beginning ``=``, ``+``, ``-`` or ``@`` is executed as a formula by
    Excel and LibreOffice. An audit export is the one file most likely to be
    opened by someone senior on a hospital laptop, and its contents include
    free text a user typed — a closure note, a break-glass reason. Prefixing
    an apostrophe keeps the value readable and inert.
    """
    if value is None:
        return ""
    rendered = (
        audit_service.canonical_json(value)
        if isinstance(value, dict | list)
        else str(value)
    )
    if rendered[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + rendered
    return rendered


@router.get("/export.csv", summary="Export the audit trail as CSV")
async def export_csv(
    action: str | None = Query(default=None, max_length=80),
    entity_type: str | None = Query(default=None, max_length=40),
    entity_id: str | None = Query(default=None, max_length=64),
    actor_user_id: uuid.UUID | None = None,
    from_: dt.datetime | None = Query(default=None, alias="from"),
    to: dt.datetime | None = Query(default=None),
    break_glass_only: bool = False,
    user: AuthenticatedUser = Depends(require_role(*AUDIT_READER_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Includes ``prev_hash`` and ``row_hash``.

    Without them the export is a list of claims; with them it is verifiable
    against the chain by anyone who exported it, which is the difference
    between a report and evidence.
    """
    where, params = _where(
        action, entity_type, entity_id, actor_user_id, from_, to, break_glass_only
    )
    params["lim"] = MAX_EXPORT_ROWS

    rows = (
        await session.execute(
            text(f"{_SELECT} WHERE {where} ORDER BY a.seq LIMIT :lim"), params
        )
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "seq",
            "occurred_at_utc",
            "actor_employee_code",
            "actor_name",
            "actor_ip",
            "action",
            "entity_type",
            "entity_id",
            "before",
            "after",
            "break_glass_reason",
            "prev_hash",
            "row_hash",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.seq,
                r.occurred_at.astimezone(dt.UTC).isoformat(),
                _csv_cell(r.actor_employee_code),
                _csv_cell(r.actor_name),
                _csv_cell(r.actor_ip),
                _csv_cell(r.action),
                _csv_cell(r.entity_type),
                _csv_cell(r.entity_id),
                _csv_cell(r.before),
                _csv_cell(r.after),
                _csv_cell(r.break_glass_reason),
                r.prev_hash,
                r.row_hash,
            ]
        )

    buffer.seek(0)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="audit-{stamp}.csv"',
            # The export may be truncated; say so in a header rather than
            # silently handing over a partial record.
            "X-Rows-Exported": str(len(rows)),
            "X-Export-Truncated": "true" if len(rows) >= MAX_EXPORT_ROWS else "false",
        },
    )


@router.get(
    "/anchors",
    response_model=list[AuditAnchorOut],
    summary="Nightly notarisations — the published head hashes",
)
async def list_anchors(
    limit: int = Query(default=60, ge=1, le=365),
    user: AuthenticatedUser = Depends(require_role(*AUDIT_READER_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> list[AuditAnchorOut]:
    rows = (
        await session.execute(
            text(
                "SELECT anchored_at, head_seq, head_hash, rows_verified, "
                "       chain_intact, first_break_seq "
                "  FROM audit_anchors ORDER BY anchored_at DESC LIMIT :lim"
            ),
            {"lim": limit},
        )
    ).all()
    return [
        AuditAnchorOut(
            anchored_at=r.anchored_at,
            head_seq=int(r.head_seq),
            head_hash=r.head_hash,
            rows_verified=int(r.rows_verified),
            chain_intact=r.chain_intact,
            first_break_seq=r.first_break_seq,
        )
        for r in rows
    ]
