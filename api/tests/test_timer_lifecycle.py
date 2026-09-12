"""Phase 2.2 — the timer lifecycle, against a real PostgreSQL.

Almost every guarantee in Phase 2 is a statement about *concurrency*, and a
sequential test of a concurrency property proves nothing. So the races here
use two independent connections with real commits and real row locks; the
savepoint-scoped session cannot express them, because both halves would share
a connection and serialise for the wrong reason.

The tests that do not need concurrency use the fast fixture. The ones that do
say so, and clean up after themselves.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models.timers import idempotency_key
from app.services.timers import (
    cancel_case_timers,
    claim_timer_for_firing,
    create_timer,
    mark_fired,
    pause_case_timers,
    resume_case_timers,
    supersede_result_due,
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


async def _case(
    session: AsyncSession, encounter_status: str = "discharged"
) -> dict[str, Any]:
    """A discharged encounter with one open case, built like Phase 1 builds it."""
    tag = uuid.uuid4().hex[:10]
    ids = {
        k: str(uuid.uuid4())
        for k in ("dept", "user", "head", "pat", "enc", "order", "contract", "case")
    }
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role) VALUES "
            "(:u, :uc, 'Dr Owner', 'doctor'), (:h, :hc, 'Dr Head', 'unit_head')"
        ),
        {"u": ids["user"], "uc": f"E{tag}", "h": ids["head"], "hc": f"H{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO departments (id, code, name, unit_head_user_id) "
            "VALUES (:i, :c, 'D', :h)"
        ),
        {"i": ids["dept"], "c": f"D{tag}", "h": ids["head"]},
    )
    await session.execute(
        text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, 'P')"),
        {"i": ids["pat"], "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            "status, department_id) VALUES (:i, :p, :n, 'ipd', now(), :s, :d)"
        ),
        {
            "i": ids["enc"],
            "p": ids["pat"],
            "n": f"ENC{tag}",
            "s": encounter_status,
            "d": ids["dept"],
        },
    )
    await session.execute(
        text(
            "INSERT INTO orders (id, encounter_id, patient_id, test_code, test_name, "
            "category, ordered_at, status) "
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


def _in(hours: float) -> dt.datetime:
    return dt.datetime.now(dt.UTC) + dt.timedelta(hours=hours)


# ── creation ──────────────────────────────────────────────────────────


async def test_create_timer_writes_truth_and_a_wake_up(session: AsyncSession) -> None:
    ids = await _case(session)
    when = _in(48)

    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", when)

    assert created.created is True
    assert created.pgmq_msg_id is not None
    assert created.pgmq_msg_id > 0

    row = (
        await session.execute(
            text(
                "SELECT status, fire_at, pgmq_msg_id, attempts, idempotency_key "
                "  FROM sla_timers WHERE id = :i"
            ),
            {"i": str(created.timer_id)},
        )
    ).one()
    assert row.status == "pending"
    assert row.attempts == 0
    assert abs((row.fire_at - when).total_seconds()) < 1
    assert row.idempotency_key == idempotency_key(ids["case"], "result_due", when)

    # The doorbell exists, and points at the same deadline.
    message = (
        await session.execute(
            text("SELECT message FROM pgmq.q_sla_timers WHERE msg_id = :m"),
            {"m": row.pgmq_msg_id},
        )
    ).scalar_one()
    assert message["timer_id"] == str(created.timer_id)
    assert message["case_id"] == ids["case"]


async def test_creating_the_same_timer_twice_is_a_no_op(
    session: AsyncSession,
) -> None:
    """Replay protection, in the database rather than in a Python check."""
    ids = await _case(session)
    when = _in(48)

    first = await create_timer(session, uuid.UUID(ids["case"]), "result_due", when)
    second = await create_timer(session, uuid.UUID(ids["case"]), "result_due", when)

    assert second.created is False
    assert second.timer_id == first.timer_id

    count = (
        await session.execute(
            text("SELECT count(*) FROM sla_timers WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert count == 1


async def test_a_replay_does_not_ring_a_second_doorbell(
    session: AsyncSession,
) -> None:
    """Two timers would be obvious. Two *messages* for one timer would not, and
    would double every downstream effect if the handler were ever less careful
    than it is."""
    ids = await _case(session)
    when = _in(48)

    await create_timer(session, uuid.UUID(ids["case"]), "result_due", when)
    await create_timer(session, uuid.UUID(ids["case"]), "result_due", when)

    messages = (
        await session.execute(
            text(
                "SELECT count(*) FROM pgmq.q_sla_timers "
                " WHERE message->>'case_id' = :c"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert messages == 1


async def test_different_deadlines_are_different_timers(
    session: AsyncSession,
) -> None:
    """The 24h re-check in 2.3 depends on this: same case, same type, later
    instant must be a new timer, not a collision."""
    ids = await _case(session)
    a = await create_timer(session, uuid.UUID(ids["case"]), "owner_reminder", _in(24))
    b = await create_timer(session, uuid.UUID(ids["case"]), "owner_reminder", _in(48))
    assert a.created
    assert b.created
    assert a.timer_id != b.timer_id


async def test_create_timer_refuses_a_naive_deadline(session: AsyncSession) -> None:
    ids = await _case(session)
    with pytest.raises(ValueError, match="timezone-aware"):
        await create_timer(
            session,
            uuid.UUID(ids["case"]),
            "result_due",
            dt.datetime(2026, 3, 14, 12, 0),
        )


async def test_create_timer_refuses_an_unknown_type(session: AsyncSession) -> None:
    ids = await _case(session)
    with pytest.raises(ValueError, match="unknown timer_type"):
        await create_timer(session, uuid.UUID(ids["case"]), "made_up", _in(1))


# ── firing ────────────────────────────────────────────────────────────


async def test_claim_then_fire_sets_status_and_fired_at_together(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(-1))

    claim = await claim_timer_for_firing(session, created.timer_id)
    assert claim is not None
    assert claim.attempts == 1

    await mark_fired(session, created.timer_id)

    row = (
        await session.execute(
            text("SELECT status, fired_at, attempts FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).one()
    assert row.status == "fired"
    assert row.fired_at is not None
    assert row.attempts == 1


async def test_a_fired_timer_cannot_be_claimed_again(
    session: AsyncSession,
) -> None:
    """Duplicate delivery, the single most important property in Phase 2."""
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(-1))

    first = await claim_timer_for_firing(session, created.timer_id)
    assert first is not None
    await mark_fired(session, created.timer_id)

    for _ in range(3):
        assert await claim_timer_for_firing(session, created.timer_id) is None


@pytest.mark.parametrize("status", ["cancelled", "superseded"])
async def test_a_terminal_timer_cannot_be_claimed(
    session: AsyncSession, status: str
) -> None:
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(-1))
    await session.execute(
        text("UPDATE sla_timers SET status = :s WHERE id = :i"),
        {"s": status, "i": str(created.timer_id)},
    )
    assert await claim_timer_for_firing(session, created.timer_id) is None


async def test_a_paused_timer_cannot_be_claimed(session: AsyncSession) -> None:
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(-1))
    await pause_case_timers(session, uuid.UUID(ids["case"]), "deceased")
    assert await claim_timer_for_firing(session, created.timer_id) is None


async def test_claiming_increments_attempts_even_without_firing(
    session: AsyncSession,
) -> None:
    """attempts counts claims, so a handler that crashes mid-work and is
    redelivered still shows the retry."""
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(-1))
    await claim_timer_for_firing(session, created.timer_id)
    await claim_timer_for_firing(session, created.timer_id)

    attempts = (
        await session.execute(
            text("SELECT attempts FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).scalar_one()
    assert attempts == 2


async def test_mark_fired_will_not_resurrect_a_cancelled_timer(
    session: AsyncSession,
) -> None:
    """Belt and braces on the cancel-vs-fire race: even if a handler somehow
    reached mark_fired after a cancel landed, the WHERE clause refuses."""
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(-1))
    await cancel_case_timers(session, uuid.UUID(ids["case"]))

    await mark_fired(session, created.timer_id)

    status = (
        await session.execute(
            text("SELECT status FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).scalar_one()
    assert status == "cancelled"


# ── cancellation and supersession ─────────────────────────────────────


async def test_cancel_takes_every_pending_timer_on_the_case(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    for hours, kind in (
        (1, "result_due"),
        (24, "owner_reminder"),
        (48, "owner_reminder"),
    ):
        await create_timer(session, uuid.UUID(ids["case"]), kind, _in(hours))

    cancelled = await cancel_case_timers(session, uuid.UUID(ids["case"]))
    assert len(cancelled) == 3

    remaining = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND status = 'pending'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert remaining == 0


async def test_cancel_leaves_an_already_fired_timer_as_history(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(-1))
    await claim_timer_for_firing(session, created.timer_id)
    await mark_fired(session, created.timer_id)

    await cancel_case_timers(session, uuid.UUID(ids["case"]))

    row = (
        await session.execute(
            text("SELECT status, fired_at FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).one()
    assert row.status == "fired", "a fired timer was rewritten by a later cancel"
    assert row.fired_at is not None


async def test_supersede_only_touches_result_due(session: AsyncSession) -> None:
    """The plan distinguishes cancelled from superseded, and so does this: a
    result answers the result_due deadline and nothing else."""
    ids = await _case(session)
    await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(1))
    await create_timer(session, uuid.UUID(ids["case"]), "owner_reminder", _in(24))

    superseded = await supersede_result_due(session, uuid.UUID(ids["case"]))
    assert len(superseded) == 1

    rows = dict(
        (
            await session.execute(
                text("SELECT timer_type, status FROM sla_timers WHERE case_id = :c"),
                {"c": ids["case"]},
            )
        ).all()
    )
    assert rows["result_due"] == "superseded"
    assert rows["owner_reminder"] == "pending"


# ── pause and resume ──────────────────────────────────────────────────


async def test_pause_keeps_the_timer_pending_and_its_deadline(
    session: AsyncSession,
) -> None:
    """Pause preserves timer truth. Cancelling instead would lose the deadline
    and resume would have to invent a new one."""
    ids = await _case(session)
    when = _in(48)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", when)

    paused = await pause_case_timers(session, uuid.UUID(ids["case"]), "deceased")
    assert paused == [created.timer_id]

    row = (
        await session.execute(
            text(
                "SELECT status, paused_at, pause_reason, fire_at "
                "  FROM sla_timers WHERE id = :i"
            ),
            {"i": str(created.timer_id)},
        )
    ).one()
    assert row.status == "pending", "pause must not be a fifth status"
    assert row.paused_at is not None
    assert row.pause_reason == "deceased"
    assert abs((row.fire_at - when).total_seconds()) < 1


async def test_resume_restores_the_original_deadline_and_a_doorbell(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    when = _in(48)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", when)
    await pause_case_timers(session, uuid.UUID(ids["case"]), "transferred")

    resumed = await resume_case_timers(session, uuid.UUID(ids["case"]))
    assert resumed == [created.timer_id]

    row = (
        await session.execute(
            text(
                "SELECT paused_at, pause_reason, fire_at, pgmq_msg_id "
                "  FROM sla_timers WHERE id = :i"
            ),
            {"i": str(created.timer_id)},
        )
    ).one()
    assert row.paused_at is None
    assert row.pause_reason is None
    assert abs((row.fire_at - when).total_seconds()) < 1, "resume moved the deadline"

    live = (
        await session.execute(
            text("SELECT count(*) FROM pgmq.q_sla_timers WHERE msg_id = :m"),
            {"m": row.pgmq_msg_id},
        )
    ).scalar_one()
    assert live == 1, "resume left the timer with no wake-up"


async def test_pause_refuses_a_reason_the_plan_does_not_name(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    with pytest.raises(ValueError, match="pause reason"):
        await pause_case_timers(session, uuid.UUID(ids["case"]), "on_holiday")


async def test_pause_is_idempotent(session: AsyncSession) -> None:
    ids = await _case(session)
    await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(48))

    first = await pause_case_timers(session, uuid.UUID(ids["case"]), "deceased")
    second = await pause_case_timers(session, uuid.UUID(ids["case"]), "deceased")
    assert len(first) == 1
    assert second == [], "a second pause re-paused an already-paused timer"


# ── timezone ──────────────────────────────────────────────────────────


async def test_all_timer_math_is_utc_regardless_of_input_zone(
    session: AsyncSession,
) -> None:
    """Phase 2.5: "DST/timezone: all math in UTC."

    The same instant expressed in IST and UTC must be one timer, not two.
    """
    ids = await _case(session)
    utc = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
    ist = utc.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))

    a = await create_timer(session, uuid.UUID(ids["case"]), "result_due", utc)
    b = await create_timer(session, uuid.UUID(ids["case"]), "result_due", ist)

    assert b.created is False, "the same instant in two zones became two timers"
    assert b.timer_id == a.timer_id


# ── the races: two real connections ───────────────────────────────────


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


async def _cleanup(
    maker: async_sessionmaker[AsyncSession], ids: dict[str, Any]
) -> None:
    async with maker() as s:
        # case_events is append-only and its trigger refuses DELETE; bypassed
        # for this cleanup only, on this connection only.
        await s.execute(text("SET session_replication_role = replica"))
        for sql in (
            "DELETE FROM case_events WHERE case_id = :case",
            "DELETE FROM sla_timers WHERE case_id = :case",
            "DELETE FROM lab_flags WHERE case_id = :case",
            "DELETE FROM results WHERE case_id = :case",
            "DELETE FROM pending_cases WHERE id = :case",
            "DELETE FROM discharge_contracts WHERE id = :contract",
            "DELETE FROM orders WHERE id = :order",
            "DELETE FROM encounters WHERE id = :enc",
            "DELETE FROM patients WHERE id = :pat",
            "DELETE FROM departments WHERE id = :dept",
            "DELETE FROM users WHERE id IN (:user, :head)",
            "DELETE FROM pgmq.q_sla_timers WHERE message->>'case_id' = :case",
        ):
            await s.execute(text(sql), ids)
        await s.execute(text("SET session_replication_role = origin"))
        await s.commit()


async def test_two_workers_racing_the_same_timer_fire_it_once(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """B: timer worker vs timer worker.

    Two independent connections claim the same timer at the same moment. The
    row lock serialises them; exactly one may act.
    """
    async with maker() as setup:
        ids = await _case(setup)
        created = await create_timer(
            setup, uuid.UUID(ids["case"]), "result_due", _in(-1)
        )
        await setup.commit()

    try:

        async def attempt() -> str:
            async with maker() as s:
                claim = await claim_timer_for_firing(s, created.timer_id)
                if claim is None:
                    await s.commit()
                    return "declined"
                await mark_fired(s, created.timer_id)
                await s.commit()
                return "fired"

        results = await asyncio.gather(attempt(), attempt())
        assert sorted(results) == ["declined", "fired"], results

        async with maker() as check:
            row = (
                await check.execute(
                    text(
                        "SELECT status, fired_at, attempts FROM sla_timers "
                        " WHERE id = :i"
                    ),
                    {"i": str(created.timer_id)},
                )
            ).one()
            assert row.status == "fired"
            assert row.fired_at is not None
    finally:
        await _cleanup(maker, ids)


async def test_closing_a_case_while_its_timer_fires_is_safe(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """C: timer worker vs case closure.

    Whichever wins, the timer must not end up both fired and cancelled, and it
    must never be claimable afterwards.
    """
    from app.services.cases import close_case

    async with maker() as setup:
        ids = await _case(setup)
        created = await create_timer(
            setup, uuid.UUID(ids["case"]), "result_due", _in(-1)
        )
        await setup.commit()

    try:

        async def fire() -> str:
            async with maker() as s:
                claim = await claim_timer_for_firing(s, created.timer_id)
                if claim is None:
                    await s.commit()
                    return "declined"
                await mark_fired(s, created.timer_id)
                await s.commit()
                return "fired"

        async def close() -> str:
            async with maker() as s:
                await close_case(s, uuid.UUID(ids["case"]), reason="test")
                await s.commit()
                return "closed"

        outcomes = await asyncio.gather(fire(), close())
        assert "closed" in outcomes

        async with maker() as check:
            status = (
                await check.execute(
                    text("SELECT status FROM sla_timers WHERE id = :i"),
                    {"i": str(created.timer_id)},
                )
            ).scalar_one()
            # Exactly one terminal state, never a mixture.
            assert status in ("fired", "cancelled"), status
            assert await claim_timer_for_firing(check, created.timer_id) is None
    finally:
        await _cleanup(maker, ids)


async def test_a_result_arriving_while_the_timer_fires_is_safe(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """D: timer worker vs result arrival."""
    from app.services.results import record_result

    async with maker() as setup:
        ids = await _case(setup)
        created = await create_timer(
            setup, uuid.UUID(ids["case"]), "result_due", _in(-1)
        )
        await setup.commit()

    try:

        async def fire() -> str:
            async with maker() as s:
                claim = await claim_timer_for_firing(s, created.timer_id)
                if claim is None:
                    await s.commit()
                    return "declined"
                await mark_fired(s, created.timer_id)
                await s.commit()
                return "fired"

        async def result() -> str:
            async with maker() as s:
                await record_result(
                    s, uuid.UUID(ids["order"]), report_status="final", source="manual"
                )
                await s.commit()
                return "recorded"

        outcomes = await asyncio.gather(fire(), result())
        assert "recorded" in outcomes

        async with maker() as check:
            status = (
                await check.execute(
                    text("SELECT status FROM sla_timers WHERE id = :i"),
                    {"i": str(created.timer_id)},
                )
            ).scalar_one()
            assert status in ("fired", "superseded"), status

            state = (
                await check.execute(
                    text("SELECT state FROM pending_cases WHERE id = :i"),
                    {"i": ids["case"]},
                )
            ).scalar_one()
            assert state == "result_received"
    finally:
        await _cleanup(maker, ids)


async def test_two_concurrent_creations_of_one_timer_produce_one(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """A: discharge replay vs timer creation.

    The unique index is the arbiter, not a Python check-then-act.
    """
    async with maker() as setup:
        ids = await _case(setup)
        await setup.commit()

    when = _in(48)
    try:

        async def attempt() -> bool:
            async with maker() as s:
                created = await create_timer(
                    s, uuid.UUID(ids["case"]), "result_due", when
                )
                await s.commit()
                return created.created

        outcomes = await asyncio.gather(
            attempt(), attempt(), attempt(), return_exceptions=True
        )
        # Losers either see created=False or hit the unique index; both are
        # correct, and neither creates a second timer.
        successes = [o for o in outcomes if o is True]
        assert len(successes) == 1, outcomes

        async with maker() as check:
            count = (
                await check.execute(
                    text("SELECT count(*) FROM sla_timers WHERE case_id = :c"),
                    {"c": ids["case"]},
                )
            ).scalar_one()
            assert count == 1
    finally:
        await _cleanup(maker, ids)
