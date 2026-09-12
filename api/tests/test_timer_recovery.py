"""Phase 2.5 — recovery. What happens when the machinery fails.

Every test here breaks something on purpose: the queue loses a message, the
worker dies holding one, the whole stack is down past a deadline, the sweep
runs twice. The property under test is always the same one, and it is the
reason Phase 2 exists:

    **PostgreSQL is timer truth. pgmq is only a doorbell.**

A lost doorbell must cost a few minutes, never a patient. So a timer whose
message is gone is still findable, still overdue, and still fires exactly
once when it is found — and finding it is `pg_cron`'s job, from the table, not
from the queue.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.services.timers import create_timer
from worker.consumers.sla_timers import handle_sla_timer

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


def _ago(hours: float) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)


async def _flags(session: AsyncSession, case_id: str) -> int:
    return int(
        (
            await session.execute(
                text("SELECT count(*) FROM lab_flags WHERE case_id = :c"),
                {"c": case_id},
            )
        ).scalar_one()
    )


async def _sweep(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(text("SELECT rg_sweep_overdue_sla_timers()"))
        ).scalar_one()
    )


# ── I: the message is lost after the timer is created ─────────────────


async def test_a_timer_whose_message_vanished_is_still_findable(
    session: AsyncSession,
) -> None:
    """The scenario the sweep exists for. The row says pending and overdue;
    the queue says nothing at all."""
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(3))

    # The queue loses it. Everything about the timer is still true.
    await session.execute(
        text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
        {"m": created.pgmq_msg_id},
    )

    swept = await _sweep(session)
    assert swept >= 1

    row = (
        await session.execute(
            text("SELECT pgmq_msg_id, attempts, status FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).one()
    assert row.pgmq_msg_id != created.pgmq_msg_id, "no new wake-up was enqueued"
    assert row.attempts == 1
    assert row.status == "pending", "the sweep must not fire the timer itself"


async def test_the_recovered_timer_then_fires_exactly_once(
    session: AsyncSession,
) -> None:
    """Recovery is only worth anything if the recovered timer still produces
    exactly one clinical effect."""
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(3))
    await session.execute(
        text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
        {"m": created.pgmq_msg_id},
    )
    await _sweep(session)

    await handle_sla_timer(session, {"timer_id": str(created.timer_id)})
    assert await _flags(session, ids["case"]) == 1


# ── K: the sweep runs repeatedly ──────────────────────────────────────


async def test_repeated_sweeps_do_not_duplicate_the_timer(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(3))
    await session.execute(
        text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
        {"m": created.pgmq_msg_id},
    )

    for _ in range(3):
        await _sweep(session)

    timers = (
        await session.execute(
            text("SELECT count(*) FROM sla_timers WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert timers == 1, "the sweep created duplicate timer rows"


async def test_a_second_sweep_leaves_a_live_message_alone(
    session: AsyncSession,
) -> None:
    """Once the doorbell is back, sweeping again must not ring a second one --
    otherwise every five minutes would add another copy."""
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(3))
    await session.execute(
        text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
        {"m": created.pgmq_msg_id},
    )
    await _sweep(session)

    after_first = (
        await session.execute(
            text("SELECT pgmq_msg_id, attempts FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).one()

    await _sweep(session)

    after_second = (
        await session.execute(
            text("SELECT pgmq_msg_id, attempts FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).one()
    assert after_second.pgmq_msg_id == after_first.pgmq_msg_id
    assert after_second.attempts == after_first.attempts


async def test_the_sweep_ignores_a_timer_inside_the_grace_window(
    session: AsyncSession,
) -> None:
    """Only more than ten minutes overdue. A timer that has just come due is
    the consumer's job, not the recovery sweep's."""
    ids = await _case(session)
    created = await create_timer(
        session, uuid.UUID(ids["case"]), "result_due", _ago(0.05)
    )
    await session.execute(
        text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
        {"m": created.pgmq_msg_id},
    )
    await _sweep(session)

    attempts = (
        await session.execute(
            text("SELECT attempts FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).scalar_one()
    assert attempts == 0


async def test_the_sweep_ignores_a_paused_timer(session: AsyncSession) -> None:
    """Ringing the doorbell for a patient who has died is exactly what the
    pause exists to prevent — including from the recovery path."""
    from app.services.timers import pause_case_timers

    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(3))
    await session.execute(
        text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
        {"m": created.pgmq_msg_id},
    )
    await pause_case_timers(session, uuid.UUID(ids["case"]), "deceased")

    await _sweep(session)

    attempts = (
        await session.execute(
            text("SELECT attempts FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).scalar_one()
    assert attempts == 0


async def test_the_sweep_ignores_terminal_timers(session: AsyncSession) -> None:
    ids = await _case(session)
    for index, status in enumerate(("fired", "cancelled", "superseded")):
        created = await create_timer(
            session,
            uuid.UUID(ids["case"]),
            "result_due",
            _ago(3 + index),
        )
        await session.execute(
            text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
            {"m": created.pgmq_msg_id},
        )
        await session.execute(
            # CAST on both uses: asyncpg sends parameters untyped, and the
            # same placeholder in two positions makes Postgres deduce text in
            # one and varchar in the other ("inconsistent types deduced for
            # parameter $1"). Same family of trap as D10's overload ambiguity.
            text(
                "UPDATE sla_timers SET status = CAST(:s AS varchar), "
                "fired_at = CASE WHEN CAST(:s AS varchar) = 'fired' "
                "           THEN now() ELSE NULL END "
                "WHERE id = :i"
            ),
            {"s": status, "i": str(created.timer_id)},
        )

    await _sweep(session)

    stirred = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND attempts > 0"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert stirred == 0


# ── H: the stack was down past the deadline ───────────────────────────


async def test_a_deadline_missed_while_the_stack_was_down_still_fires(
    session: AsyncSession,
) -> None:
    """Phase 2.5: "Stop the whole stack for 2 hours past ``fire_at`` -> on
    restart the sweep fires it."

    Downtime is simulated the only way that is honest without stopping
    containers mid-suite: the timer is two hours overdue and its wake-up is
    gone, which is exactly the state a two-hour outage leaves behind once
    pgmq's visibility window has lapsed and nothing consumed it.
    """
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(2))
    await session.execute(
        text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
        {"m": created.pgmq_msg_id},
    )

    # Restart: pg_cron runs the sweep, the worker consumes what it finds.
    await _sweep(session)
    await handle_sla_timer(session, {"timer_id": str(created.timer_id)})

    row = (
        await session.execute(
            text("SELECT status, fired_at FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).one()
    assert row.status == "fired"
    assert row.fired_at is not None
    assert await _flags(session, ids["case"]) == 1


# ── J: duplicate delivery, and the worker dying mid-message ───────────


async def test_a_message_redelivered_after_a_visibility_lapse_has_one_effect(
    session: AsyncSession,
) -> None:
    """Phase 2.5: "Kill worker mid-processing -> message returns after
    visibility timeout -> still exactly one effect."

    The kill is simulated by claiming the message and never acknowledging it,
    then forcing its visibility timeout to lapse so it comes back. That is
    precisely what a SIGKILL mid-handler leaves behind.
    """
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(1))

    # Claim it, as the consumer does, and then "die" -- no delete.
    first = (
        await session.execute(text("SELECT msg_id FROM pgmq.read('sla_timers', 30, 1)"))
    ).first()
    assert first is not None

    # The visibility timeout lapses and the message returns.
    await session.execute(
        text("SELECT pgmq.set_vt('sla_timers', CAST(:m AS bigint), 0)"),
        {"m": first.msg_id},
    )
    redelivered = (
        await session.execute(
            text("SELECT msg_id, read_ct FROM pgmq.read('sla_timers', 30, 1)")
        )
    ).first()
    assert redelivered is not None
    assert redelivered.read_ct >= 2, "the redelivery did not increment read_ct"

    # Both deliveries are handled. Exactly one effect.
    await handle_sla_timer(session, {"timer_id": str(created.timer_id)})
    await handle_sla_timer(session, {"timer_id": str(created.timer_id)})

    assert await _flags(session, ids["case"]) == 1
    fired = (
        await session.execute(
            text(
                "SELECT count(*) FROM case_events "
                " WHERE case_id = :c AND event_type = 'result_due_fired'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert fired == 1


async def test_five_duplicate_deliveries_still_produce_one_flag(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(1))

    for _ in range(5):
        await handle_sla_timer(session, {"timer_id": str(created.timer_id)})

    assert await _flags(session, ids["case"]) == 1


# ── E: the sweep and a worker, at the same time ───────────────────────


async def test_a_sweep_during_firing_does_not_produce_a_second_effect(
    session: AsyncSession,
) -> None:
    """The sweep re-enqueues; the handler then runs twice. One flag."""
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(3))
    await session.execute(
        text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
        {"m": created.pgmq_msg_id},
    )

    await _sweep(session)
    await handle_sla_timer(session, {"timer_id": str(created.timer_id)})
    await _sweep(session)
    await handle_sla_timer(session, {"timer_id": str(created.timer_id)})

    assert await _flags(session, ids["case"]) == 1


async def test_the_sweep_never_fires_a_timer_itself(
    session: AsyncSession,
) -> None:
    """The build plan makes the sweep a *recovery* mechanism: find the timer,
    ring the doorbell, stop. Firing from inside the sweep would put clinical
    effects in a cron job with no lock discipline and no DLQ."""
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _ago(3))
    await session.execute(
        text("DELETE FROM pgmq.q_sla_timers WHERE msg_id = :m"),
        {"m": created.pgmq_msg_id},
    )

    await _sweep(session)

    row = (
        await session.execute(
            text("SELECT status, fired_at FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).one()
    assert row.status == "pending"
    assert row.fired_at is None
    assert await _flags(session, ids["case"]) == 0


# ── the cron job itself ───────────────────────────────────────────────


async def test_the_cron_job_targets_the_configured_database(
    session: AsyncSession,
) -> None:
    """Never a hardcoded name: POSTGRES_DB is configurable, and Phase 0's C6
    fix exists so cron.database_name follows it."""
    row = (
        await session.execute(
            text(
                "SELECT schedule, active, database, current_database() AS here "
                "  FROM cron.job WHERE jobname = 'rg-sla-timer-sweep'"
            )
        )
    ).one()
    assert row.schedule == "*/5 * * * *"
    assert row.active is True
    assert row.database == row.here
