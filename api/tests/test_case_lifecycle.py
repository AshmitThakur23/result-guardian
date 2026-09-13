"""Phase 2.2 — case closure, pause and resume.

Two of 2.2's bullets are statements about the *case*: closing one must stop
its clock atomically, and a case whose patient has died or been transferred
must stop chasing people without losing the tracking.

Scope, stated once: this is not Phase 5.3's closure workflow. There is no
reason-code vocabulary, no acknowledgement flow, no reopening. Only the part
Phase 2 owns — when a case stops being open, its timers stop firing.
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
from app.services.cases import (
    CaseNotFoundError,
    close_case,
    pause_case_for_encounter_status,
    resume_case,
)
from app.services.timers import claim_timer_for_firing, create_timer

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
    tag = uuid.uuid4().hex[:10]
    ids = {
        k: str(uuid.uuid4())
        for k in ("dept", "user", "pat", "enc", "order", "contract", "case")
    }
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role) "
            "VALUES (:u, :uc, 'Dr Owner', 'doctor')"
        ),
        {"u": ids["user"], "uc": f"E{tag}"},
    )
    await session.execute(
        text("INSERT INTO departments (id, code, name) VALUES (:i, :c, 'D')"),
        {"i": ids["dept"], "c": f"D{tag}"},
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


async def _events(session: AsyncSession, case_id: str) -> list[str]:
    return list(
        (
            await session.execute(
                text("SELECT event_type FROM case_events WHERE case_id = :c"),
                {"c": case_id},
            )
        )
        .scalars()
        .all()
    )


# ── closure ───────────────────────────────────────────────────────────


async def test_closing_a_case_cancels_every_pending_timer(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    for kind, hours in (("result_due", 1), ("owner_reminder", 24)):
        await create_timer(session, uuid.UUID(ids["case"]), kind, _in(hours))

    closed = await close_case(
        session, uuid.UUID(ids["case"]), reason="auto_closed_normal", note="seen"
    )
    assert closed.already_closed is False
    assert len(closed.cancelled_timer_ids) == 2

    pending = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND status = 'pending'"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert pending == 0


async def test_a_cancelled_timer_can_never_fire_afterwards(
    session: AsyncSession,
) -> None:
    """The point of cancelling: the doorbell may still ring, and nothing must
    happen when it does."""
    ids = await _case(session)
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(-1))
    await close_case(session, uuid.UUID(ids["case"]))
    assert await claim_timer_for_firing(session, created.timer_id) is None


async def test_closing_records_the_closure_and_the_cancellation(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(1))
    await close_case(session, uuid.UUID(ids["case"]), reason="auto_closed_normal")

    events = await _events(session, ids["case"])
    assert "case_closed" in events
    assert "timers_cancelled" in events


async def test_closing_twice_is_idempotent(session: AsyncSession) -> None:
    ids = await _case(session)
    await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(1))

    first = await close_case(
        session, uuid.UUID(ids["case"]), reason="auto_closed_normal"
    )
    second = await close_case(
        session, uuid.UUID(ids["case"]), reason="auto_closed_normal"
    )

    assert first.already_closed is False
    assert second.already_closed is True

    closures = [e for e in await _events(session, ids["case"]) if e == "case_closed"]
    assert len(closures) == 1, "a replayed closure wrote a second event"


async def test_closing_an_unknown_case_is_refused(session: AsyncSession) -> None:
    with pytest.raises(CaseNotFoundError):
        await close_case(session, uuid.uuid4())


async def test_closing_sets_closed_at_once(session: AsyncSession) -> None:
    ids = await _case(session)
    await close_case(session, uuid.UUID(ids["case"]), reason="auto_closed_normal")
    first = (
        await session.execute(
            text("SELECT closed_at FROM pending_cases WHERE id = :i"),
            {"i": ids["case"]},
        )
    ).scalar_one()

    await close_case(session, uuid.UUID(ids["case"]), reason="auto_closed_normal")
    second = (
        await session.execute(
            text("SELECT closed_at FROM pending_cases WHERE id = :i"),
            {"i": ids["case"]},
        )
    ).scalar_one()
    assert first == second


# ── pause and resume ──────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["deceased", "transferred"])
async def test_a_case_pauses_for_the_two_statuses_the_plan_names(
    session: AsyncSession, status: str
) -> None:
    ids = await _case(session, encounter_status=status)
    await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(48))

    paused = await pause_case_for_encounter_status(session, uuid.UUID(ids["case"]))
    assert paused is not None
    assert paused.reason == status
    assert len(paused.paused_timer_ids) == 1
    assert "timers_paused" in await _events(session, ids["case"])


async def test_a_live_encounter_does_not_pause(session: AsyncSession) -> None:
    ids = await _case(session, encounter_status="discharged")
    await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(48))

    assert (
        await pause_case_for_encounter_status(session, uuid.UUID(ids["case"])) is None
    )

    paused = (
        await session.execute(
            text(
                "SELECT count(*) FROM sla_timers "
                " WHERE case_id = :c AND paused_at IS NOT NULL"
            ),
            {"c": ids["case"]},
        )
    ).scalar_one()
    assert paused == 0


async def test_the_pause_reason_comes_from_the_encounter_not_a_caller(
    session: AsyncSession,
) -> None:
    """A client able to assert "this patient is deceased" could silence any
    case's timers at will. The function takes no reason argument at all."""
    import inspect

    signature = inspect.signature(pause_case_for_encounter_status)
    assert "reason" not in signature.parameters


async def test_resume_restores_firing(session: AsyncSession) -> None:
    ids = await _case(session, encounter_status="deceased")
    created = await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(-1))
    await pause_case_for_encounter_status(session, uuid.UUID(ids["case"]))
    assert await claim_timer_for_firing(session, created.timer_id) is None

    resumed = await resume_case(session, uuid.UUID(ids["case"]))
    assert resumed == [created.timer_id]
    assert await claim_timer_for_firing(session, created.timer_id) is not None
    assert "timers_resumed" in await _events(session, ids["case"])


async def test_resume_is_a_no_op_when_nothing_is_paused(
    session: AsyncSession,
) -> None:
    ids = await _case(session)
    await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(48))
    assert await resume_case(session, uuid.UUID(ids["case"])) == []


async def test_resume_of_an_unknown_case_is_refused(session: AsyncSession) -> None:
    with pytest.raises(CaseNotFoundError):
        await resume_case(session, uuid.uuid4())


async def test_a_paused_case_keeps_its_tracking(session: AsyncSession) -> None:
    """Pause must not look like closure. The case is still open and still
    counted -- it has simply stopped chasing people."""
    ids = await _case(session, encounter_status="deceased")
    await create_timer(session, uuid.UUID(ids["case"]), "result_due", _in(48))
    await pause_case_for_encounter_status(session, uuid.UUID(ids["case"]))

    row = (
        await session.execute(
            text("SELECT state, closed_at FROM pending_cases WHERE id = :i"),
            {"i": ids["case"]},
        )
    ).one()
    assert row.state == "awaiting_result"
    assert row.closed_at is None
