"""Worker entrypoint. Phase 0.7.

Runs the pgmq consumers plus a heartbeat. Started as
``python -m worker.main`` from the same image as the API.

Handlers are stubs until Phase 2 -- the skeleton exists now so the heartbeat
and the shutdown path can be proven at Exit Gate 0.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import dispose_engine, get_sessionmaker
from app.logging import configure_logging
from worker.consumer import QueueConsumer

log = structlog.get_logger(__name__)

WORKER_NAME = os.getenv("RG_WORKER_NAME", "worker-1")
HEARTBEAT_INTERVAL_S = 15


async def heartbeat(shutdown: asyncio.Event) -> None:
    """Write liveness to worker_health.

    /api/health reports the age of this row, and Phase 10.4 alerts when it
    exceeds 5 minutes. A worker that dies silently is how timers stop firing.
    """
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    while not shutdown.is_set():
        try:
            async with sessionmaker() as session:
                await session.execute(
                    text(
                        "INSERT INTO worker_health (worker_name, last_beat_at, pid, version) "
                        "VALUES (:n, now(), :p, :v) "
                        "ON CONFLICT (worker_name) DO UPDATE "
                        "SET last_beat_at = now(), pid = :p, version = :v"
                    ),
                    {"n": WORKER_NAME, "p": os.getpid(), "v": settings.version},
                )
                await session.commit()
        except Exception:
            log.exception("heartbeat_failed")
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=HEARTBEAT_INTERVAL_S)
        except TimeoutError:
            pass


async def _todo_handler(session: AsyncSession, message: dict[str, Any]) -> None:
    """Placeholder. Phase 2 replaces this per queue."""
    log.info("message_received_no_handler", message=message)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("worker_starting", worker=WORKER_NAME, version=settings.version)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown.set)
        except NotImplementedError:
            # Windows dev: add_signal_handler is unsupported on ProactorEventLoop.
            signal.signal(sig, lambda *_: shutdown.set())

    consumers = [
        QueueConsumer("sla_timers", _todo_handler, shutdown),
        QueueConsumer("notifications", _todo_handler, shutdown),
        QueueConsumer("ingest", _todo_handler, shutdown),
        QueueConsumer("extract", _todo_handler, shutdown),
    ]

    tasks = [asyncio.create_task(heartbeat(shutdown))]
    tasks += [asyncio.create_task(c.run()) for c in consumers]

    await shutdown.wait()
    log.info("worker_shutdown_requested")
    # Consumers finish their in-flight message and exit on their own; compose
    # allows 30s (stop_grace_period) before SIGKILL.
    await asyncio.gather(*tasks, return_exceptions=True)
    await dispose_engine()
    log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
