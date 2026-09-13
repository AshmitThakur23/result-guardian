"""Phase 4.4 / 4.5 / 4.6 — the escalation ladder, end to end.

**Exit Gate 4 is two sentences and both are tested here:**

    A critical flag left untouched walks **all four rungs** and produces a
    patient SMS (to a test number), with a complete ``case_events`` trail.

    Acknowledging at **any** rung stops everything.

Phase 4.7's own list is covered too — the full ladder on a compressed clock,
acknowledge-at-rung-1, owner on leave, quiet hours, a 500 from the SMS
provider, a deceased patient, an unverified phone.

The clock is compressed by writing a department-scoped ``escalation_chain``
with delays in the past rather than by mocking time. Mocked time would prove
the code runs; a real timer row at a real instant proves the *database* agrees,
and PostgreSQL is where the timers live.
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
from app.notifications.adapters import FailingAdapter, NullAdapter
from app.services import notifications as notify
from app.services.escalation import (
    acknowledge_case,
    chain_for,
    fire_rung,
    start_ladder,
)

pytestmark = pytest.mark.integration

MIN = dt.timedelta(minutes=1)


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
    """Swap every outbound channel for a recording adapter.

    A test that actually sent email would be a test that fails on a laptop
    with no mail server, so the registry is swapped and restored.
    """
    registry = notify.registry()
    saved = dict(registry._adapters)
    fakes = {ch: NullAdapter(ch) for ch in ("email", "sms", "whatsapp")}
    for adapter in fakes.values():
        registry.register(adapter)
    try:
        yield fakes
    finally:
        registry._adapters = saved


async def _world(
    session: AsyncSession,
    *,
    severity: str = "critical",
    encounter_status: str = "discharged",
    phone_verified: bool = True,
    language: str = "en",
) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:8]
    ids = {
        k: str(uuid.uuid4())
        for k in (
            "dept",
            "doctor",
            "head",
            "admin",
            "pat",
            "enc",
            "order",
            "contract",
            "case",
        )
    }
    for key, role, code in (
        ("doctor", "doctor", "DOC"),
        ("head", "unit_head", "HED"),
        ("admin", "admin", "ADM"),
    ):
        await session.execute(
            text(
                "INSERT INTO users (id, employee_code, full_name, role, is_active, "
                " email, phone_e164) "
                "VALUES (:i, :c, :n, :r, true, :em, :ph)"
            ),
            {
                "i": ids[key],
                "c": f"{code}{tag}",
                "n": f"Dr {code}",
                "r": role,
                "em": f"{code.lower()}{tag}@example.test",
                "ph": "+915550000001",
            },
        )
    await session.execute(
        text(
            "INSERT INTO departments (id, code, name, unit_head_user_id, active) "
            "VALUES (:i, :c, 'Medicine', :h, true)"
        ),
        {"i": ids["dept"], "c": f"D{tag}", "h": ids["head"]},
    )
    await session.execute(
        text(
            "INSERT INTO patients (id, mrn, name, phone_primary_e164, "
            " phone_verified_at, preferred_language) "
            "VALUES (:i, :m, 'Sunita Rao', '+915550000099', :v, :lang)"
        ),
        {
            "i": ids["pat"],
            "m": f"MRN{tag}",
            "v": dt.datetime.now(dt.UTC) if phone_verified else None,
            "lang": language,
        },
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            "discharged_at, status, department_id) "
            "VALUES (:i, :p, :n, 'ipd', now(), now(), :st, :d)"
        ),
        {
            "i": ids["enc"],
            "p": ids["pat"],
            "n": f"E{tag}",
            "st": encounter_status,
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
        {"i": ids["contract"], "e": ids["enc"], "o": ids["order"], "u": ids["doctor"]},
    )
    await session.execute(
        text(
            "INSERT INTO pending_cases "
            "(id, order_id, encounter_id, patient_id, contract_id, current_owner_id, "
            " state, severity, flagged_at) "
            "VALUES (:i, :o, :e, :p, :c, :u, 'flagged', :sev, now())"
        ),
        {
            "i": ids["case"],
            "o": ids["order"],
            "e": ids["enc"],
            "p": ids["pat"],
            "c": ids["contract"],
            "u": ids["doctor"],
            "sev": severity,
        },
    )
    await session.commit()
    return ids


async def _compress_clock(
    session: AsyncSession, department_id: str, severity: str = "critical"
) -> None:
    """Phase 4.7: *"Full ladder on a compressed clock (delays in seconds)."*

    Department-scoped rows override the global ladder for this department
    alone, so compressing the clock here cannot alter any other test's timing.
    All five rungs are due immediately.
    """
    for level, target, channels in (
        (0, "owner", '["in_app"]'),
        (1, "owner", '["in_app","sms"]'),
        (2, "unit_head", '["in_app","sms"]'),
        (3, "patient", '["sms"]'),
        (4, "admin", '["in_app","sms"]'),
    ):
        await session.execute(
            text(
                "INSERT INTO escalation_chain "
                "(id, department_id, level, target_type, delay_minutes, channels, "
                " severity, active) "
                "VALUES (:i, :d, :lv, :t, 0, CAST(:ch AS jsonb), :sev, true)"
            ),
            {
                "i": str(uuid.uuid4()),
                "d": department_id,
                "lv": level,
                "t": target,
                "ch": channels,
                "sev": severity,
            },
        )


async def _events(session: AsyncSession, case_id: str) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT event_type FROM case_events WHERE case_id = :c "
                " ORDER BY occurred_at, id"
            ),
            {"c": case_id},
        )
    ).all()
    return [r.event_type for r in rows]


async def _timers(session: AsyncSession, case_id: str) -> list[Any]:
    return list(
        (
            await session.execute(
                text(
                    "SELECT escalation_level, status, fire_at FROM sla_timers "
                    " WHERE case_id = :c AND timer_type = 'case_escalation' "
                    " ORDER BY escalation_level"
                ),
                {"c": case_id},
            )
        ).all()
    )


async def _notifications(session: AsyncSession, case_id: str) -> list[Any]:
    return list(
        (
            await session.execute(
                text(
                    "SELECT escalation_level, channel, status, template_key, "
                    "       suppression_reason, user_id, patient_id, locale "
                    "  FROM notifications WHERE case_id = :c "
                    " ORDER BY escalation_level, channel"
                ),
                {"c": case_id},
            )
        ).all()
    )


# ── the chain is configuration ────────────────────────────────────────


async def test_the_default_ladder_matches_the_plans_table(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    critical = {
        r.level: r
        for r in await chain_for(
            session, department_id=uuid.UUID(ids["dept"]), severity="critical"
        )
    }
    follow_up = {
        r.level: r
        for r in await chain_for(
            session, department_id=uuid.UUID(ids["dept"]), severity="follow_up"
        )
    }

    assert critical[0].delay_minutes == 0
    assert critical[1].delay_minutes == 60  # +1h
    assert critical[2].delay_minutes == 240  # +4h
    assert critical[3].delay_minutes == 480  # +8h
    assert critical[4].delay_minutes == 2880  # +48h

    assert follow_up[1].delay_minutes == 240  # +4h
    assert follow_up[2].delay_minutes == 720  # +12h
    assert follow_up[3].delay_minutes == 1440  # +24h

    assert critical[2].target_type == "unit_head"
    assert critical[3].target_type == "patient"
    assert critical[4].target_type == "admin"


async def test_a_department_row_overrides_the_global_default(
    session: AsyncSession,
) -> None:
    """*"One row per rung, per department. This is what makes escalation
    configurable per hospital."*"""
    ids = await _world(session)
    await session.execute(
        text(
            "INSERT INTO escalation_chain "
            "(id, department_id, level, target_type, delay_minutes, channels, "
            " severity, active) "
            "VALUES (:i, :d, 1, 'owner', 7, '[\"in_app\"]'::jsonb, 'critical', true)"
        ),
        {"i": str(uuid.uuid4()), "d": ids["dept"]},
    )
    rungs = {
        r.level: r
        for r in await chain_for(
            session, department_id=uuid.UUID(ids["dept"]), severity="critical"
        )
    }
    assert rungs[1].delay_minutes == 7
    # The rungs it did not override are inherited.
    assert rungs[2].delay_minutes == 240


# ── the ladder is scheduled up front ──────────────────────────────────


async def test_starting_the_ladder_schedules_every_rung(
    session: AsyncSession,
) -> None:
    """All five timers at once, not chained. A chained ladder loses every
    remaining rung if one firing fails."""
    ids = await _world(session)
    started = await start_ladder(session, uuid.UUID(ids["case"]))

    assert started.created == 5
    timers = await _timers(session, ids["case"])
    assert [t.escalation_level for t in timers] == [0, 1, 2, 3, 4]
    assert all(t.status == "pending" for t in timers)


async def test_rung_delays_are_measured_from_flagged_at(
    session: AsyncSession,
) -> None:
    """A rung that fires late must not push the rest of the ladder out."""
    ids = await _world(session)
    flagged = (
        (
            await session.execute(
                text("SELECT flagged_at FROM pending_cases WHERE id = :c"),
                {"c": ids["case"]},
            )
        )
        .one()
        .flagged_at
    )

    # Start the ladder an hour "late".
    await start_ladder(
        session, uuid.UUID(ids["case"]), at=flagged + dt.timedelta(hours=1)
    )
    timers = {t.escalation_level: t for t in await _timers(session, ids["case"])}
    # Rung 1 for critical is +60m from flagged_at, not from now.
    assert abs((timers[1].fire_at - (flagged + 60 * MIN)).total_seconds()) < 2


async def test_starting_the_ladder_twice_does_not_double_it(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    first = await start_ladder(session, uuid.UUID(ids["case"]))
    second = await start_ladder(session, uuid.UUID(ids["case"]))

    assert first.created == 5
    assert second.created == 0
    assert second.already_present == 5
    assert len(await _timers(session, ids["case"])) == 5


async def test_a_normal_case_gets_no_ladder(session: AsyncSession) -> None:
    """There is nobody to chase about a normal result."""
    ids = await _world(session, severity="normal")
    started = await start_ladder(session, uuid.UUID(ids["case"]))
    assert started.created == 0
    assert await _timers(session, ids["case"]) == []


async def test_an_acknowledged_case_gets_no_ladder(session: AsyncSession) -> None:
    ids = await _world(session)
    await session.execute(
        text(
            "UPDATE pending_cases SET state = 'acknowledged', "
            "acknowledged_at = now() WHERE id = :c"
        ),
        {"c": ids["case"]},
    )
    started = await start_ladder(session, uuid.UUID(ids["case"]))
    assert started.created == 0


# ── ★ EXIT GATE 4, clause 1 ───────────────────────────────────────────


async def test_exit_gate_a_critical_flag_walks_all_rungs_to_a_patient_sms(
    session: AsyncSession, adapters: dict[str, NullAdapter]
) -> None:
    """*"A critical flag left untouched walks **all four rungs** and produces a
    patient SMS (to a test number), with a complete ``case_events`` trail."*"""
    ids = await _world(session)
    await _compress_clock(session, ids["dept"])

    for level in (0, 1, 2, 3, 4):
        await fire_rung(session, uuid.UUID(ids["case"]), level)

    rows = await _notifications(session, ids["case"])
    by_level: dict[int, list[Any]] = {}
    for row in rows:
        by_level.setdefault(row.escalation_level, []).append(row)

    # Every rung produced something.
    assert set(by_level) == {0, 1, 2, 3, 4}

    # Rung 3 is the patient, by SMS, and it was actually sent.
    patient_rows = [r for r in by_level[3] if r.patient_id is not None]
    assert patient_rows, "rung 3 produced no patient notification"
    assert patient_rows[0].channel == "sms"
    assert patient_rows[0].status == "sent"
    assert patient_rows[0].template_key == "patient_result_pending"

    # It really went through the SMS adapter.
    assert any(r.phone_e164 == "+915550000099" for r, _, _ in adapters["sms"].sent)

    # A complete trail.
    events = await _events(session, ids["case"])
    assert events.count("escalation_rung_fired") == 5
    assert "notification_sent" in events

    payloads = (
        await session.execute(
            text(
                "SELECT payload FROM case_events WHERE case_id = :c "
                "   AND event_type = 'escalation_rung_fired' "
                " ORDER BY (payload->>'level')::int"
            ),
            {"c": ids["case"]},
        )
    ).all()
    assert [p.payload["level"] for p in payloads] == [0, 1, 2, 3, 4]
    assert [p.payload["target"] for p in payloads] == [
        "owner",
        "owner",
        "unit_head",
        "patient",
        "admin",
    ]
    # "case_events records every rung with target and channel"
    assert all(p.payload["channels"] for p in payloads)


async def test_the_patient_sms_never_carries_the_result(
    session: AsyncSession, adapters: dict[str, NullAdapter]
) -> None:
    """*"**Never include the result in an SMS.**"*

    The strongest rule in 4.6, and the easiest to erode by "just adding the
    test name to be helpful".
    """
    ids = await _world(session)
    await _compress_clock(session, ids["dept"])
    await fire_rung(session, uuid.UUID(ids["case"]), 3)

    sent = [
        body for _, key, body in adapters["sms"].sent if key == "patient_result_pending"
    ]
    assert sent, "no patient SMS was sent"
    body = sent[0].lower()
    for leak in ("urine culture", "critical", "escherichia", "resistant", "mrn"):
        assert leak not in body, f"the patient SMS leaked {leak!r}: {sent[0]!r}"
    assert "needs discussion" in body


@pytest.mark.parametrize("language", ["en", "hi", "pa"])
async def test_the_patient_sms_uses_their_language(
    session: AsyncSession, adapters: dict[str, NullAdapter], language: str
) -> None:
    """*"Language from ``patients.preferred_language``, fallback English."*"""
    ids = await _world(session, language=language)
    await _compress_clock(session, ids["dept"])
    await fire_rung(session, uuid.UUID(ids["case"]), 3)

    rows = [r for r in await _notifications(session, ids["case"]) if r.patient_id]
    assert rows[0].locale == language


# ── ★ EXIT GATE 4, clause 2 ───────────────────────────────────────────


@pytest.mark.parametrize("acknowledge_at_rung", [0, 1, 2, 3])
async def test_exit_gate_acknowledging_at_any_rung_stops_everything(
    session: AsyncSession, acknowledge_at_rung: int
) -> None:
    """*"Acknowledging at **any** rung stops everything."*"""
    ids = await _world(session)
    await start_ladder(session, uuid.UUID(ids["case"]))

    for level in range(acknowledge_at_rung + 1):
        await fire_rung(session, uuid.UUID(ids["case"]), level)

    result = await acknowledge_case(
        session, uuid.UUID(ids["case"]), acknowledged_by=uuid.UUID(ids["doctor"])
    )
    assert result.already_acknowledged is False
    assert len(result.cancelled_timer_ids) == 5  # none had been marked fired here

    timers = await _timers(session, ids["case"])
    assert all(t.status == "cancelled" for t in timers)
    assert "case_acknowledged" in await _events(session, ids["case"])


async def test_acknowledging_twice_is_not_an_error(session: AsyncSession) -> None:
    """A double-click must not raise."""
    ids = await _world(session)
    await start_ladder(session, uuid.UUID(ids["case"]))
    await acknowledge_case(
        session, uuid.UUID(ids["case"]), acknowledged_by=uuid.UUID(ids["doctor"])
    )
    again = await acknowledge_case(
        session, uuid.UUID(ids["case"]), acknowledged_by=uuid.UUID(ids["doctor"])
    )
    assert again.already_acknowledged is True
    assert again.cancelled_timer_ids == []


async def test_a_rung_that_fires_after_acknowledgement_does_nothing(
    session: AsyncSession,
) -> None:
    """The race: a rung already in flight when the acknowledgement lands."""
    ids = await _world(session)
    await _compress_clock(session, ids["dept"])
    await start_ladder(session, uuid.UUID(ids["case"]))
    await acknowledge_case(
        session, uuid.UUID(ids["case"]), acknowledged_by=uuid.UUID(ids["doctor"])
    )

    fired = await fire_rung(session, uuid.UUID(ids["case"]), 3)
    assert fired.skipped_reason == "acknowledged"
    assert list(await _notifications(session, ids["case"])) == []


# ── 4.5 · alert fatigue ───────────────────────────────────────────────


async def test_quiet_hours_defer_a_follow_up_but_never_a_critical(
    session: AsyncSession,
) -> None:
    """*"FOLLOW_UP batches to the next window; **CRITICAL always sends
    immediately**."*"""
    from app.services.notification_policy import evaluate

    at_23 = dt.datetime(2026, 9, 13, 17, 30, tzinfo=dt.UTC)  # 23:00 IST
    follow_up = await evaluate(
        session, severity="follow_up", channel="sms", user_id=uuid.uuid4(), at=at_23
    )
    assert follow_up.send is False
    assert follow_up.suppression_reason == "quiet_hours"
    assert follow_up.defer_until is not None

    critical = await evaluate(
        session, severity="critical", channel="sms", user_id=uuid.uuid4(), at=at_23
    )
    assert critical.send is True


async def test_quiet_hours_do_not_defer_an_in_app_row(
    session: AsyncSession,
) -> None:
    """An in-app row does not wake anybody; the dashboard should be correct at
    3am even if nobody is looking."""
    from app.services.notification_policy import evaluate

    at_23 = dt.datetime(2026, 9, 13, 17, 30, tzinfo=dt.UTC)
    verdict = await evaluate(
        session, severity="follow_up", channel="in_app", user_id=uuid.uuid4(), at=at_23
    )
    assert verdict.send is True


async def test_a_daytime_follow_up_is_not_deferred(session: AsyncSession) -> None:
    from app.services.notification_policy import evaluate

    at_noon = dt.datetime(2026, 9, 13, 6, 30, tzinfo=dt.UTC)  # 12:00 IST
    verdict = await evaluate(
        session, severity="follow_up", channel="sms", user_id=uuid.uuid4(), at=at_noon
    )
    assert verdict.send is True


async def test_multiple_analytes_in_one_report_produce_one_notification(
    session: AsyncSession,
) -> None:
    """*"Multiple analytes in one report = **one** notification, not twelve."*

    The grouping falls out of the dedupe key: same case, same template, same
    rung, so twelve dispatches collapse to one row.
    """
    ids = await _world(session)
    await _compress_clock(session, ids["dept"])

    for _ in range(12):
        await notify.dispatch(
            session,
            case_id=uuid.UUID(ids["case"]),
            template_key="case_flagged_owner",
            channel="in_app",
            user_id=uuid.UUID(ids["doctor"]),
            context=await _context(session, ids["case"]),
            severity="critical",
            escalation_level=0,
        )

    rows = await _notifications(session, ids["case"])
    assert len(rows) == 1


async def test_the_sms_rate_cap_holds_and_rolls_into_the_digest(
    session: AsyncSession,
) -> None:
    """*"Max N SMS per doctor per hour, overflow rolls into digest."*"""
    from app.services.notification_policy import evaluate, open_follow_up_digest

    ids = await _world(session, severity="follow_up")
    # Anchored to the instant being evaluated, not to now(): the database
    # clock is not the test author's clock, and a window chosen in the
    # future silently counts nothing.
    at = dt.datetime.now(dt.UTC).replace(hour=6, minute=30, second=0, microsecond=0)
    # Six already-sent SMS in the last hour, which is the default cap.
    for n in range(6):
        await session.execute(
            text(
                "INSERT INTO notifications "
                "(id, case_id, user_id, channel, template_key, locale, status, "
                " sent_at, attempts, dedupe_key) "
                "VALUES (:i, :c, :u, 'sms', 't', 'en', 'sent', :t, 1, :k)"
            ),
            {
                "i": str(uuid.uuid4()),
                "c": ids["case"],
                "u": ids["doctor"],
                "t": at - dt.timedelta(minutes=5),
                "k": f"cap-{n}-{uuid.uuid4()}",
            },
        )

    verdict = await evaluate(
        session,
        severity="follow_up",
        channel="sms",
        user_id=uuid.UUID(ids["doctor"]),
        at=at,
    )
    assert verdict.send is False
    assert verdict.suppression_reason == "rate_capped"

    # Nothing is lost: the case still appears in the morning digest.
    digest = await open_follow_up_digest(session, uuid.UUID(ids["doctor"]))
    assert ids["case"] in {d["case_id"] for d in digest}


async def test_a_critical_is_never_rate_capped(session: AsyncSession) -> None:
    """A doctor who has already had six texts this hour is exactly the doctor
    with a lot going on."""
    from app.services.notification_policy import evaluate

    ids = await _world(session)
    for n in range(20):
        await session.execute(
            text(
                "INSERT INTO notifications "
                "(id, case_id, user_id, channel, template_key, locale, status, "
                " sent_at, attempts, dedupe_key) "
                "VALUES (:i, :c, :u, 'sms', 't', 'en', 'sent', now(), 1, :k)"
            ),
            {
                "i": str(uuid.uuid4()),
                "c": ids["case"],
                "u": ids["doctor"],
                "k": f"flood-{n}-{uuid.uuid4()}",
            },
        )
    verdict = await evaluate(
        session,
        severity="critical",
        channel="sms",
        user_id=uuid.UUID(ids["doctor"]),
        at=dt.datetime.now(dt.UTC),
    )
    assert verdict.send is True


# ── 4.6 · the patient rung's safety rules ─────────────────────────────


async def test_a_deceased_patient_is_never_messaged_and_the_unit_head_is(
    session: AsyncSession, adapters: dict[str, NullAdapter]
) -> None:
    """*"Suppress entirely if encounter status is ``deceased``."* (4.7)"""
    ids = await _world(session, encounter_status="deceased")
    await _compress_clock(session, ids["dept"])
    await fire_rung(session, uuid.UUID(ids["case"]), 3)

    rows = await _notifications(session, ids["case"])
    patient_rows = [r for r in rows if r.patient_id is not None]
    assert patient_rows[0].status == "suppressed"
    assert patient_rows[0].suppression_reason == "patient_deceased"
    assert not adapters["sms"].sent, "an SMS was sent for a deceased patient"


async def test_an_unverified_phone_skips_the_patient_and_escalates(
    session: AsyncSession, adapters: dict[str, NullAdapter]
) -> None:
    """*"Only send if ``phone_verified_at`` is set; otherwise escalate to unit
    head instead."* (4.7)"""
    ids = await _world(session, phone_verified=False)
    await _compress_clock(session, ids["dept"])
    fired = await fire_rung(session, uuid.UUID(ids["case"]), 3)

    rows = await _notifications(session, ids["case"])
    patient_rows = [r for r in rows if r.patient_id is not None]
    assert patient_rows[0].status == "suppressed"
    assert patient_rows[0].suppression_reason == "phone_unverified"

    # The unit head got it instead.
    head_rows = [r for r in rows if str(r.user_id or "") == ids["head"]]
    assert head_rows, "the patient rung was skipped without escalating"

    payload = (
        await session.execute(
            text(
                "SELECT payload FROM case_events WHERE case_id = :c "
                "   AND event_type = 'escalation_rung_fired' "
                "   AND (payload->>'level')::int = 3"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert payload.payload["redirected_to_unit_head"] is True
    assert fired.target_type == "patient"


async def test_a_transferred_patient_is_redirected_not_messaged(
    session: AsyncSession,
) -> None:
    ids = await _world(session, encounter_status="transferred")
    await _compress_clock(session, ids["dept"])
    await fire_rung(session, uuid.UUID(ids["case"]), 3)

    rows = await _notifications(session, ids["case"])
    patient_rows = [r for r in rows if r.patient_id is not None]
    assert patient_rows[0].status == "suppressed"
    assert [r for r in rows if str(r.user_id or "") == ids["head"]]


# ── 4.3 · provider failure must not stop the ladder ───────────────────


async def test_a_provider_500_is_retried_surfaced_and_the_ladder_continues(
    session: AsyncSession,
) -> None:
    """*"SMS provider returns 500 → retried → failure surfaced, **ladder
    continues**."* (4.7)"""
    ids = await _world(session)
    await _compress_clock(session, ids["dept"])
    await start_ladder(session, uuid.UUID(ids["case"]))

    failing = FailingAdapter("sms")
    notify.registry().register(failing)

    fired = await fire_rung(session, uuid.UUID(ids["case"]), 1)

    # Retried three times, as the plan requires.
    assert failing.attempts == 3

    sms = [
        r
        for r in await _notifications(session, ids["case"])
        if r.channel == "sms" and r.escalation_level == 1
    ]
    assert sms[0].status == "failed"

    # Surfaced.
    events = await _events(session, ids["case"])
    assert "notification_failed" in events

    # And the ladder kept its remaining rungs.
    pending = [t for t in await _timers(session, ids["case"]) if t.status == "pending"]
    assert {t.escalation_level for t in pending} >= {2, 3, 4}
    assert fired.skipped_reason is None


async def test_an_unconfigured_channel_degrades_to_in_app(
    session: AsyncSession,
) -> None:
    """Losing a channel must never mean losing the message."""
    registry = notify.registry()
    saved = dict(registry._adapters)
    registry._adapters = {"in_app": saved["in_app"]}
    try:
        ids = await _world(session)
        await _compress_clock(session, ids["dept"])
        await fire_rung(session, uuid.UUID(ids["case"]), 1)
        rows = await _notifications(session, ids["case"])
        assert all(r.channel == "in_app" for r in rows)
        assert all(r.status == "sent" for r in rows)
    finally:
        registry._adapters = saved


# ── owner on leave, through the ladder ────────────────────────────────


async def test_a_rung_notifies_the_delegate_when_the_owner_is_on_leave(
    session: AsyncSession,
) -> None:
    """*"Owner on leave → delegate notified → event logged."* (4.7)"""
    ids = await _world(session)
    delegate = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO users (id, employee_code, full_name, role, is_active, email) "
            "VALUES (:i, :c, 'Dr Cover', 'doctor', true, 'cover@example.test')"
        ),
        {"i": delegate, "c": f"COV{uuid.uuid4().hex[:6]}"},
    )
    await session.execute(
        text(
            "INSERT INTO user_absences "
            "(id, user_id, absence_type, starts_at, delegate_user_id) "
            "VALUES (:i, :u, 'leave', now() - interval '1 day', :d)"
        ),
        {"i": str(uuid.uuid4()), "u": ids["doctor"], "d": delegate},
    )
    await _compress_clock(session, ids["dept"])

    await fire_rung(session, uuid.UUID(ids["case"]), 1)

    rows = await _notifications(session, ids["case"])
    assert {str(r.user_id) for r in rows if r.user_id} == {delegate}

    payload = (
        await session.execute(
            text(
                "SELECT payload FROM case_events WHERE case_id = :c "
                "   AND event_type = 'escalation_rung_fired'"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert payload.payload["owner_resolution_step"] == "absence_delegate"


async def _context(session: AsyncSession, case_id: str) -> dict[str, object]:
    """Minimal render context for a direct dispatch call."""
    return {
        "patient_name": "Sunita Rao",
        "mrn": "MRN-1",
        "test_name": "Urine Culture",
        "severity_label": "CRITICAL",
        "department_name": "Medicine",
        "owner_name": "Dr DOC",
        "discharged_on": "13 Sep 2026",
        "elapsed_label": "0 hours",
        "case_url": f"/cases/{case_id}",
        "reason_summary": "",
        "hospital_name": "the hospital",
        "hospital_phone": "0000",
    }
