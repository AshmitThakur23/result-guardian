"""Worker entrypoint. Phase 0.7.

Runs the pgmq consumers plus a heartbeat. Started as
``python -m worker.main`` from the same image as the API.

``sla_timers`` has had a real handler since Phase 2.2 and ``classify`` since
Phase 3.6. No other queue is consumed: a stub that logs and acknowledges would
*delete* messages that later phases are supposed to process. See the comment
in ``main()``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal

import structlog
from sqlalchemy import text

from app.config import get_settings
from app.db.session import dispose_engine, get_sessionmaker
from app.logging import configure_logging
from worker.consumer import QueueConsumer
from worker.consumers.classify import handle_classify
from worker.consumers.notifications import handle_notification
from worker.consumers.sla_timers import handle_sla_timer

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
                        "INSERT INTO worker_health "
                        "(worker_name, last_beat_at, pid, version) "
                        "VALUES (:n, now(), :p, :v) "
                        "ON CONFLICT (worker_name) DO UPDATE "
                        "SET last_beat_at = now(), pid = :p, version = :v"
                    ),
                    {"n": WORKER_NAME, "p": os.getpid(), "v": settings.version},
                )
                await session.commit()
        except Exception:
            log.exception("heartbeat_failed")
        # Timing out just means "nobody asked us to stop" -- beat again.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=HEARTBEAT_INTERVAL_S)


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

    # ⚠️ Only queues with a REAL handler get a consumer.
    #
    # Phase 0.7 started a stub consumer on every queue to prove the skeleton,
    # which was harmless while nothing produced messages. It stopped being
    # harmless the moment Phase 2.3 began enqueueing notification intents and
    # Phase 2.4 began enqueueing classification work: the stub logs the message
    # and the consumer then DELETES it, so an obligation the plan requires to
    # be recorded ("Notify: lab department queue + responsible doctor") was
    # being destroyed within two seconds of being created.
    #
    # An unconsumed queue is the correct state for a phase that has not been
    # built. The messages accumulate durably, survive restarts, and are there
    # for Phase 6 (ingest) and Phase 7 (extract) to consume when those phases add
    # their handlers. Phase 3 took `classify`; Phase 4.3 takes `notifications`.
    consumers = [
        # The only real handler in Phase 2. It is idempotent by construction
        # (SELECT ... FOR UPDATE on the timer row), which is what lets pgmq's
        # at-least-once delivery be safe rather than merely tolerable.
        QueueConsumer("sla_timers", handle_sla_timer, shutdown),
        # Phase 3.6. Deterministic, no NODE B: a classification is table
        # lookups and comparisons, and `classifications` being unique on
        # (result_id, engine_version) is what makes a duplicate delivery
        # produce one decision rather than two.
        QueueConsumer("classify", handle_classify, shutdown),
        # Phase 4.3. The intents Phase 2.3 has been enqueueing since lab flags
        # shipped finally have a consumer. Idempotent on `notifications`
        # .dedupe_key, so a redelivered intent produces one message.
        QueueConsumer("notifications", handle_notification, shutdown),
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
