"""Phase 4.5's digest and ADR 0004's weekly roster reminder.

Both are *schedules*, not events, and until something runs them on a clock
they are functions nobody calls. These tests run the SQL functions the
``pg_cron`` jobs call, then drain the intents through the real consumer — so
what is proven is the whole path, not just that the SQL parses.

The digest matters more than it looks. Quiet hours and the rate cap both hold
messages back; if nothing sweeps up afterwards, those two fatigue controls
become two ways of losing a flag. The digest is the safety net under them, and
it reads the **cases** rather than the held-back notifications, so a flag that
was suppressed for any reason still appears.
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
from app.notifications.adapters import NullAdapter
from app.services import notifications as notify
from worker.consumers.notifications import handle_notification

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


@pytest_asyncio.fixture(autouse=True)
async def adapters() -> AsyncIterator[dict[str, NullAdapter]]:
    registry = notify.registry()
    saved = dict(registry._adapters)
    fakes = {ch: NullAdapter(ch) for ch in ("email", "sms", "whatsapp")}
    for adapter in fakes.values():
        registry.register(adapter)
    try:
        yield fakes
    finally:
        registry._adapters = saved


async def _doctor_with_flags(
    session: AsyncSession, *, count: int = 3, severity: str = "follow_up"
) -> dict[str, str]:
    tag = uuid.uuid4().hex[:8]
    ids = {
        "doctor": str(uuid.uuid4()),
        "dept": str(uuid.uuid4()),
        "head": str(uuid.uuid4()),
    }
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role, is_active, email) "
            "VALUES (:i, :c, 'Dr Digest', 'doctor', true, :e)"
        ),
        {"i": ids["doctor"], "c": f"DG{tag}", "e": f"dg{tag}@example.test"},
    )
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role, is_active, email) "
            "VALUES (:i, :c, 'Dr Head', 'unit_head', true, :e)"
        ),
        {"i": ids["head"], "c": f"HD{tag}", "e": f"hd{tag}@example.test"},
    )
    await session.execute(
        text(
            "INSERT INTO departments (id, code, name, unit_head_user_id, active) "
            "VALUES (:i, :c, 'Digest Dept', :h, true)"
        ),
        {"i": ids["dept"], "c": f"DG{tag}", "h": ids["head"]},
    )
    for n in range(count):
        pat, enc, order, case = (str(uuid.uuid4()) for _ in range(4))
        await session.execute(
            text("INSERT INTO patients (id, mrn, name) VALUES (:i, :m, :n)"),
            {"i": pat, "m": f"MRN{tag}{n}", "n": f"Patient {n}"},
        )
        await session.execute(
            text(
                "INSERT INTO encounters (id, patient_id, encounter_no, type, "
                "admitted_at, status, department_id) "
                "VALUES (:i, :p, :e, 'ipd', now(), 'discharged', :d)"
            ),
            {"i": enc, "p": pat, "e": f"E{tag}{n}", "d": ids["dept"]},
        )
        await session.execute(
            text(
                "INSERT INTO orders (id, encounter_id, patient_id, test_code, "
                "test_name, category, ordered_at, status) "
                "VALUES (:i, :e, :p, 'T', :tn, 'lab', now(), 'in_lab')"
            ),
            {"i": order, "e": enc, "p": pat, "tn": f"Test {n}"},
        )
        await session.execute(
            text(
                "INSERT INTO pending_cases "
                "(id, order_id, encounter_id, patient_id, current_owner_id, state, "
                " severity, flagged_at) "
                "VALUES (:i, :o, :e, :p, :u, 'flagged', :sev, now())"
            ),
            {
                "i": case,
                "o": order,
                "e": enc,
                "p": pat,
                "u": ids["doctor"],
                "sev": severity,
            },
        )
    await session.commit()
    return ids


# ── 4.5 · the morning digest ──────────────────────────────────────────


async def test_the_digest_job_enqueues_one_intent_per_doctor(
    session: AsyncSession,
) -> None:
    """*"One email per doctor per morning… not one each."*"""
    ids = await _doctor_with_flags(session, count=4)

    queued = (
        await session.execute(text("SELECT rg_enqueue_follow_up_digests() AS n"))
    ).one()
    assert int(queued.n) >= 1

    intents = (
        await session.execute(
            text(
                "SELECT message FROM pgmq.q_notifications "
                " WHERE message->>'user_id' = :u "
                "   AND message->>'template_key' = 'follow_up_digest'"
            ),
            {"u": ids["doctor"]},
        )
    ).all()
    assert len(intents) == 1, "a doctor with four flags got more than one digest"
    assert intents[0].message["case_count"] == 4


async def test_the_digest_lists_every_open_follow_up(
    session: AsyncSession, adapters: dict[str, NullAdapter]
) -> None:
    ids = await _doctor_with_flags(session, count=3)

    await handle_notification(
        session,
        {
            "template_key": "follow_up_digest",
            "audience": "responsible_doctor",
            "user_id": ids["doctor"],
            "digest": True,
        },
    )

    sent = [
        body for _, key, body in adapters["email"].sent if key == "follow_up_digest"
    ]
    assert sent, "no digest was sent"
    assert "3 open follow-up flags" in sent[0]
    for n in range(3):
        assert f"Test {n}" in sent[0]
    # Critical flags are not batched.
    assert "not batched" in sent[0]


async def test_the_digest_excludes_critical_flags(session: AsyncSession) -> None:
    """*"CRITICAL always sends immediately"* — batching one would delay it."""
    ids = await _doctor_with_flags(session, count=2, severity="critical")

    (await session.execute(text("SELECT rg_enqueue_follow_up_digests()"))).one()

    intents = (
        await session.execute(
            text(
                "SELECT count(*) AS n FROM pgmq.q_notifications "
                " WHERE message->>'user_id' = :u "
                "   AND message->>'template_key' = 'follow_up_digest'"
            ),
            {"u": ids["doctor"]},
        )
    ).one()
    assert int(intents.n) == 0


async def test_an_empty_digest_is_not_sent(
    session: AsyncSession, adapters: dict[str, NullAdapter]
) -> None:
    """Everything was acknowledged between the job and the send. An empty
    digest trains people to ignore the next one."""
    ids = await _doctor_with_flags(session, count=1)
    await session.execute(
        text(
            "UPDATE pending_cases SET state = 'acknowledged', acknowledged_at = now() "
            " WHERE current_owner_id = :u"
        ),
        {"u": ids["doctor"]},
    )

    await handle_notification(
        session,
        {
            "template_key": "follow_up_digest",
            "user_id": ids["doctor"],
            "digest": True,
        },
    )
    assert not [k for _, k, _ in adapters["email"].sent if k == "follow_up_digest"]


async def test_a_redelivered_digest_intent_sends_once_a_day(
    session: AsyncSession,
) -> None:
    ids = await _doctor_with_flags(session, count=2)
    intent = {
        "template_key": "follow_up_digest",
        "user_id": ids["doctor"],
        "digest": True,
    }
    for _ in range(3):
        await handle_notification(session, intent)

    row = (
        await session.execute(
            text(
                "SELECT count(*) AS n FROM notifications "
                " WHERE user_id = :u AND template_key = 'follow_up_digest'"
            ),
            {"u": ids["doctor"]},
        )
    ).one()
    assert int(row.n) == 1


async def test_a_digest_is_never_sent_during_quiet_hours(
    session: AsyncSession,
) -> None:
    """A digest that woke somebody at 03:00 would defeat its own purpose."""
    from app.services.notification_policy import evaluate

    at_3am = dt.datetime.now(dt.UTC).replace(hour=21, minute=30)  # 03:00 IST
    verdict = await evaluate(
        session, severity=None, channel="email", user_id=uuid.uuid4(), at=at_3am
    )
    assert verdict.send is False
    assert verdict.suppression_reason == "quiet_hours"


# ── ADR 0004 guard 1 · the weekly roster reminder ─────────────────────


async def test_the_roster_job_reminds_every_unit_head(
    session: AsyncSession,
) -> None:
    """*"Weekly reminder — notification to each unit head to confirm or update
    the coming week's roster."*"""
    ids = await _doctor_with_flags(session, count=1)

    queued = (
        await session.execute(text("SELECT rg_enqueue_roster_reminders() AS n"))
    ).one()
    assert int(queued.n) >= 1

    intent = (
        await session.execute(
            text(
                "SELECT message FROM pgmq.q_notifications "
                " WHERE message->>'user_id' = :u "
                "   AND message->>'template_key' = 'roster_weekly_reminder'"
            ),
            {"u": ids["head"]},
        )
    ).one()
    # No shift was entered, so the reminder says so.
    assert intent.message["has_upcoming_shift"] is False
    assert intent.message["department_id"] == ids["dept"]


async def test_the_reminder_knows_a_roster_is_already_in_place(
    session: AsyncSession,
) -> None:
    """*"Confirming an unchanged roster is one click; it must not require
    re-entry."* The flag is what lets the template say which case it is."""
    ids = await _doctor_with_flags(session, count=1)
    await session.execute(
        text(
            "INSERT INTO duty_roster "
            "(id, user_id, department_id, shift_start, shift_end, role_on_duty) "
            "VALUES (:i, :u, :d, now(), now() + interval '7 days', 'primary')"
        ),
        {"i": str(uuid.uuid4()), "u": ids["doctor"], "d": ids["dept"]},
    )

    (await session.execute(text("SELECT rg_enqueue_roster_reminders()"))).one()

    intent = (
        await session.execute(
            text(
                "SELECT message FROM pgmq.q_notifications "
                " WHERE message->>'user_id' = :u "
                "   AND message->>'template_key' = 'roster_weekly_reminder'"
            ),
            {"u": ids["head"]},
        )
    ).one()
    assert intent.message["has_upcoming_shift"] is True


async def test_the_roster_reminder_renders_and_sends(
    session: AsyncSession, adapters: dict[str, NullAdapter]
) -> None:
    ids = await _doctor_with_flags(session, count=1)

    await handle_notification(
        session,
        {
            "template_key": "roster_weekly_reminder",
            "audience": "unit_head",
            "user_id": ids["head"],
            "department_id": ids["dept"],
            "department_name": "Digest Dept",
            "has_upcoming_shift": False,
        },
    )

    sent = [
        body
        for _, key, body in adapters["email"].sent
        if key == "roster_weekly_reminder"
    ]
    assert sent
    assert "Digest Dept" in sent[0]
    assert "no shift covering the coming week" in sent[0]


# ── both jobs are actually scheduled ──────────────────────────────────


async def test_both_jobs_are_registered_with_pg_cron(
    session: AsyncSession,
) -> None:
    """A function nobody calls is not a schedule."""
    rows = (
        await session.execute(
            text(
                "SELECT jobname, schedule FROM cron.job "
                " WHERE jobname IN ('rg-follow-up-digest', 'rg-roster-weekly-reminder')"
                " ORDER BY jobname"
            )
        )
    ).all()
    jobs = {r.jobname: r.schedule for r in rows}
    assert jobs["rg-follow-up-digest"] == "30 1 * * *"  # 07:00 IST
    assert jobs["rg-roster-weekly-reminder"] == "30 3 * * 5"


async def test_the_cron_jobs_target_this_database(session: AsyncSession) -> None:
    """The Phase 0 lesson, re-checked: a job scheduled into the wrong database
    fires against nothing, silently."""
    rows = (
        await session.execute(
            text(
                "SELECT j.jobname FROM cron.job j "
                " WHERE j.jobname LIKE 'rg-%' "
                "   AND j.database <> current_database()"
            )
        )
    ).all()
    assert not rows, f"cron jobs pointed at another database: {rows}"
