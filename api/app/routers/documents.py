"""``/api/documents`` — the review queue, page images and retry. Phase 6.

The upload endpoint itself lives in ``app/routers/reports.py`` because 6.1
names it ``POST /api/reports/upload`` and a URL a hospital's integration team
has been given is not ours to tidy.

**What this router is really for** is 6.5's last clause:

    any failure routes the document to the Phase 3 manual entry form with the
    page images shown side-by-side

The page-image endpoint is the load-bearing one. Without it the fallback is a
form with nothing to read from, which is not a fallback — it is a dead end
with a text box.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.security import client_ip, require_role
from app.services import audit as audit_service
from app.services.auth import AuthenticatedUser
from app.services.documents import repository
from app.services.documents.storage import StorageError, get_store

router = APIRouter(prefix="/documents", tags=["documents"])

# Who may work the review queue. A lab tech uploads and fixes their own
# uploads; a doctor and a unit head need to see a document that failed
# because their patient's result is inside it. An auditor reads everything.
REVIEW_ROLES = ("lab_tech", "doctor", "unit_head", "admin", "auditor")

# Retrying re-runs extraction and overwrites pages and spans. That is a
# narrower permission than looking at the queue.
RETRY_ROLES = ("lab_tech", "admin")


def _serialise(document: repository.DocumentRow) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "sha256": document.sha256,
        "original_filename": document.original_filename,
        "mime_type": document.mime_type,
        "size_bytes": document.size_bytes,
        "page_count": document.page_count,
        "source_channel": document.source_channel,
        "status": document.status,
        "error_text": document.error_text,
        "received_at": document.received_at.isoformat(),
        "attempts": document.attempts,
        "order_id": str(document.order_id) if document.order_id else None,
        "case_id": str(document.case_id) if document.case_id else None,
    }


@router.get("/review-queue", summary="Documents a human still has to deal with")
async def review_queue(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: AuthenticatedUser = Depends(require_role(*REVIEW_ROLES)),
) -> dict[str, Any]:
    """Oldest first — a backlog served newest-first grows a tail nobody
    reaches."""
    rows = await repository.review_queue(session, limit=limit, offset=offset)
    return {"documents": [_serialise(row) for row in rows], "count": len(rows)}


@router.get("/{document_id}", summary="One document, with its pages")
async def get_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: AuthenticatedUser = Depends(require_role(*REVIEW_ROLES)),
) -> dict[str, Any]:
    document = await repository.get(session, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    pages = await repository.pages_for(session, document_id)
    payload = _serialise(document)
    payload["pages"] = [
        {
            "page_no": page.page_no,
            "width_pt": float(page.width_pt) if page.width_pt is not None else None,
            "height_pt": float(page.height_pt) if page.height_pt is not None else None,
            "is_scanned": page.is_scanned,
            "text_layer": page.text_layer,
            "ocr_confidence": (
                float(page.ocr_confidence) if page.ocr_confidence is not None else None
            ),
            # A URL, not a path. The client must never learn where on the
            # server's disk anything lives.
            "image_url": (
                f"/api/documents/{document_id}/pages/{page.page_no}/image"
                if page.image_path
                else None
            ),
        }
        for page in pages
    ]
    return payload


@router.get("/{document_id}/spans", summary="Spans for the highlight overlay")
async def get_spans(
    document_id: uuid.UUID,
    page_no: int | None = Query(None, ge=1),
    session: AsyncSession = Depends(get_session),
    user: AuthenticatedUser = Depends(require_role(*REVIEW_ROLES)),
) -> dict[str, Any]:
    document = await repository.get(session, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    rows = await repository.spans_for(session, document_id, page_no)
    return {
        "spans": [
            {
                "page_no": row.page_no,
                "char_start": row.char_start,
                "char_end": row.char_end,
                "bbox": row.bbox,
                "text": row.text,
            }
            for row in rows
        ]
    }


@router.get(
    "/{document_id}/pages/{page_no}/image",
    summary="The rendered page image, for the overlay and the fallback form",
    response_class=Response,
)
async def page_image(
    document_id: uuid.UUID,
    page_no: int,
    session: AsyncSession = Depends(get_session),
    user: AuthenticatedUser = Depends(require_role(*REVIEW_ROLES)),
) -> Response:
    """Serve the PNG. **This is what makes the 6.5 fallback usable.**"""
    document = await repository.get(session, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    pages = await repository.pages_for(session, document_id)
    match = next((p for p in pages if p.page_no == page_no), None)
    if match is None or not match.image_path:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No rendered image for that page"
        )

    try:
        # `absolute()` inside the store refuses any path that escapes the
        # document root, so a tampered `image_path` column cannot turn this
        # into an arbitrary file read.
        data = get_store().read(match.image_path)
    except StorageError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "The page image is missing from disk"
        ) from exc

    return Response(
        content=data,
        media_type="image/png",
        headers={
            # Immutable: the document is content-addressed, so a given
            # document's page N never changes without becoming a different
            # document.
            "Cache-Control": "private, max-age=86400, immutable",
            # No patient identifier in a filename that ends up in a browser's
            # download history.
            "Content-Disposition": f'inline; filename="page-{page_no}.png"',
        },
    )


@router.post(
    "/{document_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run extraction. 6.5's admin retry button.",
)
async def retry(
    document_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: AuthenticatedUser = Depends(require_role(*RETRY_ROLES)),
) -> dict[str, Any]:
    """Put the document back on the ``ingest`` queue.

    Retrying a document that already extracted cleanly is allowed — a
    threshold may have been lowered, or an OCR model updated, and re-running
    is the only way to benefit from that. It is audited either way.
    """
    document = await repository.get(session, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    if document.status == repository.STATUS_EXTRACTING:
        # Already in flight. A second message would have two workers writing
        # pages for one document.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This document is being processed right now. Wait for it to "
            "finish, or retry it after it times out.",
        )

    if not get_store().verify(document.storage_path, document.sha256):
        # Retrying a file that no longer matches its own hash would parse
        # something other than what was uploaded.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The stored file is missing or no longer matches its content "
            "hash. Re-upload the report instead of retrying it.",
        )

    await repository.set_status(
        session, document_id, repository.STATUS_RECEIVED, error_text=None
    )
    await repository.enqueue_ingest(session, document_id)
    await audit_service.append(
        session,
        action=audit_service.ACTION_DOCUMENT_RETRIED,
        entity_type="document",
        entity_id=document_id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        before={"status": document.status, "error_text": document.error_text},
        after={"status": repository.STATUS_RECEIVED, "attempts": document.attempts},
    )
    await session.commit()

    return {"document_id": str(document_id), "status": repository.STATUS_RECEIVED}
