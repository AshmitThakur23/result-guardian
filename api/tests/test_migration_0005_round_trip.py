"""Phase 2.1 — migration 0005 is genuinely reversible.

A migration that has only ever been run forwards is a migration nobody can
back out of at 2am. This runs the round trip for real: head → 0004 → head,
against the live database, asserting what exists at each stop.

⚠️ **This test mutates schema.** It therefore always restores the database to
head in a ``finally``, whether it passed, failed or was interrupted — leaving
the database at 0004 would fail every other integration test in the suite for
reasons that have nothing to do with them.

It deliberately checks that downgrade removes *only* Phase 2.1's additions. A
downgrade that takes a Phase 1 table, an extension or a queue with it is far
worse than one that fails outright.

The test is **synchronous** on purpose. Alembic's command API is sync and this
project's ``alembic/env.py`` runs its own ``asyncio.run`` internally, so
driving it from an async test would mean nesting event loops. Assertions use
``asyncio.run`` against the app's own asyncpg engine — which also avoids adding
a second database driver just to run a test.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any, TypeVar

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings

pytestmark = pytest.mark.integration

T = TypeVar("T")

TABLE = "sla_timers"
FUNCTION = "rg_sweep_overdue_sla_timers"
CRON_JOB = "rg-sla-timer-sweep"

# Everything downgrade() must leave completely alone.
PHASE_1_TABLES = (
    "departments",
    "users",
    "patients",
    "encounters",
    "orders",
    "discharge_contracts",
    "discharge_contract_revisions",
    "pending_cases",
    "case_events",
    "discharge_medications",
    "discharge_overrides",
    "worker_health",
)
EXTENSIONS = ("vector", "pgmq", "pg_cron", "pg_trgm", "unaccent", "pgcrypto")
QUEUES = ("sla_timers", "notifications", "ingest", "extract", "dlq")


def _run(coro_factory: Callable[[Any], Any]) -> Any:
    """Open an asyncpg connection, hand it to the callable, return the result."""

    async def _inner() -> Any:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect() as conn:
                return await coro_factory(conn)
        finally:
            await engine.dispose()

    return asyncio.run(_inner())


def _reachable() -> bool:
    try:
        _run(lambda conn: conn.execute(text("SELECT 1")))
        return True
    except Exception:
        return False


@pytest.fixture
def alembic_config() -> Config:
    # The project's own alembic.ini and env.py, unmodified: env.py builds the
    # async engine from app settings, so nothing here needs a second driver.
    return Config("alembic.ini")


async def _state(conn: Any) -> dict[str, bool]:
    table = await conn.execute(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name = :n AND table_schema = 'public'"
        ),
        {"n": TABLE},
    )
    function = await conn.execute(
        text("SELECT count(*) FROM pg_proc WHERE proname = :n"), {"n": FUNCTION}
    )
    job = await conn.execute(
        text("SELECT count(*) FROM cron.job WHERE jobname = :n"), {"n": CRON_JOB}
    )
    return {
        "table": bool(table.scalar()),
        "function": bool(function.scalar()),
        "cron_job": bool(job.scalar()),
    }


async def _phase_1_intact(conn: Any) -> None:
    tables = set(
        (
            await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        )
        .scalars()
        .all()
    )
    missing = set(PHASE_1_TABLES) - tables
    assert not missing, f"downgrade removed Phase 1 tables: {sorted(missing)}"

    extensions = set(
        (await conn.execute(text("SELECT extname FROM pg_extension"))).scalars().all()
    )
    assert set(EXTENSIONS) <= extensions, sorted(set(EXTENSIONS) - extensions)

    queues = set(
        (await conn.execute(text("SELECT queue_name FROM pgmq.list_queues()")))
        .scalars()
        .all()
    )
    assert set(QUEUES) <= queues, sorted(set(QUEUES) - queues)

    trigger = (
        await conn.execute(
            text(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname = 'trg_case_events_append_only'"
            )
        )
    ).scalar()
    assert trigger == 1, "downgrade disturbed the case_events append-only trigger"


async def _version(conn: Any) -> str:
    result = await conn.execute(text("SELECT version_num FROM alembic_version"))
    return str(result.scalar_one())


def test_migration_0005_round_trips(alembic_config: Config) -> None:
    if not _reachable():
        pytest.skip("no Postgres reachable — skipped")

    try:
        # ── the starting state: 0005 applied ───────────────────────
        assert all(_run(_state).values()), "0005 is not applied to start with"

        # ── downgrade ──────────────────────────────────────────────
        command.downgrade(alembic_config, "0004_phase_1_2_indexes")

        after = _run(_state)
        assert after == {"table": False, "function": False, "cron_job": False}, after
        _run(_phase_1_intact)
        assert _run(_version) == "0004_phase_1_2_indexes"

        # ── upgrade again ──────────────────────────────────────────
        command.upgrade(alembic_config, "head")

        again = _run(_state)
        assert all(again.values()), again
        _run(_phase_1_intact)
        assert _run(_version) == "0005_sla_timers"

        # Scheduled exactly once — re-running must not leave two jobs doing
        # the same work every five minutes.
        jobs = _run(
            lambda conn: conn.execute(
                text("SELECT count(*) FROM cron.job WHERE jobname = :n"),
                {"n": CRON_JOB},
            )
        )
        assert jobs.scalar() == 1
    finally:
        # Never leave the database behind head, whatever happened above. A
        # failure here is suppressed on purpose: the assertion that already
        # failed is the useful error, and masking it with a restore error
        # would hide it.
        with contextlib.suppress(Exception):
            command.upgrade(alembic_config, "head")


def test_autogenerate_reports_no_drift() -> None:
    """The model and the migration describe the same table.

    Drift means the next developer's autogenerate silently proposes altering a
    table that is already correct — or dropping one it cannot see.
    """
    if not _reachable():
        pytest.skip("no Postgres reachable — skipped")

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db.base import Base

    def _compare(sync_conn: Any) -> list[Any]:
        context = MigrationContext.configure(sync_conn)
        return list(compare_metadata(context, Base.metadata))

    diff = _run(lambda conn: conn.run_sync(_compare))
    assert diff == [], f"schema drift: {diff}"
