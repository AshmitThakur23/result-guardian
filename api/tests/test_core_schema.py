"""Phase 1.1 — the core schema, asserted against a real PostgreSQL.

The build plan's RULE 1 is that the safety property is *"database constraints
and a state machine"*, not application code. These tests check the database
actually enforces what the plan says it should, because a CHECK constraint
that was never applied looks exactly like one that was.

Skips when no database is reachable, like the pgmq integration tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

pytestmark = pytest.mark.integration

PHASE_1_1_TABLES = (
    "departments",
    "users",
    "patients",
    "encounters",
    "orders",
    "discharge_contracts",
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            ready = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name = 'departments'"
                    )
                )
            ).scalar()
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    if not ready:
        await engine.dispose()
        pytest.skip("migration 0002 not applied — skipped")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as s:
            yield s
    finally:
        await engine.dispose()


async def test_all_six_tables_exist(session: AsyncSession) -> None:
    rows = (
        await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
    ).scalars()
    present = set(rows)
    assert set(PHASE_1_1_TABLES) <= present


@pytest.mark.parametrize("table", PHASE_1_1_TABLES)
async def test_every_table_carries_the_audit_columns(
    session: AsyncSession, table: str
) -> None:
    """CLAUDE.md: every table gets created_at, updated_at, created_by, updated_by.

    Plus deleted_at -- clinical rows are never hard-deleted.
    """
    rows = (
        await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t"
            ),
            {"t": table},
        )
    ).scalars()
    columns = set(rows)
    assert {
        "id",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
        "deleted_at",
    } <= columns


@pytest.mark.parametrize("table", PHASE_1_1_TABLES)
async def test_timestamps_are_timestamptz(session: AsyncSession, table: str) -> None:
    """Stored UTC, displayed IST. A naive timestamp column loses the offset."""
    rows = (
        await session.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :t AND data_type LIKE 'timestamp%'"
            ),
            {"t": table},
        )
    ).all()
    assert rows, f"{table} has no timestamp columns"
    naive = [c for c, kind in rows if kind != "timestamp with time zone"]
    assert not naive, f"{table} has naive timestamp columns: {naive}"


async def test_enum_columns_are_text_with_check_not_pg_enums(
    session: AsyncSession,
) -> None:
    """Text + CHECK, never PG enum types -- altering those in a migration hurts,
    and these value sets change per hospital."""
    pg_enums = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_type t JOIN pg_namespace n "
                "ON n.oid = t.typnamespace "
                "WHERE t.typtype = 'e' AND n.nspname = 'public'"
            )
        )
    ).scalar()
    assert pg_enums == 0

    checks = (
        await session.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'c' AND connamespace = 'public'::regnamespace"
            )
        )
    ).scalars()
    names = set(checks)
    for expected in (
        "ck_users_role",
        "ck_patients_preferred_language",
        "ck_encounters_type",
        "ck_encounters_status",
        "ck_orders_category",
        "ck_orders_status",
    ):
        assert expected in names, f"missing CHECK constraint {expected}"


async def test_opd_remains_a_valid_encounter_type(session: AsyncSession) -> None:
    """ADR 0003 keeps 'opd' in the enum while putting OPD out of scope for v1,
    so admitting OPD later is a behaviour change, not a migration."""
    src = (
        await session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_encounters_type'"
            )
        )
    ).scalar()
    assert src is not None
    assert "'opd'" in src


async def test_encounter_status_covers_the_suppression_states(
    session: AsyncSession,
) -> None:
    """lama / transferred / deceased drive Phase 4.6 suppression. Notifying the
    family of a deceased patient is the failure that ends a pilot."""
    src = (
        await session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_encounters_status'"
            )
        )
    ).scalar()
    assert src is not None
    for state in ("lama", "transferred", "deceased"):
        assert f"'{state}'" in src


async def test_one_discharge_contract_per_order(session: AsyncSession) -> None:
    """UNIQUE (order_id) is the database stopping two concurrent discharges
    from both succeeding. It is the safety property, not tidiness."""
    found = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'uq_discharge_contracts_order_id' "
                "AND contype = 'u'"
            )
        )
    ).scalar()
    assert found == 1


async def test_mrn_is_uniquely_indexed(session: AsyncSession) -> None:
    """Phase 7.5 matches results on MRN; a duplicate would attach a result to
    the wrong patient, the one failure mode required to be zero."""
    unique = (
        await session.execute(
            text(
                "SELECT indisunique FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'ix_patients_mrn'"
            )
        )
    ).scalar()
    assert unique is True


async def test_expected_tat_hours_is_numeric_not_float(
    session: AsyncSession,
) -> None:
    """Values as NUMERIC, never float: the gate derives expected_by from
    ordered_at + this, and binary rounding there is a wrong deadline."""
    kind = (
        await session.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'orders' AND column_name = 'expected_tat_hours'"
            )
        )
    ).scalar()
    assert kind == "numeric"


async def test_phase_0_infrastructure_survived_the_migration(
    session: AsyncSession,
) -> None:
    """Phase 1 must not break Phase 0. THE ONE RULE."""
    exts = set(
        (await session.execute(text("SELECT extname FROM pg_extension"))).scalars()
    )
    assert {"pgmq", "pg_cron", "vector", "pg_trgm", "unaccent", "pgcrypto"} <= exts

    queues = set(
        (
            await session.execute(text("SELECT queue_name FROM pgmq.list_queues()"))
        ).scalars()
    )
    assert {"sla_timers", "notifications", "ingest", "extract", "dlq"} <= queues

    # The worker's heartbeat table is modelled now, so a future autogenerate
    # cannot silently propose dropping it.
    still_there = (
        await session.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = 'worker_health'"
            )
        )
    ).scalar()
    assert still_there == 1
