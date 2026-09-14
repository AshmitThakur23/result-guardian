"""Shared pytest fixtures.

**Fixtures live here rather than in an importable module on purpose.** pytest
discovers a `conftest.py` automatically, so a test can name `session` as a
parameter without importing it — and importing a fixture is what makes ruff's
F811 fire, because the import binds the same name the parameter shadows.

Phases 1-4 predate this file and define their own `session` fixture at module
level; pytest prefers the closest definition, so those are untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.services import settings_store
from app.services.rate_limit import login_limiter


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """One outer transaction per test, rolled back at the end.

    ``join_transaction_mode="create_savepoint"`` means the code under test can
    call ``commit()`` normally — it releases a savepoint — while the outer
    transaction still rolls the whole test away. That is what lets these tests
    exercise the real commit paths without leaving rows behind.
    """
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        conn = await engine.connect()
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    trans = await conn.begin()
    maker = async_sessionmaker(
        bind=conn,
        expire_on_commit=False,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with maker() as s:
            yield s
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from app.db.session import get_session
    from app.main import create_app

    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    # The probe would otherwise try to reach NODE B from the test process.
    app.state.llm_probe = _StubProbe()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


class _StubStatus:
    reachable = False
    error = "stubbed_in_tests"

    def to_dict(self) -> dict[str, Any]:
        return {"reachable": False, "error": self.error}


class _StubProbe:
    """Stands in for ``LlmProbe`` so tests never touch the LAN.

    It must keep the **whole** shape the application calls, not just the part
    a given test happens to exercise. When ``invalidate_kill_switch`` was added
    to the real probe on 2026-09-14, this stub lacked it and the admin
    kill-switch tests failed with ``AttributeError`` — the test double had
    silently drifted from the thing it doubles.

    ``invalidated`` is recorded rather than discarded so a test can assert the
    endpoint actually drops the cached value; without that call an admin waits
    up to ``KILL_SWITCH_CACHE_S`` for a switch the UI already says is off.
    """

    def __init__(self) -> None:
        self.invalidated = 0

    async def status(self) -> _StubStatus:
        return _StubStatus()

    def invalidate_kill_switch(self) -> None:
        self.invalidated += 1


@pytest.fixture(autouse=True)
def _reset_process_state() -> Any:
    """The rate limiter and the settings cache are process-global.

    Left alone, the 20-attempt login window leaks between tests and whichever
    test happens to run 21st fails with a 429 that has nothing to do with it.
    """
    login_limiter.reset()
    settings_store.invalidate()
    yield
    login_limiter.reset()
    settings_store.invalidate()


@pytest.fixture(autouse=True, scope="session")
def _close_extraction_pool() -> Any:
    """Shut Phase 6's extraction process pool down at the end of the run.

    ``ProcessPoolExecutor`` leaves a manager thread alive, and if the
    interpreter starts tearing down modules while it is still running, its
    weakref callback raises `AttributeError: 'NoneType' object has no
    attribute 'util'` — noise printed after the summary that looks like a
    test failure and is not.
    """
    yield
    from app.services.documents import pipeline

    pipeline.shutdown_pool()
