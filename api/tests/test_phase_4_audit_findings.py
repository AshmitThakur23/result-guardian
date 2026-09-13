"""Regression tests for the defects the Phase 4 audit found.

One test per finding, each written so it fails if the fix is reverted.

* **B1 · P1** — the escalation ladder resolved its target from
  ``contract.responsible_doctor`` and never looked at ``current_owner_id``, so
  a case a human deliberately reassigned kept escalating to the *original*
  doctor. ``POST /reassign`` was cosmetic for the one purpose it exists for.
* **B2 · P1** — a deadlock. The fire path locked ``sla_timers`` then
  ``pending_cases``; ``acknowledge_case`` locked them the other way round.
  PostgreSQL detected it and aborted one of them.
* **B3 · P1** — two rungs falling due at the same instant collapsed onto one
  timer, because the idempotency key had no rung in it. The ladder silently
  lost its upper rungs.
* **B4 · P2** — patient messages bypassed the fatigue controls entirely, so a
  FOLLOW_UP patient SMS went out at 03:00. Worse for a patient than for a
  doctor: they cannot act on it and cannot tell whether it is urgent.
* **B5 · P2** — nothing connected classification to the ladder in a test, so
  the seam where Phase 3 hands over to Phase 4 was unproven.

B2 and B3 have their tests in ``test_phase_4_integration.py``, beside the
concurrency and recovery work they belong to.
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
from app.notifications.adapters import NullAdapter
from app.services import notifications as notify
from app.services.escalation import fire_rung, start_ladder
from app.services.ownership import (
    STEP_ASSIGNED_OWNER,
    STEP_CONTRACT_DOCTOR,
    reassign_case,
    resolve_owner,
)
from scripts.seed_rules_dev import _seed as seed_rules

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
            # B5 drives a real classification, and Rule B reads its antibiotic
            # synonyms from the database. Without them "Monocef" does not map
            # to ceftriaxone and a covered culture reads as an unchecked one.
            await seed_rules(s)
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


async def _world(
    session: AsyncSession, *, severity: str = "critical"
) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:8]
    ids = {
        k: str(uuid.uuid4())
        for k in (
            "dept",
            "doctor",
            "other",
            "head",
            "pat",
            "enc",
            "order",
            "ct",
            "case",
        )
    }
    for key, role, code in (
        ("doctor", "doctor", "DOC"),
        ("other", "doctor", "OTH"),
        ("head", "unit_head", "HED"),
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
    for level, target, channels in (
        (0, "owner", '["in_app"]'),
        (1, "owner", '["in_app"]'),
        (3, "patient", '["sms"]'),
    ):
        await session.execute(
            text(
                "INSERT INTO escalation_chain "
                "(id, department_id, level, target_type, delay_minutes, channels, "
                " severity, active) "
                "VALUES (:i, :d, :l, :t, 0, CAST(:c AS jsonb), :sev, true)"
            ),
            {
                "i": str(uuid.uuid4()),
                "d": ids["dept"],
                "l": level,
                "t": target,
                "c": channels,
                "sev": severity,
            },
        )
    await session.commit()
    return ids


# ── B1 · the ladder must follow a reassignment ────────────────────────


async def test_b1_the_ladder_notifies_the_reassigned_owner(
    session: AsyncSession,
) -> None:
    """Measured before the fix: after a reassignment, rung 1 notified the
    **contract doctor**, not the new owner — which made ``/reassign``
    cosmetic for the one thing it exists to do."""
    ids = await _world(session)
    await start_ladder(session, uuid.UUID(ids["case"]))
    await reassign_case(
        session,
        uuid.UUID(ids["case"]),
        new_owner_id=uuid.UUID(ids["other"]),
        reason="Dr DOC is off shift.",
    )

    await fire_rung(session, uuid.UUID(ids["case"]), 1)

    rows = (
        await session.execute(
            text(
                "SELECT user_id FROM notifications "
                " WHERE case_id = :c AND escalation_level = 1"
            ),
            {"c": ids["case"]},
        )
    ).all()
    assert rows, "rung 1 notified nobody"
    assert {str(r.user_id) for r in rows} == {ids["other"]}


async def test_b1_an_explicit_assignment_outranks_the_contract(
    session: AsyncSession,
) -> None:
    ids = await _world(session)
    await reassign_case(
        session,
        uuid.UUID(ids["case"]),
        new_owner_id=uuid.UUID(ids["other"]),
        reason="covering",
    )
    resolution = await resolve_owner(session, uuid.UUID(ids["case"]), record=False)
    assert resolution.step == STEP_ASSIGNED_OWNER
    assert str(resolution.user_id) == ids["other"]


async def test_b1_an_unreassigned_case_still_starts_at_the_contract_doctor(
    session: AsyncSession,
) -> None:
    """The fix must not skip step 1 for every case. A case whose owner is
    simply the contract doctor resolves exactly as it did before."""
    ids = await _world(session)
    resolution = await resolve_owner(session, uuid.UUID(ids["case"]), record=False)
    assert resolution.step == STEP_CONTRACT_DOCTOR
    assert resolution.level == 1


async def test_b1_a_reassignment_to_someone_who_goes_absent_falls_through(
    session: AsyncSession,
) -> None:
    """The explicit assignment is still subject to availability — otherwise
    reassigning to someone on leave would be a way to silence a case."""
    ids = await _world(session)
    await reassign_case(
        session,
        uuid.UUID(ids["case"]),
        new_owner_id=uuid.UUID(ids["other"]),
        reason="covering",
    )
    await session.execute(
        text(
            "INSERT INTO user_absences (id, user_id, absence_type, starts_at) "
            "VALUES (:i, :u, 'leave', now() - interval '1 hour')"
        ),
        {"i": str(uuid.uuid4()), "u": ids["other"]},
    )

    resolution = await resolve_owner(session, uuid.UUID(ids["case"]), record=False)
    assert resolution.step != STEP_ASSIGNED_OWNER
    assert str(resolution.user_id) == ids["doctor"]


# ── B4 · patients are covered by the fatigue controls too ─────────────


async def test_b4_a_follow_up_patient_sms_respects_quiet_hours(
    session: AsyncSession, adapters: dict[str, NullAdapter]
) -> None:
    """Measured before the fix: a FOLLOW_UP patient SMS went out at 03:00 IST.

    The plan scopes quiet hours by *severity*, not by audience, and a 3am text
    is worse for a patient than for a doctor — they cannot act on it and
    cannot tell whether it is urgent.
    """
    ids = await _world(session, severity="follow_up")
    at_3am = dt.datetime.now(dt.UTC).replace(hour=21, minute=30)  # 03:00 IST

    await fire_rung(session, uuid.UUID(ids["case"]), 3, at=at_3am)

    rows = (
        await session.execute(
            text(
                "SELECT status, suppression_reason FROM notifications "
                " WHERE case_id = :c AND patient_id IS NOT NULL"
            ),
            {"c": ids["case"]},
        )
    ).all()
    assert rows, "no patient notification was recorded at all"
    assert rows[0].status == "suppressed"
    assert rows[0].suppression_reason == "quiet_hours"
    assert not adapters["sms"].sent, "a patient was texted at 3am"


async def test_b4_a_critical_patient_sms_still_goes_out_at_3am(
    session: AsyncSession, adapters: dict[str, NullAdapter]
) -> None:
    """The other half. **CRITICAL always sends immediately** — the fix must
    not have bought quiet nights at the cost of the one message that matters.
    """
    ids = await _world(session, severity="critical")
    at_3am = dt.datetime.now(dt.UTC).replace(hour=21, minute=30)

    await fire_rung(session, uuid.UUID(ids["case"]), 3, at=at_3am)

    rows = (
        await session.execute(
            text(
                "SELECT status FROM notifications "
                " WHERE case_id = :c AND patient_id IS NOT NULL"
            ),
            {"c": ids["case"]},
        )
    ).all()
    assert rows[0].status == "sent"
    assert adapters["sms"].sent


async def test_b4_a_deceased_patient_is_suppressed_not_merely_deferred(
    session: AsyncSession,
) -> None:
    """Ordering: 4.6's absolute rules run *before* the fatigue policy. A
    deceased patient must read ``patient_deceased``, never ``quiet_hours`` —
    deferring implies it goes out later."""
    ids = await _world(session, severity="follow_up")
    await session.execute(
        text("UPDATE encounters SET status = 'deceased' WHERE id = :e"),
        {"e": ids["enc"]},
    )
    at_3am = dt.datetime.now(dt.UTC).replace(hour=21, minute=30)

    await fire_rung(session, uuid.UUID(ids["case"]), 3, at=at_3am)

    row = (
        await session.execute(
            text(
                "SELECT suppression_reason FROM notifications "
                " WHERE case_id = :c AND patient_id IS NOT NULL"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert row.suppression_reason == "patient_deceased"


# ── B5 · the seam between Phase 3 and Phase 4 ─────────────────────────


async def test_b5_classifying_a_flagged_result_starts_the_ladder(
    session: AsyncSession,
) -> None:
    """Phase 3 classifies; Phase 4 escalates. Nothing proved the handover
    until this test — every other Phase 4 test starts the ladder by hand.
    """
    from app.rules.orchestrator import classify_result
    from app.schemas.results import ResultContent
    from app.services.results import record_result

    ids = await _world(session, severity="critical")
    # Reset the case to pre-classification so the orchestrator does the work.
    await session.execute(
        text(
            "UPDATE pending_cases SET state = 'awaiting_result', severity = NULL, "
            "       flagged_at = NULL WHERE id = :c"
        ),
        {"c": ids["case"]},
    )
    await session.execute(
        text(
            "INSERT INTO discharge_medications "
            "(id, encounter_id, drug_name, is_antibiotic) "
            "VALUES (:i, :e, 'Monocef', true)"
        ),
        {"i": str(uuid.uuid4()), "e": ids["enc"]},
    )

    intake = await record_result(
        session,
        uuid.UUID(ids["order"]),
        report_status="final",
        content=ResultContent.model_validate(
            {
                "organisms": [
                    {
                        "organism_name": "Escherichia coli",
                        "colony_count": ">100,000 CFU/mL",
                        "specimen_type": "urine",
                        "sensitivities": [
                            {"antibiotic_name": "Ceftriaxone", "interpretation": "R"}
                        ],
                    }
                ]
            }
        ),
    )
    classification = await classify_result(session, intake.result_id)
    assert classification.severity == "critical"

    timers = (
        await session.execute(
            text(
                "SELECT escalation_level, status FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'case_escalation' "
                " ORDER BY escalation_level"
            ),
            {"c": ids["case"]},
        )
    ).all()
    assert timers, "classification flagged the case but started no ladder"
    assert all(t.status == "pending" for t in timers)
    assert "escalation_ladder_started" in await _events(session, ids["case"])


async def test_b5_a_normal_classification_starts_no_ladder(
    session: AsyncSession,
) -> None:
    """The negative case: there is nobody to chase about a normal result."""
    from app.rules.orchestrator import classify_result
    from app.schemas.results import ResultContent
    from app.services.results import record_result

    ids = await _world(session)
    await session.execute(
        text(
            "UPDATE pending_cases SET state = 'awaiting_result', severity = NULL, "
            "       flagged_at = NULL WHERE id = :c"
        ),
        {"c": ids["case"]},
    )
    await session.execute(
        text(
            "INSERT INTO discharge_medications "
            "(id, encounter_id, drug_name, is_antibiotic) "
            "VALUES (:i, :e, 'Monocef', true)"
        ),
        {"i": str(uuid.uuid4()), "e": ids["enc"]},
    )

    intake = await record_result(
        session,
        uuid.UUID(ids["order"]),
        report_status="final",
        content=ResultContent.model_validate(
            {
                "organisms": [
                    {
                        "organism_name": "Escherichia coli",
                        "colony_count": ">100,000 CFU/mL",
                        "specimen_type": "urine",
                        "sensitivities": [
                            {"antibiotic_name": "Ceftriaxone", "interpretation": "S"}
                        ],
                    }
                ]
            }
        ),
    )
    classification = await classify_result(session, intake.result_id)
    assert classification.severity == "normal"

    timers = (
        await session.execute(
            text(
                "SELECT count(*) AS n FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'case_escalation'"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert int(timers.n) == 0


async def test_b5_a_failing_ladder_does_not_cost_the_classification(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE ONE RULE, at the new seam. The severity is already written and the
    case is already flagged; losing the whole classification because a rung
    could not be booked would be the more expensive failure."""
    from app.rules import orchestrator
    from app.schemas.results import ResultContent
    from app.services.results import record_result

    ids = await _world(session)
    await session.execute(
        text(
            "UPDATE pending_cases SET state = 'awaiting_result', severity = NULL, "
            "       flagged_at = NULL WHERE id = :c"
        ),
        {"c": ids["case"]},
    )

    async def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("escalation_chain is misconfigured")

    monkeypatch.setattr("app.services.escalation.start_ladder", _explode, raising=True)

    intake = await record_result(
        session,
        uuid.UUID(ids["order"]),
        report_status="final",
        content=ResultContent.model_validate(
            {
                "analytes": [
                    {
                        "test_name": "POTASSIUM",
                        "value_numeric": "7.9",
                        "ref_low": "3.5",
                        "ref_high": "5.1",
                    }
                ]
            }
        ),
    )
    classification = await orchestrator.classify_result(session, intake.result_id)

    # The classification survived.
    assert classification.severity == "critical"
    case = (
        await session.execute(
            text("SELECT state, severity FROM pending_cases WHERE id = :c"),
            {"c": ids["case"]},
        )
    ).one()
    assert case.state == "flagged"
    assert case.severity == "critical"
    # And the failure is recorded rather than swallowed.
    assert "escalation_ladder_start_failed" in await _events(session, ids["case"])


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
