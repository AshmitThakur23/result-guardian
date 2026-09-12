"""Phase 1.5 — the development seed script.

Two properties matter, and neither is "it inserted some rows":

* **Deterministic.** Running it twice must produce the *same* twenty patients,
  not forty. A seed that duplicates on every run is one a developer stops
  running, and then the dev database drifts from what the screens expect.
* **Obviously fake.** Every row has to be unmistakable as synthetic. A seed
  that produces plausible-looking patients in a hospital's dev database is a
  data-governance incident waiting for someone to point a report at it.

The script commits for real, so these tests clean up after themselves.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from scripts.seed_dev import (
    DOCTOR_COUNT,
    EMPLOYEE_PREFIX,
    ENCOUNTER_PREFIX,
    MRN_PREFIX,
    ORDER_COUNT,
    PATIENT_COUNT,
    SEED_TAG,
    _seed,
    seed_id,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        async with engine.connect():
            pass
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"no Postgres reachable ({type(exc).__name__}) — skipped")

    try:
        yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    finally:
        await engine.dispose()


# ── determinism, without touching the database ────────────────────────


def test_ids_are_stable_across_runs() -> None:
    """The whole idempotency argument rests on this."""
    assert seed_id("patient", 3) == seed_id("patient", 3)
    assert seed_id("patient", 3) != seed_id("patient", 4)
    assert seed_id("patient", 3) != seed_id("doctor", 3)


def test_ids_are_uuidv7_like_every_other_pk() -> None:
    """CLAUDE.md: all PKs are UUIDv7, time-sortable. Determinism must not be
    bought by dropping the project's own convention."""
    for kind in ("patient", "doctor", "encounter", "order", "medication"):
        value = seed_id(kind, 0)
        assert value.version == 7, f"{kind}: version {value.version}"
        assert value.variant == uuid.RFC_4122


def test_seeded_ids_sort_in_insertion_order() -> None:
    ids = [seed_id("patient", i) for i in range(PATIENT_COUNT)]
    assert ids == sorted(ids)


# ── the seed itself ───────────────────────────────────────────────────


async def _cleanup(session: AsyncSession) -> None:
    """Remove every seeded row. Hard delete: this is scaffolding, and it only
    ever touches ids from the seed namespace."""
    # case_events is append-only and the trigger correctly refuses a DELETE --
    # which is the whole point of it. Bypassed for this cleanup only, the way
    # test_discharge_action.py already does it: session_replication_role is
    # scoped to this connection and resets when it closes, so unlike
    # ALTER TABLE ... DISABLE TRIGGER a crash here cannot leave the production
    # guard switched off for everyone.
    await session.execute(text("SET session_replication_role = replica"))
    for sql in (
        "DELETE FROM discharge_overrides WHERE encounter_id IN "
        "(SELECT id FROM encounters WHERE encounter_no LIKE :enc)",
        "DELETE FROM case_events WHERE case_id IN (SELECT id FROM pending_cases "
        "WHERE encounter_id IN (SELECT id FROM encounters "
        "WHERE encounter_no LIKE :enc))",
        "DELETE FROM pending_cases WHERE encounter_id IN "
        "(SELECT id FROM encounters WHERE encounter_no LIKE :enc)",
        "DELETE FROM discharge_contracts WHERE encounter_id IN "
        "(SELECT id FROM encounters WHERE encounter_no LIKE :enc)",
        "DELETE FROM discharge_medications WHERE encounter_id IN "
        "(SELECT id FROM encounters WHERE encounter_no LIKE :enc)",
        "DELETE FROM orders WHERE encounter_id IN "
        "(SELECT id FROM encounters WHERE encounter_no LIKE :enc)",
        "DELETE FROM encounters WHERE encounter_no LIKE :enc",
        "DELETE FROM patients WHERE mrn LIKE :mrn",
        "UPDATE users SET department_id = NULL WHERE employee_code LIKE :emp",
        "DELETE FROM departments WHERE code LIKE 'SEED-%'",
        "DELETE FROM users WHERE employee_code LIKE :emp",
    ):
        await session.execute(
            text(sql),
            {
                "enc": f"{ENCOUNTER_PREFIX}%",
                "mrn": f"{MRN_PREFIX}%",
                "emp": f"{EMPLOYEE_PREFIX}%",
            },
        )
    await session.execute(text("SET session_replication_role = origin"))
    await session.commit()


async def _counts(session: AsyncSession) -> dict[str, int]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM patients"
                    " WHERE mrn LIKE :mrn AND deleted_at IS NULL) AS patients, "
                    "(SELECT count(*) FROM users"
                    " WHERE employee_code LIKE :emp"
                    " AND deleted_at IS NULL) AS doctors, "
                    "(SELECT count(*) FROM encounters"
                    " WHERE encounter_no LIKE :enc"
                    " AND deleted_at IS NULL) AS encounters, "
                    "(SELECT count(*) FROM orders o"
                    " JOIN encounters e ON e.id = o.encounter_id"
                    " WHERE e.encounter_no LIKE :enc"
                    " AND o.deleted_at IS NULL) AS orders"
                ),
                {
                    "mrn": f"{MRN_PREFIX}%",
                    "emp": f"{EMPLOYEE_PREFIX}%",
                    "enc": f"{ENCOUNTER_PREFIX}%",
                },
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def test_produces_the_counts_the_build_plan_asks_for(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """20 patients, 60 orders in varied states, 10 doctors."""
    async with maker() as session:
        await _cleanup(session)
        await _seed(session, dt.datetime.now(dt.UTC))
        await session.commit()
        try:
            counts = await _counts(session)
            assert counts["patients"] == PATIENT_COUNT
            assert counts["doctors"] == DOCTOR_COUNT
            assert counts["orders"] == ORDER_COUNT
            assert counts["encounters"] == PATIENT_COUNT
        finally:
            await _cleanup(session)


async def test_running_it_twice_does_not_duplicate(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """The property that makes it safe to re-run."""
    async with maker() as session:
        await _cleanup(session)
        now = dt.datetime.now(dt.UTC)
        try:
            await _seed(session, now)
            await session.commit()
            first = await _counts(session)

            await _seed(session, now)
            await session.commit()
            second = await _counts(session)

            assert first == second
        finally:
            await _cleanup(session)


async def test_every_row_is_obviously_synthetic(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """No seeded row may be mistakable for a real patient record."""
    async with maker() as session:
        await _cleanup(session)
        try:
            await _seed(session, dt.datetime.now(dt.UTC))
            await session.commit()

            rows = (
                await session.execute(
                    text(
                        "SELECT name, mrn, phone_primary_e164 FROM patients "
                        "WHERE mrn LIKE :mrn"
                    ),
                    {"mrn": f"{MRN_PREFIX}%"},
                )
            ).all()
            assert len(rows) == PATIENT_COUNT
            for name, mrn, phone in rows:
                assert SEED_TAG in name, name
                assert mrn.startswith(MRN_PREFIX), mrn
                # 555 is the range reserved for fiction.
                assert phone.startswith("+91555"), phone

            doctors = (
                (
                    await session.execute(
                        text(
                            "SELECT full_name FROM users WHERE employee_code LIKE :emp"
                        ),
                        {"emp": f"{EMPLOYEE_PREFIX}%"},
                    )
                )
                .scalars()
                .all()
            )
            assert all(SEED_TAG in n for n in doctors)
        finally:
            await _cleanup(session)


async def test_orders_are_in_varied_states(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """*"60 orders in varied states"*. A seed where every order is `ordered`
    gives a developer nothing to look at, and no way to see a non-blocking
    order render differently from a blocking one."""
    async with maker() as session:
        await _cleanup(session)
        try:
            await _seed(session, dt.datetime.now(dt.UTC))
            await session.commit()

            statuses = (
                (
                    await session.execute(
                        text(
                            "SELECT DISTINCT o.status FROM orders o "
                            "JOIN encounters e ON e.id = o.encounter_id "
                            "WHERE e.encounter_no LIKE :enc"
                        ),
                        {"enc": f"{ENCOUNTER_PREFIX}%"},
                    )
                )
                .scalars()
                .all()
            )
            # Every value the schema allows should be represented.
            assert set(statuses) == {
                "ordered",
                "collected",
                "in_lab",
                "preliminary",
                "final",
                "cancelled",
                "rejected",
            }, sorted(statuses)
        finally:
            await _cleanup(session)


async def test_gives_the_gate_something_to_block_on(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """The seed exists so a human can exercise the discharge gate. If every
    seeded encounter were already dischargeable, it would not do that."""
    from app.services.discharge_readiness import get_discharge_readiness

    async with maker() as session:
        await _cleanup(session)
        try:
            await _seed(session, dt.datetime.now(dt.UTC))
            await session.commit()

            blocked = 0
            for i in range(PATIENT_COUNT):
                readiness = await get_discharge_readiness(
                    session, seed_id("encounter", i)
                )
                if readiness.blocking_orders:
                    blocked += 1
            assert blocked >= 10, f"only {blocked} of {PATIENT_COUNT} are blocked"
        finally:
            await _cleanup(session)


async def test_the_department_has_a_unit_head(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """Without one, the Phase 1.3 override path 409s on seeded data with
    "no unit head to flag to" -- and the emergency path is unusable."""
    async with maker() as session:
        await _cleanup(session)
        try:
            await _seed(session, dt.datetime.now(dt.UTC))
            await session.commit()

            head = (
                await session.execute(
                    text(
                        "SELECT u.role FROM departments d "
                        "JOIN users u ON u.id = d.unit_head_user_id "
                        "WHERE d.code LIKE 'SEED-%'"
                    )
                )
            ).scalar_one()
            assert head == "unit_head"
        finally:
            await _cleanup(session)


async def test_every_seeded_doctor_is_active(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """The contract endpoint refuses an inactive responsible doctor, so an
    inactive seeded doctor would only produce a confusing 422 in the gate."""
    async with maker() as session:
        await _cleanup(session)
        try:
            await _seed(session, dt.datetime.now(dt.UTC))
            await session.commit()

            inactive = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM users "
                        "WHERE employee_code LIKE :emp AND is_active = false"
                    ),
                    {"emp": f"{EMPLOYEE_PREFIX}%"},
                )
            ).scalar_one()
            assert inactive == 0
        finally:
            await _cleanup(session)
