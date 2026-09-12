"""Phase 2 audit — the races the implementation did not cover as real races.

The implementation suite tests worker-vs-worker, worker-vs-closure,
worker-vs-result and concurrent creation. These are the remaining scenarios
from the Phase 2 concurrency list, run the same way: two independent
connections, real commits, real row locks, and an assertion on the **final
database state** rather than on the absence of an exception.

Written during the audit rather than alongside the implementation, so they are
deliberately adversarial about state the implementation might have assumed
rather than enforced.
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
from app.services.timers import (
    claim_timer_for_firing,
    create_timer,
    mark_fired,
    pause_case_timers,
    resume_case_timers,
)
from worker.consumers.sla_timers import handle_sla_timer

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


async def _case(session: AsyncSession) -> dict[str, Any]:
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
        {"u": ids["user"], "uc": f"A{tag}", "h": ids["head"], "hc": f"B{tag}"},
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
            "status, department_id) "
            "VALUES (:i, :p, :n, 'ipd', now(), 'discharged', :d)"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"ENC{tag}", "d": ids["dept"]},
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


async def _cleanup(
    maker: async_sessionmaker[AsyncSession], ids: dict[str, Any]
) -> None:
    async with maker() as s:
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
            "DELETE FROM pgmq.q_notifications WHERE message->>'case_id' = :case",
        ):
            await s.execute(text(sql), ids)
        await s.execute(text("SET session_replication_role = origin"))
        await s.commit()


def _ago(hours: float) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)


async def _flags(maker: async_sessionmaker[AsyncSession], case_id: str) -> int:
    async with maker() as s:
        return int(
            (
                await s.execute(
                    text("SELECT count(*) FROM lab_flags WHERE case_id = :c"),
                    {"c": case_id},
                )
            ).scalar_one()
        )


# ── 8: pause vs a worker already firing ───────────────────────────────


async def test_pausing_while_a_worker_fires_never_yields_both(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """A paused timer must not fire, and a fired timer must not be quietly
    reopened by a pause. Whichever transaction wins, the other must see it."""
    async with maker() as setup:
        ids = await _case(setup)
        created = await create_timer(
            setup, uuid.UUID(ids["case"]), "result_due", _ago(1)
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

        async def pause() -> str:
            async with maker() as s:
                paused = await pause_case_timers(s, uuid.UUID(ids["case"]), "deceased")
                await s.commit()
                return "paused" if paused else "nothing_to_pause"

        outcomes = await asyncio.gather(fire(), pause())

        async with maker() as check:
            row = (
                await check.execute(
                    text(
                        "SELECT status, fired_at, paused_at FROM sla_timers "
                        " WHERE id = :i"
                    ),
                    {"i": str(created.timer_id)},
                )
            ).one()

            # The forbidden state: a timer recorded as fired AND paused.
            assert not (
                row.status == "fired" and row.paused_at is not None
            ), f"timer is both fired and paused: {outcomes}"

            if row.status == "fired":
                assert row.fired_at is not None
                assert "nothing_to_pause" in outcomes
            else:
                assert row.status == "pending"
                assert row.paused_at is not None
                # And it genuinely cannot fire now.
                assert await claim_timer_for_firing(check, created.timer_id) is None
    finally:
        await _cleanup(maker, ids)


# ── 9: resume vs the sweep ────────────────────────────────────────────


async def test_resume_racing_the_sweep_leaves_one_live_wake_up(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """Resume re-enqueues, and so does the sweep. Between them the timer must
    end up with exactly one *live* message it actually points at -- not a
    pgmq_msg_id referring to a message that no longer exists."""
    async with maker() as setup:
        ids = await _case(setup)
        created = await create_timer(
            setup, uuid.UUID(ids["case"]), "result_due", _ago(3)
        )
        await pause_case_timers(setup, uuid.UUID(ids["case"]), "transferred")
        # The wake-up is gone, as it would be after a long pause.
        await setup.execute(
            text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
            {"m": created.pgmq_msg_id},
        )
        await setup.commit()

    try:

        async def resume() -> str:
            async with maker() as s:
                await resume_case_timers(s, uuid.UUID(ids["case"]))
                await s.commit()
                return "resumed"

        async def sweep() -> str:
            async with maker() as s:
                await s.execute(text("SELECT rg_sweep_overdue_sla_timers()"))
                await s.commit()
                return "swept"

        await asyncio.gather(resume(), sweep())

        async with maker() as check:
            row = (
                await check.execute(
                    text(
                        "SELECT pgmq_msg_id, status, paused_at FROM sla_timers "
                        " WHERE id = :i"
                    ),
                    {"i": str(created.timer_id)},
                )
            ).one()
            assert row.status == "pending"
            assert row.paused_at is None

            # Whatever the ordering, the id on the row must name a real message.
            live = (
                await check.execute(
                    text("SELECT count(*) FROM pgmq.q_sla_timers WHERE msg_id = :m"),
                    {"m": row.pgmq_msg_id},
                )
            ).scalar_one()
            assert live == 1, (
                "the timer points at a wake-up that does not exist — it would "
                "stay silent until the next sweep"
            )
    finally:
        await _cleanup(maker, ids)


# ── two sweeps at once ────────────────────────────────────────────────


async def test_two_concurrent_sweeps_do_not_double_enqueue(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """FOR UPDATE ... SKIP LOCKED is what stops two pg_cron runs (or a manual
    run racing the scheduled one) from ringing the doorbell twice."""
    async with maker() as setup:
        ids = await _case(setup)
        created = await create_timer(
            setup, uuid.UUID(ids["case"]), "result_due", _ago(3)
        )
        await setup.execute(
            text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
            {"m": created.pgmq_msg_id},
        )
        await setup.commit()

    try:

        async def sweep() -> int:
            async with maker() as s:
                swept = (
                    await s.execute(text("SELECT rg_sweep_overdue_sla_timers()"))
                ).scalar_one()
                await s.commit()
                return int(swept)

        results = await asyncio.gather(sweep(), sweep())
        assert sum(results) == 1, f"the timer was swept twice: {results}"

        async with maker() as check:
            messages = (
                await check.execute(
                    text(
                        "SELECT count(*) FROM pgmq.q_sla_timers "
                        " WHERE message->>'case_id' = :c"
                    ),
                    {"c": ids["case"]},
                )
            ).scalar_one()
            assert messages == 1
            attempts = (
                await check.execute(
                    text("SELECT attempts FROM sla_timers WHERE id = :i"),
                    {"i": str(created.timer_id)},
                )
            ).scalar_one()
            assert attempts == 1
    finally:
        await _cleanup(maker, ids)


# ── a worker and the sweep, at the same instant ───────────────────────


async def test_a_sweep_concurrent_with_firing_produces_one_flag(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as setup:
        ids = await _case(setup)
        created = await create_timer(
            setup, uuid.UUID(ids["case"]), "result_due", _ago(3)
        )
        await setup.execute(
            text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
            {"m": created.pgmq_msg_id},
        )
        await setup.commit()

    try:

        async def fire() -> str:
            async with maker() as s:
                await handle_sla_timer(s, {"timer_id": str(created.timer_id)})
                await s.commit()
                return "handled"

        async def sweep() -> str:
            async with maker() as s:
                await s.execute(text("SELECT rg_sweep_overdue_sla_timers()"))
                await s.commit()
                return "swept"

        await asyncio.gather(fire(), sweep())
        # And the swept wake-up is then consumed too.
        async with maker() as s:
            await handle_sla_timer(s, {"timer_id": str(created.timer_id)})
            await s.commit()

        assert await _flags(maker, ids["case"]) == 1
    finally:
        await _cleanup(maker, ids)


# ── 6/7: the same result arriving twice at once ───────────────────────


async def test_two_simultaneous_intakes_of_one_report_store_one_result(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """Replay protection is a partial unique index, not a Python check, so it
    has to hold when both callers check at the same moment."""
    from app.services.results import record_result

    async with maker() as setup:
        ids = await _case(setup)
        await setup.commit()

    ref = f"ACC-{uuid.uuid4().hex[:10]}"
    try:

        async def intake() -> str:
            async with maker() as s:
                try:
                    await record_result(
                        s,
                        uuid.UUID(ids["order"]),
                        report_status="final",
                        source="manual",
                        source_ref=ref,
                    )
                    await s.commit()
                    return "recorded"
                except Exception as exc:
                    await s.rollback()
                    return f"refused:{type(exc).__name__}"

        outcomes = await asyncio.gather(intake(), intake())
        assert outcomes.count("recorded") == 1, outcomes

        async with maker() as check:
            count = (
                await check.execute(
                    text("SELECT count(*) FROM results WHERE source_ref = :r"),
                    {"r": ref},
                )
            ).scalar_one()
            assert count == 1, "the same report was stored twice"

            events = (
                await check.execute(
                    text(
                        "SELECT count(*) FROM case_events "
                        " WHERE case_id = :c AND event_type = 'result_received'"
                    ),
                    {"c": ids["case"]},
                )
            ).scalar_one()
            assert events == 1, "one report produced two clinical events"
    finally:
        await _cleanup(maker, ids)


# ── the lab flag, raised from two directions at once ──────────────────


async def test_two_concurrent_flag_raises_produce_one_open_flag(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    from app.services.lab_flags import raise_lab_flag

    async with maker() as setup:
        ids = await _case(setup)
        await setup.commit()

    try:

        async def raise_it() -> str:
            async with maker() as s:
                try:
                    result = await raise_lab_flag(s, uuid.UUID(ids["case"]))
                    await s.commit()
                    return "created" if result.created else "existing"
                except Exception as exc:
                    await s.rollback()
                    return f"refused:{type(exc).__name__}"

        outcomes = await asyncio.gather(raise_it(), raise_it())
        assert outcomes.count("created") == 1, outcomes
        assert await _flags(maker, ids["case"]) == 1
    finally:
        await _cleanup(maker, ids)
