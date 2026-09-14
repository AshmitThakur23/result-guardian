"""Recover documents stuck in ``extracting``. Phase 6.5.

A worker killed mid-extraction — OOM, SIGKILL, a pulled power cable — leaves a
document row saying ``extracting`` for ever. pgmq handles its own half of the
problem: the visibility timeout lapses and the message is redelivered. Nothing
handles the *status*, and that is the half that matters to a human, because
``extracting`` is not in the review queue. The document looks busy. It is
actually abandoned, and the ward is waiting for a result that will never
appear.

    **The workflow never stalls because parsing failed.** — 6.5

A crashed worker is a parsing failure like any other, so it gets the same
treatment: the document moves to ``failed`` with an explanation, which puts it
in the review queue with its pages beside the manual entry form.

**The grace period is deliberately generous** — several times the extraction
timeout. A document being legitimately processed right now must never be
snatched away from the worker doing it; the cost of waiting an extra few
minutes to be sure is nothing, and the cost of getting it wrong is two workers
writing pages for the same document.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog

from app.db.session import get_sessionmaker
from app.services import audit
from app.services.documents import repository
from app.services.documents import settings as ingest_settings

log = structlog.get_logger(__name__)

SWEEP_INTERVAL_S = 60.0

# A document is only considered abandoned after this many times the configured
# extraction timeout.
GRACE_MULTIPLIER = 3.0


async def sweep_once() -> int:
    """One pass. Returns how many documents were recovered."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        tuning = await ingest_settings.load(session)
        cutoff = tuning.ingest_timeout_s * GRACE_MULTIPLIER
        stale = await repository.stale_processing(session, older_than_seconds=cutoff)
        if not stale:
            return 0

        for document in stale:
            log.warning(
                "recovering_stale_document",
                document_id=str(document.id),
                attempts=document.attempts,
            )
            await repository.set_status(
                session,
                document.id,
                repository.STATUS_FAILED,
                error_text=(
                    "Extraction stopped unexpectedly — the worker did not "
                    "finish. Enter this result manually from the page images, "
                    "or retry it from the admin screen."
                ),
                ended=True,
            )
            await audit.append(
                session,
                action=audit.ACTION_DOCUMENT_FAILED,
                entity_type="document",
                entity_id=document.id,
                before={"status": repository.STATUS_EXTRACTING},
                after={
                    "status": repository.STATUS_FAILED,
                    "reason": "abandoned_by_worker",
                    "attempts": document.attempts,
                },
            )
        await session.commit()
        return len(stale)


async def sweep_stale_documents(shutdown: asyncio.Event) -> None:
    while not shutdown.is_set():
        try:
            recovered = await sweep_once()
            if recovered:
                log.info("stale_documents_recovered", count=recovered)
        except Exception:
            log.exception("stale_document_sweep_failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=SWEEP_INTERVAL_S)
