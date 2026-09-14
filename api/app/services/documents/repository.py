"""Database access for documents, pages and spans. Phase 6.

Every write the ingestion pipeline makes goes through here, for one reason:
**status transitions are written before and after each stage** (6.5), and a
transition scattered across four call sites is a transition that will be
missing from one of them. A document stuck in ``extracting`` for ever is
invisible to the review queue — it looks busy rather than broken.

None of these commit. The caller owns the transaction, so a status change and
the audit row that explains it land together or not at all.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from typing import Any, Final

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.types import uuid7
from app.services.documents.native import Span

log = structlog.get_logger(__name__)

STATUS_RECEIVED = "received"
STATUS_CLASSIFIED = "classified"
STATUS_EXTRACTING = "extracting"
STATUS_EXTRACTED = "extracted"
STATUS_FAILED = "failed"
STATUS_NEEDS_REVIEW = "needs_review"

# Statuses a human still has to deal with. The review queue is exactly this
# set, and 6.5's fallback exists to make sure nothing lands here without a
# page image to read.
UNRESOLVED_STATUSES = (STATUS_FAILED, STATUS_NEEDS_REVIEW)


class _Unset:
    """Sentinel for "do not touch this column"."""


# ``error_text`` has three meanings and needs three values, not two.
# ``UNSET`` leaves the column alone, ``None`` clears it, a string sets it.
#
# Conflating the first two was a real defect: the retry endpoint passed
# ``error_text=None`` meaning *clear this*, and got *leave it alone* — so a
# document queued for another attempt still displayed the previous failure's
# message in the review queue. Caught by
# ``test_retry_requeues_a_failed_document``.
UNSET: Final = _Unset()


@dataclass(frozen=True)
class DocumentRow:
    id: uuid.UUID
    sha256: str
    original_filename: str | None
    mime_type: str
    size_bytes: int
    storage_path: str
    page_count: int | None
    source_channel: str
    status: str
    error_text: str | None
    received_at: dt.datetime
    attempts: int
    order_id: uuid.UUID | None
    case_id: uuid.UUID | None


_SELECT = """
    SELECT id, sha256, original_filename, mime_type, size_bytes, storage_path,
           page_count, source_channel, status, error_text, received_at,
           attempts, order_id, case_id
      FROM documents
"""


def _row(record: Any) -> DocumentRow:
    return DocumentRow(
        id=record.id,
        sha256=record.sha256,
        original_filename=record.original_filename,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        storage_path=record.storage_path,
        page_count=record.page_count,
        source_channel=record.source_channel,
        status=record.status,
        error_text=record.error_text,
        received_at=record.received_at,
        attempts=record.attempts,
        order_id=record.order_id,
        case_id=record.case_id,
    )


async def find_by_sha256(session: AsyncSession, sha256: str) -> DocumentRow | None:
    """The deduplication lookup. Soft-deleted rows count as present.

    A document someone deleted and then re-uploaded should surface the
    existing row rather than quietly creating a second one that shares a
    storage path with a deleted first.
    """
    record = (
        await session.execute(text(_SELECT + " WHERE sha256 = :sha"), {"sha": sha256})
    ).first()
    return _row(record) if record else None


async def get(session: AsyncSession, document_id: uuid.UUID) -> DocumentRow | None:
    record = (
        await session.execute(
            text(_SELECT + " WHERE id = CAST(:id AS uuid) AND deleted_at IS NULL"),
            {"id": str(document_id)},
        )
    ).first()
    return _row(record) if record else None


async def insert(
    session: AsyncSession,
    *,
    sha256: str,
    original_filename: str | None,
    mime_type: str,
    size_bytes: int,
    storage_path: str,
    source_channel: str,
    uploaded_by: uuid.UUID | None,
    order_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Create the row in ``received``. **Does not commit.**"""
    document_id = uuid7()
    await session.execute(
        text(
            "INSERT INTO documents "
            "  (id, sha256, original_filename, mime_type, size_bytes, "
            "   storage_path, source_channel, uploaded_by, status, order_id, "
            "   created_by, updated_by) "
            "VALUES "
            "  (CAST(:id AS uuid), :sha, :name, :mime, :size, :path, :channel, "
            "   CAST(:by AS uuid), :status, CAST(:order_id AS uuid), "
            "   CAST(:by AS uuid), CAST(:by AS uuid))"
        ),
        {
            "id": str(document_id),
            "sha": sha256,
            "name": original_filename,
            "mime": mime_type,
            "size": size_bytes,
            "path": storage_path,
            "channel": source_channel,
            "by": str(uploaded_by) if uploaded_by else None,
            "status": STATUS_RECEIVED,
            "order_id": str(order_id) if order_id else None,
        },
    )
    return document_id


async def set_status(
    session: AsyncSession,
    document_id: uuid.UUID,
    status: str,
    *,
    error_text: str | _Unset | None = UNSET,
    page_count: int | None = None,
    started: bool = False,
    ended: bool = False,
    increment_attempts: bool = False,
) -> None:
    """Move a document to a new status. 6.5: written **before and after** each
    stage.

    ``error_text`` takes three values, and the distinction matters: ``UNSET``
    leaves the column as it is, ``None`` clears it, a string replaces it. A
    document that failed, was retried and then succeeded must not still carry
    the message from the attempt that failed — and a document merely changing
    status must not silently lose the explanation of why it is where it is.
    """
    sets = ["status = :status", "updated_at = now()"]
    params: dict[str, Any] = {"id": str(document_id), "status": status}

    if not isinstance(error_text, _Unset):
        if error_text is None:
            sets.append("error_text = NULL")
        else:
            sets.append("error_text = :error")
            params["error"] = error_text[:4000]
    elif status in (STATUS_EXTRACTED, STATUS_EXTRACTING):
        # Entering a working state with no explicit instruction: the previous
        # attempt's message is stale by definition.
        sets.append("error_text = NULL")

    if page_count is not None:
        sets.append("page_count = :pages")
        params["pages"] = page_count
    if started:
        sets.append("processing_started_at = now()")
        sets.append("processing_ended_at = NULL")
    if ended:
        sets.append("processing_ended_at = now()")
    if increment_attempts:
        sets.append("attempts = attempts + 1")

    await session.execute(
        text(
            f"UPDATE documents SET {', '.join(sets)} " " WHERE id = CAST(:id AS uuid)"
        ),
        params,
    )


async def replace_pages(
    session: AsyncSession,
    document_id: uuid.UUID,
    pages: list[dict[str, Any]],
) -> None:
    """Write the page rows, replacing anything a previous attempt left.

    Hard DELETE, not a soft one, and this is the deliberate exception to
    *"never hard delete clinical rows"*: these rows are a derived artefact of
    the file on disk, not a clinical record. The file is the record. Keeping
    every failed attempt's pages would mean the overlay has to guess which
    generation of page 3 to draw.
    """
    await session.execute(
        text("DELETE FROM document_pages WHERE document_id = CAST(:id AS uuid)"),
        {"id": str(document_id)},
    )
    if not pages:
        return
    await session.execute(
        text(
            "INSERT INTO document_pages "
            "  (id, document_id, page_no, width_pt, height_pt, is_scanned, "
            "   text_layer, ocr_confidence, image_path) "
            "VALUES "
            "  (CAST(:id AS uuid), CAST(:doc AS uuid), :page_no, :width, "
            "   :height, :is_scanned, :text_layer, :confidence, :image_path)"
        ),
        [
            {
                "id": str(uuid7()),
                "doc": str(document_id),
                "page_no": page["page_no"],
                "width": page.get("width_pt"),
                "height": page.get("height_pt"),
                "is_scanned": page["is_scanned"],
                "text_layer": page.get("text_layer"),
                "confidence": page.get("ocr_confidence"),
                "image_path": page.get("image_path"),
            }
            for page in pages
        ],
    )


async def replace_spans(
    session: AsyncSession, document_id: uuid.UUID, spans: list[Span]
) -> None:
    """Write the spans, replacing a previous attempt's. Same reasoning as
    :func:`replace_pages`."""
    await session.execute(
        text("DELETE FROM document_spans WHERE document_id = CAST(:id AS uuid)"),
        {"id": str(document_id)},
    )
    if not spans:
        return
    await session.execute(
        text(
            "INSERT INTO document_spans "
            "  (id, document_id, page_no, char_start, char_end, bbox, text) "
            "VALUES "
            "  (CAST(:id AS uuid), CAST(:doc AS uuid), :page_no, :start, "
            "   :end, CAST(:bbox AS jsonb), :text)"
        ),
        [
            {
                "id": str(uuid7()),
                "doc": str(document_id),
                "page_no": span.page_no,
                "start": span.char_start,
                "end": span.char_end,
                # asyncpg cannot encode a dict as a query argument; JSONB
                # needs a string plus an explicit cast. Same trap the pgmq
                # producer hit in D10.
                "bbox": json.dumps(span.bbox, separators=(",", ":")),
                "text": span.text,
            }
            for span in spans
        ],
    )


async def pages_for(session: AsyncSession, document_id: uuid.UUID) -> list[Any]:
    return list(
        (
            await session.execute(
                text(
                    "SELECT page_no, width_pt, height_pt, is_scanned, text_layer, "
                    "       ocr_confidence, image_path "
                    "  FROM document_pages "
                    " WHERE document_id = CAST(:id AS uuid) AND deleted_at IS NULL "
                    " ORDER BY page_no"
                ),
                {"id": str(document_id)},
            )
        ).all()
    )


async def spans_for(
    session: AsyncSession, document_id: uuid.UUID, page_no: int | None = None
) -> list[Any]:
    clause = " AND page_no = :page" if page_no is not None else ""
    params: dict[str, Any] = {"id": str(document_id)}
    if page_no is not None:
        params["page"] = page_no
    return list(
        (
            await session.execute(
                text(
                    "SELECT page_no, char_start, char_end, bbox, text "
                    "  FROM document_spans "
                    " WHERE document_id = CAST(:id AS uuid) AND deleted_at IS NULL"
                    f"{clause} "
                    " ORDER BY page_no, char_start"
                ),
                params,
            )
        ).all()
    )


async def enqueue_ingest(session: AsyncSession, document_id: uuid.UUID) -> None:
    """Put the document on the ``ingest`` queue. **Does not commit.**

    Sharing the caller's transaction is what makes this safe: the row and the
    message become visible together. Enqueueing after a commit would leave a
    window where the worker picks up an id that does not exist yet.
    """
    await session.execute(
        text("SELECT pgmq.send('ingest', CAST(:payload AS jsonb))"),
        {"payload": json.dumps({"document_id": str(document_id)})},
    )


async def review_queue(
    session: AsyncSession, *, limit: int = 50, offset: int = 0
) -> list[DocumentRow]:
    """Everything a human still has to deal with, oldest first.

    Oldest first because this queue is a backlog, and a backlog served
    newest-first grows a tail of documents nobody ever reaches.
    """
    records = (
        await session.execute(
            text(
                _SELECT + " WHERE status = ANY(:statuses) AND deleted_at IS NULL "
                " ORDER BY received_at ASC LIMIT :limit OFFSET :offset"
            ),
            {
                "statuses": list(UNRESOLVED_STATUSES),
                "limit": limit,
                "offset": offset,
            },
        )
    ).all()
    return [_row(r) for r in records]


async def stale_processing(
    session: AsyncSession, *, older_than_seconds: float
) -> list[DocumentRow]:
    """Documents stuck in ``extracting`` past the timeout.

    A worker killed mid-extraction leaves a row claiming to be busy for ever.
    pgmq redelivers the message, but nothing would ever reset the *status* --
    so the document would be invisible to the review queue while being
    genuinely stuck. This is how it gets found.
    """
    records = (
        await session.execute(
            text(
                _SELECT + " WHERE status = :status AND deleted_at IS NULL "
                "   AND processing_started_at < now() "
                "       - make_interval(secs => :secs) "
                " ORDER BY processing_started_at ASC"
            ),
            {"status": STATUS_EXTRACTING, "secs": older_than_seconds},
        )
    ).all()
    return [_row(r) for r in records]
