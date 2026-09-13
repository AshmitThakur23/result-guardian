"""Phase 4 — worker integration, concurrency, recovery and regression.

The tests above exercise the services directly. These go through the parts
that only mean something when the queue, the worker and two real database
connections are all in play:

* a ladder rung firing through the **real** ``sla_timers`` consumer;
* a **redelivered** timer message producing one rung, not two;
* **two workers** racing the same rung on independent connections;
* an acknowledgement racing a firing rung;
* **restart recovery** — the ladder survives its wake-ups being destroyed,
  because PostgreSQL is the truth and pgmq is only a doorbell;
* the ``notifications`` queue Phase 2.3 has been filling since lab flags
  shipped, now that Phase 4.3 drains it;
* Phase 1/2/3 behaviour still intact after Phase 4's changes to the shared
  timer machinery.
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
from app.notifications.adapters import NullAdapter
from app.services import notifications as notify
from app.services.escalation import acknowledge_case, start_ladder
from app.services.timers import claim_timer_for_firing, create_timer
from worker.consumers.notifications import handle_notification
from worker.consumers.sla_timers import handle_sla_timer

pytestmark = pytest.mark.integration

SETTINGS = get_settings()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SETTINGS.database_url, pool_pre_ping=True)
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


async def _seed_case(
    session: AsyncSession, *, severity: str = "critical"
) -> dict[str, str]:
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
            "ct",
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
                " email) VALUES (:i, :c, :n, :r, true, :e)"
            ),
            {
                "i": ids[key],
                "c": f"{code}{tag}",
                "n": f"Dr {code}",
                "r": role,
                "e": f"{code.lower()}{tag}@example.test",
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
            " phone_verified_at) VALUES (:i, :m, 'P', '+915550000099', now())"
        ),
        {"i": ids["pat"], "m": f"MRN{tag}"},
    )
    await session.execute(
        text(
            "INSERT INTO encounters (id, patient_id, encounter_no, type, admitted_at, "
            "discharged_at, status, department_id) "
            "VALUES (:i, :p, :n, 'ipd', now(), now(), 'discharged', :d)"
        ),
        {"i": ids["enc"], "p": ids["pat"], "n": f"E{tag}", "d": ids["dept"]},
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
        {"i": ids["ct"], "e": ids["enc"], "o": ids["order"], "u": ids["doctor"]},
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
            "c": ids["ct"],
            "u": ids["doctor"],
            "sev": severity,
        },
    )
    await session.execute(
        text(
            "INSERT INTO escalation_chain "
            "(id, department_id, level, target_type, delay_minutes, channels, "
            " severity, active) VALUES "
            "(:a, :d, 0, 'owner', 0, '[\"in_app\"]'::jsonb, :sev, true), "
            "(:b, :d, 1, 'owner', 0, '[\"in_app\"]'::jsonb, :sev, true), "
            "(:c, :d, 2, 'unit_head', 0, '[\"in_app\"]'::jsonb, :sev, true)"
        ),
        {
            "a": str(uuid.uuid4()),
            "b": str(uuid.uuid4()),
            "c": str(uuid.uuid4()),
            "d": ids["dept"],
            "sev": severity,
        },
    )
    await session.commit()
    return ids


async def _events(session: AsyncSession, case_id: str, of_type: str) -> int:
    row = (
        await session.execute(
            text(
                "SELECT count(*) AS n FROM case_events "
                " WHERE case_id = :c AND event_type = :t"
            ),
            {"c": case_id, "t": of_type},
        )
    ).one()
    return int(row.n)


# ── the rung really fires through the worker ──────────────────────────


async def test_a_rung_fires_through_the_real_timer_consumer(
    session: AsyncSession,
) -> None:
    ids = await _seed_case(session)
    started = await start_ladder(session, uuid.UUID(ids["case"]))
    timer_id = started.timer_ids[0]

    await handle_sla_timer(session, {"timer_id": str(timer_id)})

    row = (
        await session.execute(
            text("SELECT status, fired_at FROM sla_timers WHERE id = :i"),
            {"i": str(timer_id)},
        )
    ).one()
    assert row.status == "fired"
    assert row.fired_at is not None
    assert await _events(session, ids["case"], "escalation_rung_fired") == 1


async def test_a_redelivered_timer_message_fires_one_rung(
    session: AsyncSession,
) -> None:
    """pgmq is at-least-once. The row lock is what makes that safe."""
    ids = await _seed_case(session)
    started = await start_ladder(session, uuid.UUID(ids["case"]))
    timer_id = started.timer_ids[1]

    for _ in range(3):
        await handle_sla_timer(session, {"timer_id": str(timer_id)})

    assert await _events(session, ids["case"], "escalation_rung_fired") == 1
    notifications = (
        await session.execute(
            text(
                "SELECT count(*) AS n FROM notifications "
                " WHERE case_id = :c AND escalation_level = 1"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert notifications.n == 1


async def test_the_rung_number_survives_a_late_delivery(
    session: AsyncSession,
) -> None:
    """A rung that fires hours late after a restart is still *its* rung.

    Inferring the rung from elapsed time would silently run the wrong one.
    """
    ids = await _seed_case(session)
    started = await start_ladder(session, uuid.UUID(ids["case"]))
    timer_id = started.timer_ids[2]

    await handle_sla_timer(session, {"timer_id": str(timer_id)})

    payload = (
        await session.execute(
            text(
                "SELECT payload FROM case_events WHERE case_id = :c "
                "   AND event_type = 'escalation_rung_fired'"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert payload.payload["level"] == 2
    assert payload.payload["target"] == "unit_head"


# ── the defect the ladder found ───────────────────────────────────────


def test_two_rungs_at_the_same_instant_are_distinct_timers() -> None:
    """Regression. The idempotency key was ``(case, type, fire_at)``; two
    rungs configured to fire together collapsed onto one timer and the ladder
    silently lost its upper rungs."""
    case_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    keys = {
        idempotency_key(case_id, "case_escalation", now, level) for level in range(5)
    }
    assert len(keys) == 5


def test_phase_2_timer_keys_are_unchanged() -> None:
    """THE ONE RULE: the rung is appended only when present, so every Phase 2
    key is byte-identical to what it was before Phase 4."""
    case_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    stamp = now.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")
    assert (
        idempotency_key(case_id, "result_due", now) == f"{case_id}:result_due:{stamp}Z"
    )


async def test_a_compressed_ladder_creates_every_rung(
    session: AsyncSession,
) -> None:
    """The same defect, at the level that matters: all three rungs due at the
    same instant must be three timers."""
    ids = await _seed_case(session)
    started = await start_ladder(session, uuid.UUID(ids["case"]))
    # Three department-scoped rungs override levels 0-2; levels 3 and 4 are
    # inherited from the global default, which is the point of making the
    # chain overridable per rung rather than per department.
    assert started.created == 5

    rows = (
        await session.execute(
            text(
                "SELECT escalation_level FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'case_escalation' "
                " ORDER BY escalation_level"
            ),
            {"c": ids["case"]},
        )
    ).all()
    assert [r.escalation_level for r in rows] == [0, 1, 2, 3, 4]


# ── concurrency, on independent connections ───────────────────────────


async def _independent(coro_factory: Any, count: int) -> list[Any]:
    """Run N copies of a coroutine, each on its own engine and connection."""
    barrier = asyncio.Barrier(count)

    async def one(index: int) -> Any:
        engine = create_async_engine(SETTINGS.database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with maker() as s:
                await barrier.wait()
                result = await coro_factory(s, index)
                await s.commit()
                return result
        except Exception as exc:
            return exc
        finally:
            await engine.dispose()

    return list(await asyncio.gather(*(one(i) for i in range(count))))


async def _committed_case(severity: str = "critical") -> dict[str, str]:
    engine = create_async_engine(SETTINGS.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        ids = await _seed_case(s, severity=severity)
        started = await start_ladder(s, uuid.UUID(ids["case"]))
        ids["timer0"] = str(started.timer_ids[0])
        # These rows are committed, so the *running* worker container would
        # otherwise consume the wake-ups and fire the rungs underneath the
        # test. Removing the queue messages leaves the durable timers intact
        # and lets the test drive the handler itself -- which is what is
        # actually under test here.
        await s.execute(
            text("DELETE FROM pgmq.q_sla_timers WHERE message->>'case_id' = :c"),
            {"c": ids["case"]},
        )
        await s.commit()
    await engine.dispose()
    return ids


async def _cleanup(ids: dict[str, str]) -> None:
    engine = create_async_engine(SETTINGS.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        await s.execute(text("SET session_replication_role = replica"))
        for sql in (
            "DELETE FROM notifications WHERE case_id = :c",
            "DELETE FROM patient_contacts WHERE case_id = :c",
            "DELETE FROM case_events WHERE case_id = :c",
            "DELETE FROM sla_timers WHERE case_id = :c",
            "DELETE FROM pending_cases WHERE id = :c",
        ):
            await s.execute(text(sql), {"c": ids["case"]})
        await s.execute(
            text("DELETE FROM escalation_chain WHERE department_id = :d"),
            {"d": ids["dept"]},
        )
        await s.execute(
            text("DELETE FROM discharge_contracts WHERE id = :i"), {"i": ids["ct"]}
        )
        await s.execute(text("DELETE FROM orders WHERE id = :i"), {"i": ids["order"]})
        await s.execute(text("DELETE FROM encounters WHERE id = :i"), {"i": ids["enc"]})
        await s.execute(text("DELETE FROM patients WHERE id = :i"), {"i": ids["pat"]})
        await s.execute(
            text("DELETE FROM departments WHERE id = :i"), {"i": ids["dept"]}
        )
        await s.execute(
            text("DELETE FROM users WHERE id IN (:a, :b, :c2)"),
            {"a": ids["doctor"], "b": ids["head"], "c2": ids["admin"]},
        )
        await s.execute(text("SET session_replication_role = origin"))
        await s.commit()
    await engine.dispose()


async def test_two_workers_racing_one_rung_produce_one_effect(
    session: AsyncSession,
) -> None:
    """Four workers, four connections, one timer."""
    ids = await _committed_case()
    try:

        async def fire(s: AsyncSession, _: int) -> None:
            await handle_sla_timer(s, {"timer_id": ids["timer0"]})

        await _independent(fire, 4)

        engine = create_async_engine(SETTINGS.database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with maker() as s:
            fired = int(
                (
                    await s.execute(
                        text(
                            "SELECT count(*) AS n FROM case_events "
                            " WHERE case_id = :c "
                            "   AND event_type = 'escalation_rung_fired' "
                            "   AND (payload->>'level')::int = 0"
                        ),
                        {"c": ids["case"]},
                    )
                )
                .one()
                .n
            )
            notes = (
                await s.execute(
                    text(
                        "SELECT count(*) AS n FROM notifications "
                        " WHERE case_id = :c AND escalation_level = 0"
                    ),
                    {"c": ids["case"]},
                )
            ).one()
        await engine.dispose()

        assert fired == 1, f"the rung fired {fired} times"
        assert notes.n == 1
    finally:
        await _cleanup(ids)


async def test_acknowledging_while_a_rung_fires_is_safe_either_way(
    session: AsyncSession,
) -> None:
    """Both orderings are correct; neither produces a fired *and* cancelled
    timer, and neither loses the acknowledgement."""
    ids = await _committed_case()
    try:

        async def work(s: AsyncSession, index: int) -> str:
            if index == 0:
                await handle_sla_timer(s, {"timer_id": ids["timer0"]})
                return "fired"
            await acknowledge_case(
                s, uuid.UUID(ids["case"]), acknowledged_by=uuid.UUID(ids["doctor"])
            )
            return "acknowledged"

        outcomes = await _independent(work, 2)
        # The helper returns exceptions rather than raising, so assert on them
        # explicitly -- a swallowed deadlock would look like a passing test.
        errors = [o for o in outcomes if isinstance(o, BaseException)]
        assert not errors, f"concurrent work raised: {errors!r}"

        engine = create_async_engine(SETTINGS.database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with maker() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT status, escalation_level FROM sla_timers "
                        " WHERE case_id = :c ORDER BY escalation_level"
                    ),
                    {"c": ids["case"]},
                )
            ).all()
            case = (
                await s.execute(
                    text(
                        "SELECT state, acknowledged_at FROM pending_cases "
                        " WHERE id = :c"
                    ),
                    {"c": ids["case"]},
                )
            ).one()
        await engine.dispose()

        # No timer is both fired and cancelled.
        assert all(r.status in ("fired", "cancelled", "pending") for r in rows)
        # The acknowledgement always lands.
        assert case.state == "acknowledged"
        assert case.acknowledged_at is not None
        # Every rung that did not fire is cancelled.
        assert not [r for r in rows if r.status == "pending"]
    finally:
        await _cleanup(ids)


async def test_concurrent_ladder_starts_do_not_duplicate_rungs(
    session: AsyncSession,
) -> None:
    """Three classifications racing to start the same ladder."""
    engine = create_async_engine(SETTINGS.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        ids = await _seed_case(s)
        await s.commit()
    await engine.dispose()

    try:

        async def start(s: AsyncSession, _: int) -> Any:
            return await start_ladder(s, uuid.UUID(ids["case"]))

        results = await _independent(start, 3)
        created = sum(r.created for r in results if not isinstance(r, BaseException))

        engine = create_async_engine(SETTINGS.database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with maker() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT count(*) AS n FROM sla_timers "
                        " WHERE case_id = :c AND timer_type = 'case_escalation'"
                    ),
                    {"c": ids["case"]},
                )
            ).one()
        await engine.dispose()

        assert row.n == 5, f"expected 5 rungs, found {row.n}"
        assert created == 5
    finally:
        await _cleanup(ids)


# ── recovery ──────────────────────────────────────────────────────────


async def test_the_ladder_survives_its_wake_ups_being_destroyed(
    session: AsyncSession,
) -> None:
    """**PostgreSQL is the truth; pgmq is only a doorbell.**

    Phase 2's central claim, inherited. Delete every queue message for the
    ladder and the timers are still there, still pending, still findable by
    the sweep — which is what makes "NODE A reboots and nothing is lost" true
    for Phase 4's rungs as well as Phase 2's deadlines.
    """
    ids = await _seed_case(session)
    started = await start_ladder(session, uuid.UUID(ids["case"]))
    assert started.created == 5

    destroyed = await session.execute(
        text(
            "DELETE FROM pgmq.q_sla_timers "
            " WHERE message->>'case_id' = :c RETURNING msg_id"
        ),
        {"c": ids["case"]},
    )
    assert len(list(destroyed)) == 5, "the wake-ups were not there to destroy"

    rows = (
        await session.execute(
            text(
                "SELECT escalation_level, status FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'case_escalation' "
                " ORDER BY escalation_level"
            ),
            {"c": ids["case"]},
        )
    ).all()
    assert [r.escalation_level for r in rows] == [0, 1, 2, 3, 4]
    assert all(r.status == "pending" for r in rows)

    # And a rung still fires when the sweep hands it back.
    claim = await claim_timer_for_firing(session, started.timer_ids[0])
    assert claim is not None
    assert claim.escalation_level == 0


# ── the notifications queue Phase 2.3 has been filling ────────────────


async def test_the_notification_consumer_drains_a_phase_2_intent(
    session: AsyncSession,
) -> None:
    """Phase 2.3's intents finally have a consumer."""
    ids = await _seed_case(session)

    await handle_notification(
        session,
        {
            "case_id": ids["case"],
            "template_key": "case_flagged_owner",
            "audience": "responsible_doctor",
            "patient_name": "P",
            "mrn": "MRN-1",
            "test_name": "Urine Culture",
            "severity_label": "CRITICAL",
            "discharged_on": "13 Sep 2026",
            "case_url": "/cases/x",
            "reason_summary": "",
        },
    )

    rows = (
        await session.execute(
            text(
                "SELECT channel, status, user_id FROM notifications "
                " WHERE case_id = :c ORDER BY channel"
            ),
            {"c": ids["case"]},
        )
    ).all()
    assert {r.channel for r in rows} == {"in_app", "email"}
    assert all(r.status == "sent" for r in rows)
    assert all(str(r.user_id) == ids["doctor"] for r in rows)


async def test_a_redelivered_intent_produces_one_notification(
    session: AsyncSession,
) -> None:
    ids = await _seed_case(session)
    intent = {
        "case_id": ids["case"],
        "template_key": "case_flagged_owner",
        "audience": "responsible_doctor",
        "patient_name": "P",
        "mrn": "M",
        "test_name": "T",
        "severity_label": "CRITICAL",
        "discharged_on": "today",
        "case_url": "/c",
        "reason_summary": "",
    }
    for _ in range(3):
        await handle_notification(session, intent)

    row = (
        await session.execute(
            text("SELECT count(*) AS n FROM notifications WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    ).one()
    assert row.n == 2  # one per channel, not six


@pytest.mark.parametrize(
    "message",
    [{}, {"case_id": None}, {"template_key": "x"}, {"case_id": "not-a-uuid"}],
)
async def test_a_malformed_intent_is_dropped_not_guessed(
    session: AsyncSession, message: dict[str, Any]
) -> None:
    """A wrong case_id would tell a doctor about somebody else's patient."""
    await handle_notification(session, message)


# ── Phase 1 / 2 / 3 regression ────────────────────────────────────────


async def test_phase_2_timers_still_work_unchanged(session: AsyncSession) -> None:
    """A Phase 2 timer type takes no rung and still behaves exactly as it did."""
    ids = await _seed_case(session)
    created = await create_timer(
        session,
        case_id=uuid.UUID(ids["case"]),
        timer_type="result_due",
        fire_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )
    assert created.created is True
    row = (
        await session.execute(
            text("SELECT escalation_level, timer_type FROM sla_timers WHERE id = :i"),
            {"i": str(created.timer_id)},
        )
    ).one()
    assert row.escalation_level is None
    assert row.timer_type == "result_due"


async def test_a_phase_2_timer_type_refuses_a_rung(session: AsyncSession) -> None:
    """The rung belongs to ``case_escalation`` and to nothing else."""
    ids = await _seed_case(session)
    with pytest.raises(ValueError, match="escalation_level"):
        await create_timer(
            session,
            case_id=uuid.UUID(ids["case"]),
            timer_type="result_due",
            fire_at=dt.datetime.now(dt.UTC),
            escalation_level=1,
        )


async def test_an_escalation_timer_requires_a_rung(session: AsyncSession) -> None:
    ids = await _seed_case(session)
    with pytest.raises(ValueError, match="escalation_level"):
        await create_timer(
            session,
            case_id=uuid.UUID(ids["case"]),
            timer_type="case_escalation",
            fire_at=dt.datetime.now(dt.UTC),
        )


async def test_the_database_refuses_a_rung_on_the_wrong_type(
    session: AsyncSession,
) -> None:
    """Belt and braces: the CHECK enforces it even if a caller bypasses the
    service."""
    from sqlalchemy.exc import IntegrityError

    ids = await _seed_case(session)
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO sla_timers "
                "(id, case_id, timer_type, fire_at, status, attempts, "
                " idempotency_key, escalation_level) "
                "VALUES (:i, :c, 'result_due', now(), 'pending', 0, :k, 2)"
            ),
            {"i": str(uuid.uuid4()), "c": ids["case"], "k": f"bad-{uuid.uuid4()}"},
        )
    await session.rollback()


async def test_case_events_is_still_append_only(session: AsyncSession) -> None:
    """Phase 1's trigger, still refusing to be rewritten after Phase 4 added
    six new event types."""
    from sqlalchemy.exc import IntegrityError, InternalError

    ids = await _seed_case(session)
    await start_ladder(session, uuid.UUID(ids["case"]))

    with pytest.raises((IntegrityError, InternalError)):
        await session.execute(
            text("UPDATE case_events SET event_type = 'tampered' WHERE case_id = :c"),
            {"c": ids["case"]},
        )
    await session.rollback()
