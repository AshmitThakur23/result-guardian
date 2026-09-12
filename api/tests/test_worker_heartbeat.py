"""Phase 0.7 — worker liveness.

/api/health reports the age of the worker_health row, and Phase 10.4 alerts on
it. A heartbeat that dies quietly is how timers stop firing without anyone
noticing, so the two properties worth pinning are: it writes an upsert, and a
failed beat is logged rather than killing the loop.

No database. The SQL shape and the failure handling are what is under test.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import worker.main as worker_main
from worker.main import HEARTBEAT_INTERVAL_S, WORKER_NAME, heartbeat


class _FakeSession:
    def __init__(self, shutdown: asyncio.Event, explode: bool = False) -> None:
        self.shutdown = shutdown
        self.explode = explode
        self.sql: list[str] = []
        self.params: list[dict[str, Any]] = []
        self.commits = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.sql.append(str(statement))
        self.params.append(params)
        if self.explode:
            # Stop after one failed beat so the test cannot hang.
            self.shutdown.set()
            raise RuntimeError("database is down")

    async def commit(self) -> None:
        self.commits += 1
        self.shutdown.set()  # one beat is enough; end the loop


def _install_fake(monkeypatch: Any, session: _FakeSession) -> None:
    monkeypatch.setattr(worker_main, "get_sessionmaker", lambda: lambda: session)


async def test_heartbeat_upserts_the_worker_row(monkeypatch: Any) -> None:
    shutdown = asyncio.Event()
    session = _FakeSession(shutdown)
    _install_fake(monkeypatch, session)

    await asyncio.wait_for(heartbeat(shutdown), timeout=5.0)

    assert session.commits == 1
    sql = session.sql[0]
    assert "INSERT INTO worker_health" in sql
    # Upsert, not insert: the worker restarts and must not collide with its
    # own previous row.
    assert "ON CONFLICT (worker_name) DO UPDATE" in sql
    assert session.params[0]["n"] == WORKER_NAME


async def test_heartbeat_survives_a_database_failure(monkeypatch: Any) -> None:
    """A failed beat must be logged, not raised.

    If this propagated, one transient database blip would take the whole
    worker down -- and with it every SLA timer.
    """
    shutdown = asyncio.Event()
    session = _FakeSession(shutdown, explode=True)
    _install_fake(monkeypatch, session)

    await asyncio.wait_for(heartbeat(shutdown), timeout=5.0)

    assert session.commits == 0  # never got that far
    assert shutdown.is_set()


async def test_heartbeat_exits_immediately_when_already_shut_down(
    monkeypatch: Any,
) -> None:
    shutdown = asyncio.Event()
    shutdown.set()
    session = _FakeSession(shutdown)
    _install_fake(monkeypatch, session)

    await asyncio.wait_for(heartbeat(shutdown), timeout=5.0)

    assert session.sql == []  # loop body never ran


def test_only_queues_with_a_real_handler_are_consumed() -> None:
    """Phase 2 replaced the placeholder handler, and removing it mattered.

    A stub that logs and returns causes the consumer to ``pgmq.delete`` the
    message. That was harmless while nothing produced messages; it stopped
    being harmless the moment Phase 2.3 began enqueueing notification intents
    and 2.4 began enqueueing classification work, because those were being
    destroyed within two seconds of being created.

    An unconsumed queue is the correct state for a phase that has not been
    built: the messages accumulate durably and wait for their handler.
    """
    import inspect

    source = inspect.getsource(worker_main.main)
    consumed = re.findall(r'QueueConsumer\(\s*"([a-z_]+)"', source)

    assert consumed == ["sla_timers", "classify"], (
        "a queue without a real handler is being consumed, which deletes "
        f"messages a later phase needs: {consumed}"
    )


def test_heartbeat_interval_is_well_inside_the_stale_threshold() -> None:
    """The beat must be frequent enough that /api/health never false-alarms."""
    from app.routers.health import WORKER_STALE_AFTER_S

    assert HEARTBEAT_INTERVAL_S * 2 < WORKER_STALE_AFTER_S
