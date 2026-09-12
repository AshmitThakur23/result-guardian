"""Phase 1.2 — indexes, asserted against PostgreSQL's own catalogue.

These deliberately read ``pg_indexes.indexdef`` rather than SQLAlchemy
metadata. Metadata says what we asked for; ``indexdef`` says what the database
actually built. Phase 1.1 already produced one case where the two disagreed
(five indexes existed in the database but not in the models), so the catalogue
is the source of truth here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

pytestmark = pytest.mark.integration

PHASE_1_2_INDEXES = (
    "ix_orders_encounter_id_status",
    "ix_orders_external_order_id",
    "ix_pending_cases_state_severity_opened_at",
    "ix_pending_cases_current_owner_id",
    "ix_patients_name_trgm",
    "ix_case_events_case_id_occurred_at",
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            ready = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_indexes "
                        "WHERE indexname = 'ix_patients_name_trgm'"
                    )
                )
            ).scalar()
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    if not ready:
        await engine.dispose()
        pytest.skip("migration 0004 not applied — skipped")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as s:
            yield s
    finally:
        await engine.dispose()


async def _indexdef(session: AsyncSession, name: str) -> str:
    value = (
        await session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": name}
        )
    ).scalar()
    assert value is not None, f"index {name} does not exist"
    return str(value)


@pytest.mark.parametrize("name", PHASE_1_2_INDEXES)
async def test_index_exists_and_is_valid(session: AsyncSession, name: str) -> None:
    """Present, and not left INVALID by a failed concurrent build."""
    row = (
        await session.execute(
            text(
                "SELECT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"
            ),
            {"n": name},
        )
    ).scalar()
    assert row is True


# ── composite column ordering ─────────────────────────────────────────


async def test_orders_encounter_status_column_order(session: AsyncSession) -> None:
    """encounter_id first: it is the equality predicate the gate filters on."""
    definition = await _indexdef(session, "ix_orders_encounter_id_status")
    assert "USING btree (encounter_id, status)" in definition


async def test_pending_cases_composite_column_order(session: AsyncSession) -> None:
    """state, severity, opened_at -- the dashboard's worst-first, then-oldest."""
    definition = await _indexdef(session, "ix_pending_cases_state_severity_opened_at")
    assert "USING btree (state, severity, opened_at)" in definition


async def test_case_events_index_satisfies_phase_1_2(session: AsyncSession) -> None:
    """Created by 0003 and NOT duplicated by 0004. 1.2 asks for exactly this."""
    definition = await _indexdef(session, "ix_case_events_case_id_occurred_at")
    assert "USING btree (case_id, occurred_at)" in definition

    # And there is precisely one index on that column pair.
    count = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_indexes WHERE tablename = 'case_events' "
                "AND indexdef LIKE '%case_id, occurred_at%'"
            )
        )
    ).scalar()
    assert count == 1, "case_events(case_id, occurred_at) was duplicated"


# ── partial predicates ────────────────────────────────────────────────


async def test_orders_external_order_id_is_partial(session: AsyncSession) -> None:
    """Partial on NOT NULL: most orders have no external id until the lab
    returns one, and the column is only ever looked up by value."""
    definition = await _indexdef(session, "ix_orders_external_order_id")
    assert "WHERE" in definition
    assert "external_order_id IS NOT NULL" in definition


async def test_pending_cases_owner_index_is_partial(session: AsyncSession) -> None:
    """Partial on the two live states. Postgres normalises IN (...) to
    = ANY (ARRAY[...]), so assert on the values rather than the syntax."""
    definition = await _indexdef(session, "ix_pending_cases_current_owner_id")
    assert "WHERE" in definition
    assert "flagged" in definition
    assert "result_received" in definition


async def test_superseded_plain_indexes_are_gone(session: AsyncSession) -> None:
    """0004 replaced two plain indexes rather than adding beside them.

    Keeping both would cost writes on every insert for no extra read coverage,
    since a partial index still serves equality lookups.
    """
    for name in ("ix_orders_external_order_id", "ix_pending_cases_current_owner_id"):
        count = (
            await session.execute(
                text("SELECT count(*) FROM pg_indexes WHERE indexname = :n"),
                {"n": name},
            )
        ).scalar()
        assert count == 1, f"{name} is duplicated"
        definition = await _indexdef(session, name)
        assert "WHERE" in definition, f"{name} is not partial"


# ── the trigram GIN index ─────────────────────────────────────────────


async def test_patients_name_uses_gin_with_trgm_opclass(
    session: AsyncSession,
) -> None:
    """GIN + gin_trgm_ops specifically. A plain GIN on text would not support
    the similarity search Phase 1.5 and Phase 7.4 depend on."""
    definition = await _indexdef(session, "ix_patients_name_trgm")
    assert "USING gin" in definition
    assert "gin_trgm_ops" in definition


async def test_trgm_opclass_recorded_in_the_catalogue(
    session: AsyncSession,
) -> None:
    """Read the operator class from pg_opclass, not from the DDL string."""
    opclass = (
        await session.execute(
            text(
                "SELECT op.opcname FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "JOIN pg_opclass op ON op.oid = i.indclass[0] "
                "WHERE c.relname = 'ix_patients_name_trgm'"
            )
        )
    ).scalar()
    assert opclass == "gin_trgm_ops"


async def test_trigram_similarity_actually_works(session: AsyncSession) -> None:
    """The operators the index supports are usable -- proves pg_trgm is live,
    not merely that the DDL parsed."""
    score = (
        await session.execute(text("SELECT similarity('Ramesh Kumar', 'Ramesh Kumr')"))
    ).scalar()
    assert score is not None
    assert float(score) > 0.5


async def test_phase_0_and_1_1_indexes_survived(session: AsyncSession) -> None:
    """THE ONE RULE: 1.2 must not break anything 1.1 built."""
    names = set(
        (
            await session.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
        ).scalars()
    )
    assert {
        "ix_patients_mrn",
        "uq_pending_cases_order_id",
        "uq_discharge_contracts_order_id",
        "ix_discharge_medications_encounter_id",
        "ix_discharge_overrides_encounter_id",
        "ix_discharge_contract_revisions_contract_id",
    } <= names

    # And the append-only trigger is untouched.
    trigger = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname = 'trg_case_events_append_only' AND NOT tgisinternal"
            )
        )
    ).scalar()
    assert trigger == 1
