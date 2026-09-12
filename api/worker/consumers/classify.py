"""The classification consumer. Phase 3.6.

Phase 2.4 has been enqueueing onto ``classify`` since result intake shipped,
with nothing reading it — deliberately, because an unconsumed queue is the
correct state for a phase that has not been built, and a stub consumer would
have *deleted* those messages. This is the handler that finally drains it.

Idempotency comes from the same place it does for timers: the database, not
the queue. ``classifications`` is unique on ``(result_id, engine_version)``,
so a message delivered three times produces one classification, one case
transition and one set of events. The second and third deliveries recompute
the same severity, find the insert already done, and stop before touching the
case.

Handler and ``pgmq.delete`` share one transaction (``worker/consumer.py``), so
the classification and the acknowledgement commit together. A crash between
them leaves the message to be redelivered, which is safe precisely because
this is idempotent.

**No NODE B.** Classification is deterministic table lookups; Phase 8's
narrative explanation is what uses the model, and it is a separate phase.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.rules.orchestrator import ResultNotFoundError, classify_result

log = structlog.get_logger(__name__)


async def handle_classify(session: AsyncSession, message: dict[str, Any]) -> None:
    """Entry point for the ``classify`` queue."""
    raw_result_id = message.get("result_id")
    if not raw_result_id:
        # Nothing to classify and no way to find out what was meant. Dropping
        # is better than guessing: a wrong result_id would classify somebody
        # else's report.
        log.warning("classify_message_without_result_id", message=message)
        return

    try:
        result_id = uuid.UUID(str(raw_result_id))
    except ValueError:
        log.warning("classify_message_bad_result_id", result_id=raw_result_id)
        return

    try:
        classification = await classify_result(session, result_id)
    except ResultNotFoundError:
        # The result was soft-deleted between intake and here. Not an error
        # worth retrying five times into the DLQ.
        log.warning("classify_result_missing", result_id=str(result_id))
        return

    log.info(
        "result_classified",
        result_id=str(result_id),
        case_id=str(classification.case_id) if classification.case_id else None,
        severity=classification.severity,
        engine_version=classification.engine_version,
        created=classification.created,
        auto_closed=classification.auto_closed,
        held_as_preliminary=classification.held_as_preliminary,
        reopened=classification.reopened,
    )
