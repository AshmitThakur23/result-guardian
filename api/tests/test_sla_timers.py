"""Phase 2.1 — the SLA timer model, against a real PostgreSQL.

This is a schema and data-integrity task, so the tests are about what the
*database* refuses, not what the application declines to send. Every constraint
here is a guarantee that survives a bug in application code, a psql session, or
a future phase that forgets the rule — which is the entire point of putting it
in the schema (RULE 1: the safety property is database constraints, not
application logic).

The one behavioural test is the Phase 2.1 sweep, exercised by calling its
function directly rather than waiting five minutes for pg_cron.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models.timers import (
    TIMER_STATUSES,
    TIMER_TYPES,
    idempotency_key,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
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


async def _case(session: AsyncSession) -> dict[str, Any]:
    """A real pending case, built the way the Phase 1 tests build one."""
    tag = uuid.uuid4().hex[:10]
    ids = {
        k: str(uuid.uuid4())
        for k in ("dept", "user", "pat", "enc", "order", "contract", "case")
    }
    await session.execute(
        text("INSERT INTO departments (id, code, name) VALUES (:i, :c, 'D')"),
        {"i": ids["dept"], "c": f"D{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role) "
            "VALUES (:i, :e, 'Dr Test', 'doctor')"
        ),
        {"i": ids["user"], "e": f"E{tag}"},
    )
    await session.execute(
        text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, 'P')"),
        {"i": ids["pat"], "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, "
            "admitted_at, status) VALUES (:i, :p, :n, 'ipd', now(), 'discharged')"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"ENC{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, "
            "test_name, category, ordered_at, status) "
            "VALUES (:i, :e, :p, 'URC', 'Urine Culture', 'micro', now(), 'in_lab')"
        ),
        {"i": ids["order"], "e": ids["enc"], "p": ids["pat"]},
    )
    await session.execute(
        text(
            "INSERT INTO discharge_contracts "
            "(id, encounter_id, order_id, responsible_doctor_id, expected_by) "
            "VALUES (:i, :e, :o, :u, now() + interval '2 days')"
        ),
        {"i": ids["contract"], "e": ids["enc"], "o": ids["order"], "u": ids["user"]},
    )
    await session.execute(
        text(
            "INSERT INTO pending_cases "
            "(id, order_id, encounter_id, patient_id, contract_id, current_owner_id) "
            "VALUES (:i, :o, :e, :p, :c, :u)"
        ),
        {
            "i": ids["case"],
            "o": ids["order"],
            "e": ids["enc"],
            "p": ids["pat"],
            "c": ids["contract"],
            "u": ids["user"],
        },
    )
    await session.commit()
    return ids


async def _insert_timer(
    session: AsyncSession,
    case_id: str,
    *,
    timer_type: str = "result_due",
    status: str = "pending",
    fire_at: dt.datetime | None = None,
    attempts: int = 0,
    fired_at: dt.datetime | None = None,
    pgmq_msg_id: int | None = None,
    key: str | None = None,
) -> str:
    timer_id = str(uuid.uuid4())
    when = fire_at or dt.datetime.now(dt.UTC) + dt.timedelta(days=2)
    await session.execute(
        text(
            "INSERT INTO sla_timers (id, case_id, timer_type, fire_at, status, "
            "pgmq_msg_id, attempts, fired_at, idempotency_key) "
            "VALUES (:i, :c, :tt, :f, :s, :m, :a, :fa, :k)"
        ),
        {
            "i": timer_id,
            "c": case_id,
            "tt": timer_type,
            "f": when,
            "s": status,
            "m": pgmq_msg_id,
            "a": attempts,
            "fa": fired_at,
            "k": key or idempotency_key(case_id, timer_type, when),
        },
    )
    return timer_id


# ── 1-4: every valid status inserts ───────────────────────────────────


async def test_a_pending_timer_inserts(session: AsyncSession) -> None:
    ids = await _case(session)
    timer_id = await _insert_timer(session, ids["case"])
    row = (
        await session.execute(
            text("SELECT status, attempts, fired_at FROM sla_timers WHERE id = :i"),
            {"i": timer_id},
        )
    ).one()
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.fired_at is None


async def test_a_fired_timer_inserts(session: AsyncSession) -> None:
    ids = await _case(session)
    await _insert_timer(
        session, ids["case"], status="fired", fired_at=dt.datetime.now(dt.UTC)
    )


async def test_a_cancelled_timer_inserts(session: AsyncSession) -> None:
    ids = await _case(session)
    await _insert_timer(session, ids["case"], status="cancelled")


async def test_a_superseded_timer_inserts(session: AsyncSession) -> None:
    ids = await _case(session)
    await _insert_timer(session, ids["case"], status="superseded")


async def test_every_declared_status_and_type_is_accepted(
    session: AsyncSession,
) -> None:
    """The model's tuples and the database's CHECKs must not drift apart."""
    ids = await _case(session)
    for status in TIMER_STATUSES:
        await _insert_timer(
            session,
            ids["case"],
            status=status,
            fire_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=len(status)),
            fired_at=dt.datetime.now(dt.UTC) if status == "fired" else None,
            key=f"{ids['case']}:status:{status}",
        )
    for index, timer_type in enumerate(TIMER_TYPES):
        await _insert_timer(
            session,
            ids["case"],
            timer_type=timer_type,
            fire_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=30 + index),
            key=f"{ids['case']}:type:{timer_type}",
        )


# ── 5-9: what the database refuses ────────────────────────────────────


async def test_an_unknown_timer_type_is_refused(session: AsyncSession) -> None:
    """The key must be supplied explicitly here: the helper validates the type
    in Python, and the point of this test is that the *database* refuses it
    even when application code does not."""
    ids = await _case(session)
    with pytest.raises(IntegrityError) as exc:
        await _insert_timer(
            session,
            ids["case"],
            timer_type="made_up_timer",
            key=f"{ids['case']}:made_up_timer:x",
        )
    assert "ck_sla_timers_timer_type" in str(exc.value)
    await session.rollback()


async def test_an_unknown_status_is_refused(session: AsyncSession) -> None:
    ids = await _case(session)
    with pytest.raises(IntegrityError) as exc:
        await _insert_timer(session, ids["case"], status="snoozed")
    assert "ck_sla_timers_status" in str(exc.value)
    await session.rollback()


async def test_negative_attempts_are_refused(session: AsyncSession) -> None:
    ids = await _case(session)
    with pytest.raises(IntegrityError) as exc:
        await _insert_timer(session, ids["case"], attempts=-1)
    assert "ck_sla_timers_attempts_non_negative" in str(exc.value)
    await session.rollback()


async def test_a_case_id_that_does_not_exist_is_refused(
    session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError) as exc:
        await _insert_timer(session, str(uuid.uuid4()))
    assert "sla_timers_case_id_fkey" in str(exc.value)
    await session.rollback()


async def test_a_duplicate_idempotency_key_is_refused(
    session: AsyncSession,
) -> None:
    """The guarantee that the same logical timer cannot exist twice, enforced
    by the database rather than by whoever remembers to check first."""
    ids = await _case(session)
    when = dt.datetime.now(dt.UTC) + dt.timedelta(days=2)
    key = idempotency_key(ids["case"], "result_due", when)

    await _insert_timer(session, ids["case"], fire_at=when, key=key)
    with pytest.raises(IntegrityError) as exc:
        await _insert_timer(session, ids["case"], fire_at=when, key=key)
    assert "uq_sla_timers_idempotency_key" in str(exc.value)
    await session.rollback()


async def test_the_same_timer_computed_twice_collides(
    session: AsyncSession,
) -> None:
    """A replayed discharge recomputes the same key from the same facts, which
    is what makes the unique constraint bite in Phase 2.2."""
    ids = await _case(session)
    when = dt.datetime.now(dt.UTC) + dt.timedelta(days=3)
    assert idempotency_key(ids["case"], "result_due", when) == idempotency_key(
        ids["case"], "result_due", when
    )

    await _insert_timer(session, ids["case"], fire_at=when)
    with pytest.raises(IntegrityError):
        await _insert_timer(session, ids["case"], fire_at=when)
    await session.rollback()


# ── 10-13: relationships, types and state semantics ───────────────────


async def test_a_timer_references_a_real_pending_case(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    timer_id = await _insert_timer(session, ids["case"])
    joined = (
        await session.execute(
            text(
                "SELECT pc.order_id FROM sla_timers s "
                "JOIN pending_cases pc ON pc.id = s.case_id WHERE s.id = :i"
            ),
            {"i": timer_id},
        )
    ).scalar_one()
    assert str(joined) == ids["order"]


async def test_a_case_with_timers_cannot_be_hard_deleted(
    session: AsyncSession,
) -> None:
    """ON DELETE RESTRICT, matching every other clinical FK in this schema.
    Losing the escalation clock because a row was deleted is the worst
    possible response to a mistake."""
    ids = await _case(session)
    await _insert_timer(session, ids["case"])
    with pytest.raises(IntegrityError) as exc:
        await session.execute(
            text("DELETE FROM pending_cases WHERE id = :i"), {"i": ids["case"]}
        )
    assert "sla_timers_case_id_fkey" in str(exc.value)
    await session.rollback()


async def test_every_timestamp_column_is_timezone_aware(
    session: AsyncSession,
) -> None:
    """TIMESTAMPTZ everywhere, per the base conventions. A naive column here
    would mean a deadline interpreted against the server's locale."""
    rows = (
        await session.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'sla_timers' AND data_type LIKE 'timestamp%'"
            )
        )
    ).all()
    assert {r.column_name for r in rows} == {
        "fire_at",
        "fired_at",
        "created_at",
        "updated_at",
        "deleted_at",
    }
    for row in rows:
        assert row.data_type == "timestamp with time zone", row.column_name


async def test_a_returned_timestamp_carries_its_offset(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    when = dt.datetime.now(dt.UTC) + dt.timedelta(days=2)
    timer_id = await _insert_timer(session, ids["case"], fire_at=when)
    got = (
        await session.execute(
            text("SELECT fire_at FROM sla_timers WHERE id = :i"), {"i": timer_id}
        )
    ).scalar_one()
    assert got.tzinfo is not None
    assert abs((got - when).total_seconds()) < 1


async def test_pgmq_msg_id_round_trips_as_a_bigint(session: AsyncSession) -> None:
    """It must hold a real pgmq msg_id, which is bigint — an int4 column would
    overflow silently on a long-lived queue."""
    ids = await _case(session)
    big = 9_223_372_036_854_775_807  # max bigint
    timer_id = await _insert_timer(session, ids["case"], pgmq_msg_id=big)
    assert (
        await session.execute(
            text("SELECT pgmq_msg_id FROM sla_timers WHERE id = :i"), {"i": timer_id}
        )
    ).scalar_one() == big


async def test_pgmq_msg_id_may_be_null(session: AsyncSession) -> None:
    """A timer whose message was consumed, archived or lost is still a timer.
    Finding exactly those is what the sweep is for."""
    ids = await _case(session)
    timer_id = await _insert_timer(session, ids["case"], pgmq_msg_id=None)
    assert (
        await session.execute(
            text("SELECT pgmq_msg_id FROM sla_timers WHERE id = :i"), {"i": timer_id}
        )
    ).scalar_one() is None


async def test_a_fired_timer_must_record_when_it_fired(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    with pytest.raises(IntegrityError) as exc:
        await _insert_timer(session, ids["case"], status="fired", fired_at=None)
    assert "ck_sla_timers_fired_at_matches_status" in str(exc.value)
    await session.rollback()


async def test_an_unfired_timer_cannot_claim_a_fired_at(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    for status in ("pending", "cancelled", "superseded"):
        with pytest.raises(IntegrityError) as exc:
            await _insert_timer(
                session,
                ids["case"],
                status=status,
                fired_at=dt.datetime.now(dt.UTC),
            )
        assert "ck_sla_timers_fired_at_matches_status" in str(exc.value)
        await session.rollback()


# ── 14: indexes exist, with the definitions that were asked for ───────


async def test_the_required_indexes_exist(session: AsyncSession) -> None:
    rows = (
        await session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'sla_timers'"
            )
        )
    ).all()
    by_name = {r.indexname: r.indexdef for r in rows}

    # The plan's one explicit index.
    assert "uq_sla_timers_idempotency_key" in by_name
    assert "UNIQUE" in by_name["uq_sla_timers_idempotency_key"]
    assert "(idempotency_key)" in by_name["uq_sla_timers_idempotency_key"]

    # Timers by case: Phase 2.2 cancels them atomically on closure.
    assert "(case_id)" in by_name["ix_sla_timers_case_id"]

    # Pending timers by deadline, partial — the sweep's exact query.
    pending = by_name["ix_sla_timers_pending_fire_at"]
    assert "(fire_at)" in pending
    assert "WHERE" in pending
    assert "pending" in pending


async def test_the_schema_uses_no_postgres_enum_types(
    session: AsyncSession,
) -> None:
    """text + CHECK, never a PG enum — altering one in a migration is painful
    and these value sets change per hospital."""
    assert (
        await session.execute(text("SELECT count(*) FROM pg_type WHERE typtype = 'e'"))
    ).scalar_one() == 0


# ── the Phase 2.1 sweep ───────────────────────────────────────────────


async def test_the_sweep_is_scheduled_every_five_minutes(
    session: AsyncSession,
) -> None:
    row = (
        await session.execute(
            text(
                "SELECT schedule, command, active, database, "
                "       current_database() AS here "
                "  FROM cron.job WHERE jobname = 'rg-sla-timer-sweep'"
            )
        )
    ).one()
    assert row.schedule == "*/5 * * * *"
    assert "rg_sweep_overdue_sla_timers" in row.command
    assert row.active is True
    # Compared against current_database(), never a hardcoded name: POSTGRES_DB
    # is configurable (CI runs `result_guardian_test`), and Phase 0's C6 fix
    # exists precisely so cron.database_name follows it. Asserting the literal
    # name tested the developer's .env, not the guarantee -- and it broke CI.
    assert row.database == row.here


async def test_the_sweep_re_enqueues_an_overdue_timer_with_no_message(
    session: AsyncSession,
) -> None:
    """The gap the sweep closes: the row says a timer is pending and overdue,
    and there is no queue message to wake anyone up."""
    ids = await _case(session)
    overdue = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30)
    timer_id = await _insert_timer(
        session, ids["case"], fire_at=overdue, pgmq_msg_id=None
    )

    await session.execute(text("SELECT rg_sweep_overdue_sla_timers()"))

    row = (
        await session.execute(
            text("SELECT pgmq_msg_id, attempts, status FROM sla_timers WHERE id = :i"),
            {"i": timer_id},
        )
    ).one()
    assert row.pgmq_msg_id is not None, "no wake-up was enqueued"
    assert row.attempts == 1
    assert row.status == "pending", "the sweep must not change timer state"

    message = (
        await session.execute(
            text("SELECT message FROM pgmq.q_sla_timers WHERE msg_id = :m"),
            {"m": row.pgmq_msg_id},
        )
    ).scalar_one()
    assert message["case_id"] == ids["case"]
    assert message["timer_type"] == "result_due"
    assert message["resent_by"] == "sweep"


async def test_the_sweep_leaves_a_timer_that_still_has_a_message(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    overdue = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30)

    msg_id = (
        await session.execute(
            text(
                "SELECT pgmq.send('sla_timers', "
                'CAST(\'{"timer_type":"result_due"}\' AS jsonb), 0)'
            )
        )
    ).scalar_one()
    timer_id = await _insert_timer(
        session, ids["case"], fire_at=overdue, pgmq_msg_id=int(msg_id)
    )

    await session.execute(text("SELECT rg_sweep_overdue_sla_timers()"))

    row = (
        await session.execute(
            text("SELECT pgmq_msg_id, attempts FROM sla_timers WHERE id = :i"),
            {"i": timer_id},
        )
    ).one()
    assert row.pgmq_msg_id == int(msg_id), "a live message must not be re-sent"
    assert row.attempts == 0


async def test_the_sweep_ignores_timers_inside_the_grace_window(
    session: AsyncSession,
) -> None:
    """Only timers more than 10 minutes overdue. A timer that has just come due
    is the consumer's job, not the recovery sweep's."""
    ids = await _case(session)
    just_due = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=2)
    timer_id = await _insert_timer(session, ids["case"], fire_at=just_due)

    await session.execute(text("SELECT rg_sweep_overdue_sla_timers()"))

    assert (
        await session.execute(
            text("SELECT attempts FROM sla_timers WHERE id = :i"), {"i": timer_id}
        )
    ).scalar_one() == 0


async def test_the_sweep_ignores_timers_that_are_not_pending(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    overdue = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    ignored = []
    for index, status in enumerate(("fired", "cancelled", "superseded")):
        ignored.append(
            await _insert_timer(
                session,
                ids["case"],
                status=status,
                fire_at=overdue - dt.timedelta(minutes=index),
                fired_at=dt.datetime.now(dt.UTC) if status == "fired" else None,
                key=f"{ids['case']}:swept:{status}",
            )
        )

    await session.execute(text("SELECT rg_sweep_overdue_sla_timers()"))

    for timer_id in ignored:
        assert (
            await session.execute(
                text("SELECT attempts FROM sla_timers WHERE id = :i"), {"i": timer_id}
            )
        ).scalar_one() == 0


async def test_the_sweep_ignores_soft_deleted_timers(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    overdue = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    timer_id = await _insert_timer(session, ids["case"], fire_at=overdue)
    await session.execute(
        text("UPDATE sla_timers SET deleted_at = now() WHERE id = :i"), {"i": timer_id}
    )

    await session.execute(text("SELECT rg_sweep_overdue_sla_timers()"))

    assert (
        await session.execute(
            text("SELECT attempts FROM sla_timers WHERE id = :i"), {"i": timer_id}
        )
    ).scalar_one() == 0


# ── the idempotency key helper ────────────────────────────────────────


def test_the_key_is_the_same_instant_regardless_of_timezone() -> None:
    """Two spellings of one moment must not become two timers."""
    utc = dt.datetime(2026, 3, 14, 12, 30, tzinfo=dt.UTC)
    ist = utc.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    case = uuid.uuid4()
    assert idempotency_key(case, "result_due", utc) == idempotency_key(
        case, "result_due", ist
    )


def test_the_key_distinguishes_case_type_and_instant() -> None:
    when = dt.datetime(2026, 3, 14, 12, 30, tzinfo=dt.UTC)
    a, b = uuid.uuid4(), uuid.uuid4()
    keys = {
        idempotency_key(a, "result_due", when),
        idempotency_key(b, "result_due", when),
        idempotency_key(a, "owner_reminder", when),
        idempotency_key(a, "result_due", when + dt.timedelta(days=1)),
    }
    assert len(keys) == 4


def test_the_key_refuses_a_naive_datetime() -> None:
    """A naive deadline is a deadline at the wrong hour, and a key that two
    different instants could share."""
    with pytest.raises(ValueError, match="timezone-aware"):
        idempotency_key(uuid.uuid4(), "result_due", dt.datetime(2026, 3, 14, 12, 30))


def test_the_key_refuses_an_unknown_timer_type() -> None:
    with pytest.raises(ValueError, match="unknown timer_type"):
        idempotency_key(uuid.uuid4(), "made_up", dt.datetime.now(dt.UTC))


# ── Phase 1 is untouched ──────────────────────────────────────────────


async def test_every_phase_1_table_still_exists(session: AsyncSession) -> None:
    expected = {
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
    }
    present = set(
        (
            await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        ).scalars()
    )
    assert expected <= present
    assert "sla_timers" in present


async def test_the_case_events_append_only_trigger_still_refuses(
    session: AsyncSession,
) -> None:
    """Phase 2.1 must not have disturbed Phase 1's strongest guarantee."""
    ids = await _case(session)
    event_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO case_events (id, case_id, event_type, occurred_at) "
            "VALUES (:i, :c, 'case_opened', now())"
        ),
        {"i": event_id, "c": ids["case"]},
    )
    await session.commit()

    with pytest.raises(DBAPIError) as exc:
        await session.execute(
            text("UPDATE case_events SET event_type = 'tampered' WHERE id = :i"),
            {"i": event_id},
        )
    assert "append-only" in str(exc.value)
    await session.rollback()

    with pytest.raises(DBAPIError):
        await session.execute(
            text("DELETE FROM case_events WHERE id = :i"), {"i": event_id}
        )
    await session.rollback()


async def test_all_five_queues_and_every_extension_survive(
    session: AsyncSession,
) -> None:
    queues = set(
        (await session.execute(text("SELECT queue_name FROM pgmq.list_queues()")))
        .scalars()
        .all()
    )
    assert {"sla_timers", "notifications", "ingest", "extract", "dlq"} <= queues

    extensions = set(
        (await session.execute(text("SELECT extname FROM pg_extension")))
        .scalars()
        .all()
    )
    assert {
        "vector",
        "pgmq",
        "pg_cron",
        "pg_trgm",
        "unaccent",
        "pgcrypto",
    } <= extensions
