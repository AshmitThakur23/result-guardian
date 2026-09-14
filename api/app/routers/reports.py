"""``/api/reports`` — metrics, CSV and the NABH monthly PDF. Phase 5.6."""

from __future__ import annotations

import csv
import datetime as dt
import io
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session
from app.security import client_ip, require_role
from app.services import metrics as metrics_service
from app.services.auth import AuthenticatedUser
from app.services.documents import intake
from app.services.pdf import PdfBuilder

router = APIRouter(prefix="/reports", tags=["reports"])

REPORT_ROLES = ("unit_head", "admin", "auditor")

# Phase 6.1. Deliberately wider than REPORT_ROLES: the people who receive a
# lab report are lab technicians and ward staff, not the people who read the
# NABH metrics. An auditor is excluded — reading the record is their whole
# remit, and uploading would put them in it.
UPLOAD_ROLES = ("lab_tech", "doctor", "unit_head", "admin")

# A year, and no further. The window is the only thing standing between this
# endpoint and a sequential scan of every case the hospital has ever had.
MAX_WINDOW_DAYS = 366

# The NABH standards this report answers to. Named on the document so the
# reviewer does not have to be told which clauses it covers.
NABH_CLAUSES = (
    "AAC.12 — Patients are informed of their care plan and of results that "
    "require follow-up after discharge.",
    "AAC.6.g — Laboratory results are reported to the ordering clinician "
    "within a defined turnaround time, and critical results are communicated "
    "immediately.",
)


def _window(
    from_: dt.datetime | None,
    to: dt.datetime | None,
    days: int,
    department_id: uuid.UUID | None,
    user: AuthenticatedUser,
) -> metrics_service.MetricsWindow:
    """Build the window, and scope a unit head to their own department.

    A unit head asking for another department's numbers gets their own: the
    plan gives them *"all department cases"*, singular. Overriding the
    parameter rather than refusing keeps a bookmarked URL working instead of
    erroring at them.
    """
    end = to or dt.datetime.now(dt.UTC)
    start = from_ or (end - dt.timedelta(days=days))
    if end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' must be after 'from'.",
        )
    if (end - start).days > MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The reporting window may not exceed {MAX_WINDOW_DAYS} days.",
        )

    scoped = department_id
    if user.role == "unit_head":
        scoped = user.department_id
    return metrics_service.MetricsWindow(start=start, end=end, department_id=scoped)


def _serialise(report: metrics_service.MetricsReport) -> dict[str, Any]:
    return {
        "window_start": report.window_start,
        "window_end": report.window_end,
        "department_id": report.department_id,
        "turnaround": [vars(t) for t in report.turnaround],
        "age_buckets": [vars(b) for b in report.age_buckets],
        "escalations": [vars(e) for e in report.escalations],
        "closure_reasons": [vars(c) for c in report.closure_reasons],
        "flag_rate": vars(report.flag_rate),
        "patient_contacts": vars(report.patient_contacts),
        "overrides": vars(report.overrides),
        "lab_flags": vars(report.lab_flags),
    }


@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a report PDF or image. Phase 6.1.",
)
async def upload(
    request: Request,
    file: UploadFile = File(..., description="PDF, PNG, JPEG or TIFF, max 25 MB"),
    order_id: uuid.UUID | None = Form(
        default=None,
        description=(
            "Optional. Link the document to a known order. Leave it unset and "
            "Phase 7.5 matches it; the document is readable either way."
        ),
    ),
    user: AuthenticatedUser = Depends(require_role(*UPLOAD_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Accept a file, store it content-addressed, and queue extraction.

    **202, not 201.** Nothing has been extracted yet — the document is on the
    ``ingest`` queue and the client should poll or come back to the review
    queue. Returning 201 would imply a finished resource.

    The size limit is enforced twice: the read below is bounded, and
    ``intake.accept_upload`` checks the length it actually got. Starlette
    spools a large upload to a temporary file rather than refusing it, so
    without the bound here a 2 GB POST would be written to the API
    container's disk before anything rejected it.
    """
    settings = get_settings()

    # One byte past the limit is enough to know it is over.
    data = await file.read(settings.upload_max_bytes + 1)

    try:
        accepted = await intake.accept_upload(
            session,
            data=data,
            filename=file.filename,
            source_channel="upload",
            uploaded_by=user.id,
            actor_ip=client_ip(request),
            order_id=order_id,
        )
    except intake.IntakeRejectedError as exc:
        # 422, not 400: the request was well-formed, its content was not
        # acceptable. The reason is written to be shown to the person who
        # clicked upload.
        #
        # The literal rather than `status.HTTP_422_UNPROCESSABLE_ENTITY`:
        # Starlette deprecated that name in favour of
        # HTTP_422_UNPROCESSABLE_CONTENT, and the code is what goes on the
        # wire either way. Pinning to the number rather than to whichever
        # spelling this Starlette release prefers.
        raise HTTPException(422, exc.reason) from exc

    await session.commit()

    return {
        "document_id": str(accepted.document_id),
        "sha256": accepted.sha256,
        "duplicate": accepted.duplicate,
        "size_bytes": accepted.size_bytes,
        "mime_type": accepted.mime_type,
        "virus_scan": accepted.scan_verdict,
        "message": (
            "This report was already in the system; showing the existing " "document."
            if accepted.duplicate
            else "Report received. Extraction has been queued."
        ),
    }


@router.get("/summary", summary="Every Phase 5.6 metric for a window")
async def summary(
    from_: dt.datetime | None = Query(default=None, alias="from"),
    to: dt.datetime | None = Query(default=None),
    days: int = Query(default=metrics_service.DEFAULT_WINDOW_DAYS, ge=1, le=366),
    department_id: uuid.UUID | None = None,
    user: AuthenticatedUser = Depends(require_role(*REPORT_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    window = _window(from_, to, days, department_id, user)
    return _serialise(await metrics_service.build_report(session, window))


@router.get(
    "/per-doctor",
    summary="Per-doctor acknowledgement times — Phase 5.4's unit head view",
)
async def per_doctor(
    from_: dt.datetime | None = Query(default=None, alias="from"),
    to: dt.datetime | None = Query(default=None),
    days: int = Query(default=metrics_service.DEFAULT_WINDOW_DAYS, ge=1, le=366),
    department_id: uuid.UUID | None = None,
    user: AuthenticatedUser = Depends(require_role(*REPORT_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    window = _window(from_, to, days, department_id, user)
    return [
        vars(row)
        for row in await metrics_service.per_doctor_acknowledgement(session, window)
    ]


@router.get("/summary.csv", summary="The same metrics as CSV")
async def summary_csv(
    from_: dt.datetime | None = Query(default=None, alias="from"),
    to: dt.datetime | None = Query(default=None),
    days: int = Query(default=metrics_service.DEFAULT_WINDOW_DAYS, ge=1, le=366),
    department_id: uuid.UUID | None = None,
    user: AuthenticatedUser = Depends(require_role(*REPORT_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    window = _window(from_, to, days, department_id, user)
    report = await metrics_service.build_report(session, window)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["section", "metric", "value"])
    writer.writerow(["Window", "From (UTC)", window.start.isoformat()])
    writer.writerow(["Window", "To (UTC)", window.end.isoformat()])
    for section, metric, value in metrics_service.report_rows(report):
        # Same formula-injection guard as the audit export: these cells carry
        # department names and reason codes that originate from user input.
        cell = value if value[:1] not in {"=", "+", "-", "@"} else "'" + value
        writer.writerow([section, metric, cell])

    buffer.seek(0)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="metrics-{stamp}.csv"'},
    )


@router.get("/nabh-monthly.pdf", summary="Monthly NABH review pack (AAC.12, AAC.6.g)")
async def nabh_monthly(
    month: int = Query(default=0, ge=0, le=12, description="1-12, or 0 for last month"),
    year: int = Query(default=0, ge=0, le=2200, description="0 for the current year"),
    department_id: uuid.UUID | None = None,
    user: AuthenticatedUser = Depends(require_role(*REPORT_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """A calendar month, not a rolling 30 days.

    An accreditation review is per month, and a rolling window would double-
    count the boundary cases between two consecutive packs.
    """
    now = dt.datetime.now(dt.UTC)
    if month == 0:
        first_of_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = first_of_this
        start = (first_of_this - dt.timedelta(days=1)).replace(day=1)
    else:
        chosen_year = year or now.year
        start = dt.datetime(chosen_year, month, 1, tzinfo=dt.UTC)
        end = (
            dt.datetime(chosen_year + 1, 1, 1, tzinfo=dt.UTC)
            if month == 12
            else dt.datetime(chosen_year, month + 1, 1, tzinfo=dt.UTC)
        )

    window = metrics_service.MetricsWindow(
        start=start,
        end=end,
        department_id=user.department_id if user.role == "unit_head" else department_id,
    )
    report = await metrics_service.build_report(session, window)

    department_name = "All departments"
    if window.department_id is not None:
        from sqlalchemy import text as sql_text

        row = (
            await session.execute(
                sql_text("SELECT name FROM departments WHERE id = :d"),
                {"d": str(window.department_id)},
            )
        ).first()
        department_name = row.name if row else str(window.department_id)

    pdf = PdfBuilder(title=f"Result Guardian — NABH monthly review {start:%B %Y}")
    pdf.heading("Result Guardian — post-discharge result follow-up")
    pdf.line(f"NABH monthly review · {start:%B %Y}")
    pdf.line(f"Department: {department_name}")
    pdf.line(
        f"Window (UTC): {start:%Y-%m-%d} to {end:%Y-%m-%d} · "
        f"Generated {now:%Y-%m-%d %H:%M} UTC by {user.full_name or user.employee_code}"
    )
    pdf.spacer()
    for clause in NABH_CLAUSES:
        pdf.line(clause)
    pdf.spacer()

    current_section = ""
    for section, metric, value in metrics_service.report_rows(report):
        if section != current_section:
            pdf.subheading(section)
            current_section = section
        pdf.row(metric, value)

    pdf.spacer(12)
    pdf.subheading("Provenance")
    pdf.line(
        "Every figure is computed from the operational database at generation "
        "time. No figure is estimated, smoothed or carried forward."
    )
    pdf.line(
        "The rule engine's clinical thresholds have NOT been validated by a "
        "clinician review. See docs/clinical-validation.md."
    )

    stamp = f"{start:%Y-%m}"
    return Response(
        content=pdf.build(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="nabh-{stamp}.pdf"'},
    )
