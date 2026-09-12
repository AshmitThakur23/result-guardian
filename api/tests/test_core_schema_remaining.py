"""Phase 1.1 remainder — revisions, cases, events, medications, overrides.

The headline test here is ``test_case_events_rejects_update`` /
``test_case_events_rejects_delete``. The build plan does not ask for
append-only as a convention, it asks for it *enforced with a trigger*, and the
difference only shows up when something actually tries to mutate a row. So
these tests really do issue UPDATE and DELETE and require the database to
refuse them.

Rows are created inside a transaction and rolled back, so the development
database is never left dirty.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

pytestmark = pytest.mark.integration

PHASE_1_1_REMAINING = (
    "discharge_contract_revisions",
    "pending_cases",
    "case_events",
    "discharge_medications",
    "discharge_overrides",
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
                        "WHERE table_name = 'case_events'"
                    )
                )
            ).scalar()
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    if not ready:
        await engine.dispose()
        pytest.skip("migration 0003 not applied — skipped")

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as s:
            yield s
    finally:
        await engine.dispose()


async def _fixture_case(session: AsyncSession) -> dict[str, Any]:
    """Build a minimal patient → encounter → order → contract → case chain.

    Never committed: the caller rolls back, so the dev database stays clean.
    """
    suffix = uuid.uuid4().hex[:10]
    ids = {
        k: str(uuid.uuid4())
        for k in ("dept", "user", "pat", "enc", "ord", "con", "case")
    }

    await session.execute(
        text("INSERT INTO departments (id, code, name) VALUES (:i, :c, 'Test Dept')"),
        {"i": ids["dept"], "c": f"D{suffix}"},
    )
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role, department_id) "
            "VALUES (:i, :e, 'Test Doctor', 'doctor', :d)"
        ),
        {"i": ids["user"], "e": f"E{suffix}", "d": ids["dept"]},
    )
    await session.execute(
        text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, 'Test Patient')"),
        {"i": ids["pat"], "m": f"MRN{suffix}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at) "
            "VALUES (:i, :p, :n, 'ipd', now())"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"ENC{suffix}"},
    )
    await session.execute(
        text(
            "INSERT INTO orders "
            "(id, encounter_id, patient_id, test_code, test_name, category, "
            "ordered_at) "
            "VALUES (:i, :e, :p, 'CULT', 'Urine Culture', 'micro', now())"
        ),
        {"i": ids["ord"], "e": ids["enc"], "p": ids["pat"]},
    )
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": ids["con"], "e": ids["enc"], "o": ids["ord"], "u": ids["user"]},
    )
    await session.execute(
        text(
            "INSERT INTO pending_cases "
            "(id, order_id, encounter_id, patient_id, contract_id, current_owner_id) "
            "VALUES (:i, :o, :e, :p, :c, :u)"
        ),
        {
            "i": ids["case"],
            "o": ids["ord"],
            "e": ids["enc"],
            "p": ids["pat"],
            "c": ids["con"],
            "u": ids["user"],
        },
    )
    return ids


# ── structure ─────────────────────────────────────────────────────────


async def test_all_five_tables_exist(session: AsyncSession) -> None:
    rows = (
        await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
    ).scalars()
    assert set(PHASE_1_1_REMAINING) <= set(rows)


@pytest.mark.parametrize("table", PHASE_1_1_REMAINING)
async def test_audit_columns_present(session: AsyncSession, table: str) -> None:
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
    assert {"id", "created_at", "updated_at", "created_by", "updated_by"} <= columns

    # case_events and discharge_contract_revisions deliberately have no
    # deleted_at: a soft delete is an UPDATE, and neither table may be mutated.
    if table in ("case_events", "discharge_contract_revisions"):
        assert "deleted_at" not in columns
    else:
        assert "deleted_at" in columns


@pytest.mark.parametrize("table", PHASE_1_1_REMAINING)
async def test_timestamps_are_timestamptz(session: AsyncSession, table: str) -> None:
    rows = (
        await session.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :t AND data_type LIKE 'timestamp%'"
            ),
            {"t": table},
        )
    ).all()
    naive = [c for c, kind in rows if kind != "timestamp with time zone"]
    assert not naive, f"{table} has naive timestamp columns: {naive}"


@pytest.mark.parametrize(
    ("table", "column", "target"),
    [
        ("discharge_contract_revisions", "contract_id", "discharge_contracts"),
        ("pending_cases", "order_id", "orders"),
        ("pending_cases", "encounter_id", "encounters"),
        ("pending_cases", "patient_id", "patients"),
        ("pending_cases", "contract_id", "discharge_contracts"),
        ("pending_cases", "current_owner_id", "users"),
        ("case_events", "case_id", "pending_cases"),
        ("case_events", "actor_user_id", "users"),
        ("discharge_medications", "encounter_id", "encounters"),
        ("discharge_overrides", "encounter_id", "encounters"),
        ("discharge_overrides", "order_id", "orders"),
        ("discharge_overrides", "overridden_by", "users"),
        ("discharge_overrides", "approved_by", "users"),
    ],
)
async def test_foreign_key_exists(
    session: AsyncSession, table: str, column: str, target: str
) -> None:
    found = (
        await session.execute(
            text(
                # Joined by name rather than cast with ::regclass --
                # SQLAlchemy's text() parser reads ":t::regclass" as a second
                # bind parameter and the query silently matches nothing.
                "SELECT count(*) FROM pg_constraint c "
                "JOIN pg_class tc ON tc.oid = c.conrelid "
                "JOIN pg_class fc ON fc.oid = c.confrelid "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid "
                "  AND a.attnum = ANY(c.conkey) "
                "WHERE c.contype = 'f' AND tc.relname = :t "
                "  AND fc.relname = :target AND a.attname = :col"
            ),
            {"t": table, "target": target, "col": column},
        )
    ).scalar()
    assert found is not None
    assert found >= 1, f"{table}.{column} -> {target} missing"


@pytest.mark.parametrize(
    "constraint",
    [
        "ck_pending_cases_state",
        "ck_pending_cases_severity",
        "ck_pending_cases_reopened_count_non_negative",
        "ck_discharge_medications_duration_positive",
        "ck_discharge_overrides_reason_code",
        "ck_discharge_overrides_reason_text_min_length",
    ],
)
async def test_check_constraint_exists(session: AsyncSession, constraint: str) -> None:
    found = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = :n AND contype = 'c'"
            ),
            {"n": constraint},
        )
    ).scalar()
    assert found == 1


@pytest.mark.parametrize(
    "index",
    [
        "ix_discharge_contract_revisions_contract_id",
        "ix_pending_cases_current_owner_id",
        "ix_case_events_case_id_occurred_at",
        "ix_discharge_medications_encounter_id",
        "ix_discharge_overrides_encounter_id",
    ],
)
async def test_index_exists(session: AsyncSession, index: str) -> None:
    found = (
        await session.execute(
            text("SELECT count(*) FROM pg_class WHERE relname = :n AND relkind = 'i'"),
            {"n": index},
        )
    ).scalar()
    assert found == 1


async def test_one_case_per_order(session: AsyncSession) -> None:
    found = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'uq_pending_cases_order_id' AND contype = 'u'"
            )
        )
    ).scalar()
    assert found == 1


async def test_no_pg_enum_types_were_introduced(session: AsyncSession) -> None:
    count = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_type t JOIN pg_namespace n "
                "ON n.oid = t.typnamespace "
                "WHERE t.typtype = 'e' AND n.nspname = 'public'"
            )
        )
    ).scalar()
    assert count == 0


# ── append-only enforcement: the important one ────────────────────────


async def test_case_events_trigger_is_installed(session: AsyncSession) -> None:
    found = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname = 'trg_case_events_append_only' AND NOT tgisinternal"
            )
        )
    ).scalar()
    assert found == 1


async def test_case_events_accepts_insert(session: AsyncSession) -> None:
    ids = await _fixture_case(session)
    await session.execute(
        text(
            "INSERT INTO case_events (id, case_id, event_type, payload) "
            "VALUES (:i, :c, 'case_opened', '{\"k\": 1}'::jsonb)"
        ),
        {"i": str(uuid.uuid4()), "c": ids["case"]},
    )
    count = (
        await session.execute(
            text("SELECT count(*) FROM case_events WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).scalar()
    assert count == 1
    await session.rollback()


async def test_case_events_rejects_update(session: AsyncSession) -> None:
    """A case's history is medico-legal evidence. It must not be editable."""
    ids = await _fixture_case(session)
    event_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO case_events (id, case_id, event_type) "
            "VALUES (:i, :c, 'case_opened')"
        ),
        {"i": event_id, "c": ids["case"]},
    )

    with pytest.raises(Exception, match=r"(?i)append-only"):
        await session.execute(
            text("UPDATE case_events SET event_type = 'tampered' WHERE id = :i"),
            {"i": event_id},
        )
    await session.rollback()


async def test_case_events_rejects_delete(session: AsyncSession) -> None:
    ids = await _fixture_case(session)
    event_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO case_events (id, case_id, event_type) "
            "VALUES (:i, :c, 'case_opened')"
        ),
        {"i": event_id, "c": ids["case"]},
    )

    with pytest.raises(Exception, match=r"(?i)append-only"):
        await session.execute(
            text("DELETE FROM case_events WHERE id = :i"), {"i": event_id}
        )
    await session.rollback()


# ── relationships and integrity ───────────────────────────────────────


async def test_pending_case_relationships_work(session: AsyncSession) -> None:
    """The case must join back to order, encounter, patient, contract and owner
    -- Phase 2 timers and Phase 4 ownership both need that chain."""
    ids = await _fixture_case(session)
    row = (
        await session.execute(
            text(
                "SELECT o.test_name, e.encounter_no, p.mrn, "
                "       dc.expected_by IS NOT NULL, u.full_name "
                "FROM pending_cases pc "
                "JOIN orders o ON o.id = pc.order_id "
                "JOIN encounters e ON e.id = pc.encounter_id "
                "JOIN patients p ON p.id = pc.patient_id "
                "JOIN discharge_contracts dc ON dc.id = pc.contract_id "
                "JOIN users u ON u.id = pc.current_owner_id "
                "WHERE pc.id = :i"
            ),
            {"i": ids["case"]},
        )
    ).first()
    assert row is not None
    assert row[0] == "Urine Culture"
    await session.rollback()


async def test_case_without_contract_is_allowed(session: AsyncSession) -> None:
    """The Phase 1.3 override path creates a case with no contract. A case must
    never be blocked from existing because the gate was bypassed."""
    ids = await _fixture_case(session)
    await session.execute(
        text("UPDATE pending_cases SET contract_id = NULL WHERE id = :i"),
        {"i": ids["case"]},
    )
    value = (
        await session.execute(
            text("SELECT contract_id FROM pending_cases WHERE id = :i"),
            {"i": ids["case"]},
        )
    ).scalar()
    assert value is None
    await session.rollback()


async def test_revision_links_to_its_contract(session: AsyncSession) -> None:
    ids = await _fixture_case(session)
    await session.execute(
        text(
            "INSERT INTO discharge_contract_revisions "
            "(id, contract_id, field, old_value, new_value, changed_by, reason) "
            "VALUES (:i, :c, 'expected_by', 'a', 'b', :u, 'lab delayed')"
        ),
        {"i": str(uuid.uuid4()), "c": ids["con"], "u": ids["user"]},
    )
    row = (
        await session.execute(
            text(
                "SELECT r.field, r.reason FROM discharge_contract_revisions r "
                "JOIN discharge_contracts dc ON dc.id = r.contract_id "
                "WHERE r.contract_id = :c"
            ),
            {"c": ids["con"]},
        )
    ).first()
    assert row is not None
    assert row[0] == "expected_by"
    await session.rollback()


async def test_override_rejects_a_bad_reason_code(session: AsyncSession) -> None:
    ids = await _fixture_case(session)
    with pytest.raises(Exception, match=r"(?i)ck_discharge_overrides_reason_code"):
        await session.execute(
            text(
                "INSERT INTO discharge_overrides "
                "(id, encounter_id, order_id, reason_code, reason_text, overridden_by) "
                "VALUES (:i, :e, :o, 'because_i_said_so', "
                "        'a sufficiently long explanation here', :u)"
            ),
            {
                "i": str(uuid.uuid4()),
                "e": ids["enc"],
                "o": ids["ord"],
                "u": ids["user"],
            },
        )
    await session.rollback()


async def test_override_rejects_a_too_short_reason(session: AsyncSession) -> None:
    """A one-word excuse is not an audit trail. The plan's floor is 20 chars."""
    ids = await _fixture_case(session)
    with pytest.raises(Exception, match=r"(?i)reason_text_min_length"):
        await session.execute(
            text(
                "INSERT INTO discharge_overrides "
                "(id, encounter_id, order_id, reason_code, reason_text, overridden_by) "
                "VALUES (:i, :e, :o, 'patient_lama', 'too short', :u)"
            ),
            {
                "i": str(uuid.uuid4()),
                "e": ids["enc"],
                "o": ids["ord"],
                "u": ids["user"],
            },
        )
    await session.rollback()


async def test_override_accepts_a_valid_row(session: AsyncSession) -> None:
    ids = await _fixture_case(session)
    await session.execute(
        text(
            "INSERT INTO discharge_overrides "
            "(id, encounter_id, order_id, reason_code, reason_text, overridden_by) "
            "VALUES (:i, :e, :o, 'clinical_urgency', "
            "        'Patient required urgent transfer to tertiary centre', :u)"
        ),
        {"i": str(uuid.uuid4()), "e": ids["enc"], "o": ids["ord"], "u": ids["user"]},
    )
    count = (
        await session.execute(
            text("SELECT count(*) FROM discharge_overrides WHERE order_id = :o"),
            {"o": ids["ord"]},
        )
    ).scalar()
    assert count == 1
    await session.rollback()


async def test_discharge_medication_rejects_non_positive_duration(
    session: AsyncSession,
) -> None:
    ids = await _fixture_case(session)
    with pytest.raises(Exception, match=r"(?i)duration_positive"):
        await session.execute(
            text(
                "INSERT INTO discharge_medications "
                "(id, encounter_id, drug_name, duration_days) "
                "VALUES (:i, :e, 'Amoxicillin', 0)"
            ),
            {"i": str(uuid.uuid4()), "e": ids["enc"]},
        )
    await session.rollback()


async def test_discharge_medication_supports_rule_b(session: AsyncSession) -> None:
    """Rule B compares a culture's sensitivities against these drugs, so the
    antibiotic flag and the encounter link both have to work."""
    ids = await _fixture_case(session)
    await session.execute(
        text(
            "INSERT INTO discharge_medications "
            "(id, encounter_id, drug_name, atc_code, duration_days, is_antibiotic) "
            "VALUES (:i, :e, 'Amoxicillin-clavulanate', 'J01CR02', 5, true)"
        ),
        {"i": str(uuid.uuid4()), "e": ids["enc"]},
    )
    row = (
        await session.execute(
            text(
                "SELECT m.drug_name, m.is_antibiotic, m.duration_days "
                "FROM discharge_medications m "
                "JOIN encounters e ON e.id = m.encounter_id "
                "WHERE m.encounter_id = :e AND m.is_antibiotic"
            ),
            {"e": ids["enc"]},
        )
    ).first()
    assert row is not None
    assert row[1] is True
    await session.rollback()


async def test_phase_0_infrastructure_still_intact(session: AsyncSession) -> None:
    """THE ONE RULE: a later phase must never break an earlier one."""
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
