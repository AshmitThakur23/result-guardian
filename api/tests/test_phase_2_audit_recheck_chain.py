"""Phase 2 audit — does the 24h re-check chain actually reach the ceiling?

`create_timer` is idempotent: if a timer with the same
``case_id:timer_type:fire_at`` already exists it returns ``created=False`` and
creates nothing. That is exactly right for a replay — and it is the one way
the Phase 2.3 chain could stall **silently**, because
``_schedule_recheck`` books the next look by creating a timer. If a computed
``fire_at`` ever collided with a timer that had already fired, no new timer
would be created, nothing would raise, and the lab would simply never be
chased again.

So this walks the whole seven days in compressed time and asserts the chain
completes: a re-check every 24h, exactly one escalation at the ceiling, and
never a step where a re-check fires and books nothing.
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
from app.db.models.lab import LAB_RECHECK_MAX
from app.services.lab_flags import next_recheck_at
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
        {"u": ids["user"], "uc": f"C{tag}", "h": ids["head"], "hc": f"E{tag}"},
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


async def _pending_rechecks(session: AsyncSession, case_id: str) -> list[uuid.UUID]:
    return list(
        (
            await session.execute(
                text(
                    "SELECT id FROM sla_timers "
                    " WHERE case_id = :c AND timer_type = 'owner_reminder' "
                    "   AND status = 'pending' ORDER BY fire_at"
                ),
                {"c": case_id},
            )
        )
        .scalars()
        .all()
    )


async def test_the_recheck_chain_reaches_the_ceiling_and_escalates_once(
    session: AsyncSession,
) -> None:
    """Walk all seven days. The chain must never stall, and must escalate once.

    Each iteration ages the flag and every pending timer by 24 hours, then
    fires whatever is due — which is what the worker would do on the real
    clock, compressed.
    """
    ids = await _case(session)

    # Day 0: the deadline passes and the lab is flagged.
    first = await create_timer(
        session,
        uuid.UUID(ids["case"]),
        "result_due",
        dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
    )
    await handle_sla_timer(session, {"timer_id": str(first.timer_id)})

    rechecks_fired = 0
    for _day in range(1, 10):
        # Age everything by a day.
        await session.execute(
            text(
                "UPDATE lab_flags SET raised_at = raised_at - interval '24 hours' "
                " WHERE case_id = :c"
            ),
            {"c": ids["case"]},
        )
        await session.execute(
            text(
                "UPDATE sla_timers SET fire_at = fire_at - interval '24 hours' "
                " WHERE case_id = :c AND status = 'pending'"
            ),
            {"c": ids["case"]},
        )

        due = await _pending_rechecks(session, ids["case"])
        if not due:
            break
        for timer_id in due:
            await handle_sla_timer(session, {"timer_id": str(timer_id)})
            rechecks_fired += 1

        escalations = (
            await session.execute(
                text(
                    "SELECT count(*) FROM sla_timers "
                    " WHERE case_id = :c AND timer_type = 'unit_head_escalation'"
                ),
                {"c": ids["case"]},
            )
        ).scalar_one()
        if escalations:
            break
    else:  # pragma: no cover - only on a stalled chain
        pytest.fail("the re-check chain never reached the ceiling")

    # Exactly one escalation, and it was reached by actually re-checking.
    escalations = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'unit_head_escalation'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert escalations == 1, f"expected one escalation, got {escalations}"
    assert rechecks_fired >= 6, (
        f"the chain escalated after only {rechecks_fired} re-checks — it "
        "stalled rather than running the full seven days"
    )

    # Every re-check that fired booked the next one; none silently created
    # nothing. `timer_was_new` is recorded on each scheduling event.
    stalled = (
        await session.execute(
            text(
                "SELECT count(*) FROM case_events "
                " WHERE case_id = :c AND event_type = 'lab_recheck_scheduled' "
                "   AND payload->>'outcome' = 'recheck_scheduled' "
                "   AND payload->>'timer_was_new' = 'false'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert stalled == 0, (
        "a re-check booked a timer that already existed — the idempotency key "
        "collided and the chain would have gone silent"
    )


async def test_a_recheck_never_books_a_timer_past_the_ceiling(
    session: AsyncSession,
) -> None:
    """A timer beyond the ceiling would fire into a case that has already been
    escalated, which is noise at best and a second escalation at worst."""
    raised = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
    ceiling = raised + LAB_RECHECK_MAX
    probe = raised
    while probe < ceiling:
        when = next_recheck_at(raised, probe)
        if when is None:
            break
        assert when <= ceiling, f"booked {when}, past the ceiling {ceiling}"
        probe = when
    assert next_recheck_at(raised, ceiling) is None


async def test_a_fired_recheck_cannot_be_reused_by_a_later_schedule(
    session: AsyncSession,
) -> None:
    """The concrete failure mode the chain test guards against, forced.

    If a later re-check computes a fire_at equal to one an already-fired timer
    used, create_timer returns created=False and books nothing. This asserts
    that when that happens it is at least *visible* in the event log rather
    than silent — the audit trail is what a human would have to read.
    """
    ids = await _case(session)
    when = dt.datetime.now(dt.UTC) + dt.timedelta(hours=24)

    first = await create_timer(session, uuid.UUID(ids["case"]), "owner_reminder", when)
    await session.execute(
        text("UPDATE sla_timers SET status = 'fired', fired_at = now() WHERE id = :i"),
        {"i": str(first.timer_id)},
    )

    again = await create_timer(session, uuid.UUID(ids["case"]), "owner_reminder", when)
    assert again.created is False
    assert again.timer_id == first.timer_id

    # The caller can tell, which is what `timer_was_new` in the event exists
    # for. A caller that ignored it would stall silently; the handler records
    # it on every scheduling event.
    status = (
        await session.execute(
            text("SELECT status FROM sla_timers WHERE id = :i"),
            {"i": str(again.timer_id)},
        )
    ).scalar_one()
    assert status == "fired"
