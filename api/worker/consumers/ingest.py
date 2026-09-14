"""The ingest consumer. Phase 6.5.

The ``ingest`` queue has existed since Phase 0.7 with nothing reading it —
deliberately, because a stub consumer would have *deleted* the messages a
later phase was supposed to process. This is the handler that finally drains
it.

**Idempotency comes from the database, not the queue.** pgmq is at-least-once,
so this handler will occasionally run twice on the same document. That is
safe because :func:`~app.services.documents.repository.replace_pages` and
``replace_spans`` replace rather than append: a document processed three times
has one set of pages and one set of spans, from the last attempt. The file on
disk is content-addressed and never rewritten, so every attempt reads
identical bytes and — OCR being deterministic for a fixed model — produces an
identical result.

**This handler almost never raises**, which is unusual for a consumer and is
the point. A raise here means a retry, five retries mean the DLQ, and a
document in the DLQ is one nobody is looking at. Ingestion failures are not
exceptional — an unreadable fax is a Tuesday — so they are *outcomes*: the
document lands in ``needs_review`` or ``failed`` with its pages rendered, and
the message is acknowledged. The queue is for work, not for the record of what
happened to it; the record is the document row and the audit log.

The exception is a genuinely infrastructural failure (the database is gone
mid-transaction), which does propagate, because that one really should be
retried.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.documents import pipeline

log = structlog.get_logger(__name__)


async def handle_ingest(session: AsyncSession, message: dict[str, Any]) -> None:
    """Entry point for the ``ingest`` queue."""
    raw_id = message.get("document_id")
    if not raw_id:
        # No way to find out what was meant. Guessing would process somebody
        # else's report.
        log.warning("ingest_message_without_document_id", message=message)
        return

    try:
        document_id = uuid.UUID(str(raw_id))
    except ValueError:
        log.warning("ingest_message_bad_document_id", document_id=raw_id)
        return

    try:
        outcome = await pipeline.ingest_document(session, document_id)
    except pipeline.DocumentMissingError:
        # Soft-deleted between upload and here. Not worth five retries.
        log.warning("ingest_document_missing", document_id=str(document_id))
        return

    log.info(
        "ingest_handled",
        document_id=str(document_id),
        status=outcome.status,
        pages=len(outcome.pages),
        needs_human=outcome.needs_human,
    )
