"""Generic pgmq consumer loop. Phase 0.7.

Contract, per the build plan:

* ``pgmq.read`` -> handle -> ``pgmq.delete``
* ``pgmq.archive`` on permanent failure (the message is kept, not dropped --
  a discarded clinical message is a lost patient event)
* exponential backoff, max 5 attempts, then DLQ
* graceful shutdown on SIGTERM: finish the in-flight message first

Handlers must be idempotent. pgmq guarantees at-least-once delivery, so a
timer handler that fires twice must still produce exactly one flag. Phase 2.2
enforces this with ``SELECT ... FOR UPDATE`` on the timer row.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker

log = structlog.get_logger(__name__)

Handler = Callable[[AsyncSession, dict[str, Any]], Awaitable[None]]

MAX_ATTEMPTS = 5
VISIBILITY_TIMEOUT_S = 60
EMPTY_QUEUE_SLEEP_S = 2.0


def backoff_seconds(attempt: int) -> int:
    """1, 2, 4, 8, 16 ... capped. Attempt is 1-based."""
    # Shift rather than ``2 **``: int.__pow__ is typed as returning Any
    # (it can yield a float for negative exponents), which defeats strict mode.
    return min(1 << max(attempt - 1, 0), 300)


class QueueConsumer:
    def __init__(self, queue: str, handler: Handler, shutdown: asyncio.Event) -> None:
        self.queue = queue
        self.handler = handler
        self.shutdown = shutdown
        self.log = log.bind(queue=queue)

    async def run(self) -> None:
        self.log.info("consumer_started")
        sessionmaker = get_sessionmaker()
        while not self.shutdown.is_set():
            try:
                async with sessionmaker() as session:
                    processed = await self._read_once(session)
                if not processed:
                    # Wait, but wake immediately on shutdown so SIGTERM is not
                    # delayed by a full poll interval.
                    await self._sleep_or_stop(EMPTY_QUEUE_SLEEP_S)
            except Exception:
                self.log.exception("consumer_loop_error")
                await self._sleep_or_stop(5.0)
        self.log.info("consumer_stopped")

    async def _sleep_or_stop(self, seconds: float) -> None:
        # Timing out just means "nobody asked us to stop" -- the normal case.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.shutdown.wait(), timeout=seconds)

    async def _read_once(self, session: AsyncSession) -> bool:
        row = (
            await session.execute(
                text("SELECT msg_id, read_ct, message FROM pgmq.read(:q, :vt, 1)"),
                {"q": self.queue, "vt": VISIBILITY_TIMEOUT_S},
            )
        ).first()

        if row is None:
            return False

        msg_id, read_ct, message = row
        entry = self.log.bind(msg_id=msg_id, attempt=read_ct)

        try:
            await self.handler(session, message or {})
            # CAST is load-bearing, not decoration. pgmq overloads delete() as
            # (text, bigint) and (text, bigint[]); asyncpg sends parameters
            # untyped, so Postgres sees delete(unknown, unknown), cannot pick an
            # overload, and raises AmbiguousFunctionError. The message is then
            # never deleted and is redelivered forever. See D10.
            await session.execute(
                text("SELECT pgmq.delete(:q, CAST(:id AS bigint))"),
                {"q": self.queue, "id": msg_id},
            )
            await session.commit()
            entry.info("message_handled")
        except Exception:
            await session.rollback()
            entry.exception("message_failed")
            if read_ct >= MAX_ATTEMPTS:
                await self._to_dlq(session, msg_id, message)
            else:
                # Leave it invisible a little longer each time rather than
                # hot-looping a failing message.
                await session.execute(
                    text("SELECT pgmq.set_vt(:q, :id, :vt)"),
                    {"q": self.queue, "id": msg_id, "vt": backoff_seconds(read_ct)},
                )
                await session.commit()
        return True

    async def _to_dlq(self, session: AsyncSession, msg_id: int, message: Any) -> None:
        """Archive the original and copy it to the DLQ for admin retry.

        Archive rather than delete: Phase 6.5 gives admins a retry button, and
        that needs the payload to still exist.
        """
        # pgmq.send takes jsonb, and asyncpg cannot encode a Python dict as a
        # query argument -- it needs a JSON string plus an explicit cast.
        # Passing the dict raised DataError, so the DLQ hop failed silently
        # alongside the archive() ambiguity. Both are D10.
        payload = json.dumps(
            {"queue": self.queue, "msg_id": msg_id, "message": message}
        )
        await session.execute(
            text("SELECT pgmq.send('dlq', CAST(:payload AS jsonb))"),
            {"payload": payload},
        )
        # Same overload ambiguity as delete() above -- archive() is the other
        # pgmq function with a (text, bigint) / (text, bigint[]) pair. D10.
        await session.execute(
            text("SELECT pgmq.archive(:q, CAST(:id AS bigint))"),
            {"q": self.queue, "id": msg_id},
        )
        await session.commit()
        self.log.error("message_dead_lettered", msg_id=msg_id)
