"""Phase 0.7 — pgmq consumer contract.

These assert the property Phase 2's durability rests on: **a clinical message
is never silently dropped.** It is handled and deleted, or retried with
backoff, or archived to the DLQ where an admin can see it. There is no fourth
outcome, and "vanished" is not one of them.

No database here. The consumer's branching is the thing under test, and a real
Postgres would only make these slower and flakier.
"""

from __future__ import annotations

import asyncio
from typing import Any

from worker.consumer import MAX_ATTEMPTS, QueueConsumer, backoff_seconds


class _FakeResult:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def first(self) -> tuple[Any, ...] | None:
        return self._row


class _FakeSession:
    """Records the SQL it is asked to run, and serves queued pgmq.read rows."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = list(rows or [])
        self.sql: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(
        self, statement: Any, params: dict[str, Any] | None = None
    ) -> _FakeResult:
        rendered = str(statement)
        self.sql.append(rendered)
        if "pgmq.read" in rendered:
            return _FakeResult(self.rows.pop(0) if self.rows else None)
        return _FakeResult(None)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def ran(self, fragment: str) -> bool:
        return any(fragment in s for s in self.sql)


def _consumer(handler: Any) -> QueueConsumer:
    return QueueConsumer("sla_timers", handler, asyncio.Event())


async def _ok_handler(session: Any, message: dict[str, Any]) -> None:
    return None


async def _boom_handler(session: Any, message: dict[str, Any]) -> None:
    raise RuntimeError("handler blew up")


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3, 4, 5)] == [1, 2, 4, 8, 16]
    # Never let a poison message sleep for hours.
    assert backoff_seconds(50) == 300
    # Attempt 0 must not produce a fractional or negative delay.
    assert backoff_seconds(0) == 1


async def test_empty_queue_reports_nothing_processed() -> None:
    session = _FakeSession(rows=[])
    assert await _consumer(_ok_handler)._read_once(session) is False
    assert session.commits == 0


async def test_handled_message_is_deleted_and_committed() -> None:
    session = _FakeSession(rows=[(101, 1, {"case_id": "abc"})])

    assert await _consumer(_ok_handler)._read_once(session) is True

    assert session.ran("pgmq.delete")
    assert session.commits == 1
    assert session.rollbacks == 0
    assert not session.ran("pgmq.archive")


async def test_failed_message_is_retried_not_dropped() -> None:
    """A mid-ladder failure backs off and stays on the queue."""
    session = _FakeSession(rows=[(102, 2, {"case_id": "abc"})])

    assert await _consumer(_boom_handler)._read_once(session) is True

    assert session.rollbacks == 1
    assert session.ran("pgmq.set_vt")  # made invisible, then retried
    assert not session.ran("pgmq.delete")  # never deleted on failure
    assert not session.ran("pgmq.archive")  # not terminal yet


async def test_message_goes_to_dlq_after_max_attempts_and_is_archived() -> None:
    """Terminal failure archives rather than deletes.

    Phase 6.5 gives admins a retry button, which needs the payload to still
    exist. Dropping it would lose a patient event with no trace.
    """
    session = _FakeSession(rows=[(103, MAX_ATTEMPTS, {"case_id": "abc"})])

    assert await _consumer(_boom_handler)._read_once(session) is True

    assert session.ran("pgmq.send")  # copied to dlq
    assert session.ran("pgmq.archive")  # original kept, not deleted
    assert not session.ran("pgmq.delete")
    assert session.rollbacks == 1


async def test_null_message_body_still_reaches_the_handler() -> None:
    """A NULL payload must not crash the loop into an infinite retry."""
    seen: list[dict[str, Any]] = []

    async def _record(session: Any, message: dict[str, Any]) -> None:
        seen.append(message)

    session = _FakeSession(rows=[(104, 1, None)])
    assert await _consumer(_record)._read_once(session) is True
    assert seen == [{}]
    assert session.ran("pgmq.delete")


async def test_shutdown_event_ends_the_sleep_immediately() -> None:
    """SIGTERM must not wait out a full poll interval."""
    shutdown = asyncio.Event()
    consumer = QueueConsumer("ingest", _ok_handler, shutdown)
    shutdown.set()

    loop = asyncio.get_running_loop()
    started = loop.time()
    await consumer._sleep_or_stop(30.0)
    assert loop.time() - started < 1.0
