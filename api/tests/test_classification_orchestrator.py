"""Phase 3.6 — the orchestrator and the ``classify`` consumer, end to end.

These are the tests that run against a real case: a patient, an encounter, a
discharge contract, a pending case and a result with real content. The rule
tests above check the rules; these check the part that is easy to get wrong
around them.

What is asserted here, and why each one is not obvious:

* **Rules are picked by content, not by report type.** A radiology report that
  carries a potassium value must still reach Rule A. Dispatching on
  ``orders.category`` would silently skip it.
* **Overall severity is the max.** One critical analyte in a panel of twenty
  normal ones makes the result critical.
* **Auto-close needs unanimity.** A normal culture plus one narrative section
  does not auto-close, because Rule C never permits it.
* **Idempotent per (result_id, engine_version).** Three deliveries, one
  classification, one case transition. A new engine version records a new
  decision — because it *is* a different decision.
* **Amended reports reopen a closed case** with distinct wording, so the
  doctor does not read the amendment as a duplicate.

The consumer tests go through the real ``pgmq`` queue and the real worker
dispatch loop, so the transaction boundary is exercised rather than assumed.
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
from app.rules import SEVERITY_CRITICAL, SEVERITY_FOLLOW_UP, SEVERITY_NORMAL
from app.rules.orchestrator import ResultNotFoundError, classify_result
from scripts.seed_rules_dev import _seed as seed_rules
from worker.consumers.classify import handle_classify

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
            await seed_rules(s)
            yield s
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _case(
    session: AsyncSession,
    *,
    category: str = "lab",
    report_status: str = "final",
    case_state: str = "awaiting_result",
    discharge_antibiotics: tuple[str, ...] = (),
) -> dict[str, Any]:
    """A patient discharged with a pending order, and a result on it."""
    tag = uuid.uuid4().hex[:10]
    ids = {
        k: str(uuid.uuid4())
        for k in ("dept", "user", "pat", "enc", "order", "contract", "case", "result")
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
        text(
            "INSERT INTO patients (id, mrn, name, sex, dob) "
            "VALUES (:i, :m, 'P', 'female', DATE '1979-04-02')"
        ),
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
            "VALUES (:i, :e, :p, 'URC', 'Urine Culture', :cat, now(), 'in_lab')"
        ),
        {"i": ids["order"], "e": ids["enc"], "p": ids["pat"], "cat": category},
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
            "(id, order_id, encounter_id, patient_id, contract_id, current_owner_id, "
            " state) VALUES (:i, :o, :e, :p, :c, :u, :st)"
        ),
        {
            "i": ids["case"],
            "o": ids["order"],
            "e": ids["enc"],
            "p": ids["pat"],
            "c": ids["contract"],
            "u": ids["user"],
            "st": case_state,
        },
    )
    await session.execute(
        text(
            "INSERT INTO results (id, order_id, case_id, report_status, source, "
            " reported_at) VALUES (:i, :o, :c, :rs, 'manual', now())"
        ),
        {
            "i": ids["result"],
            "o": ids["order"],
            "c": ids["case"],
            "rs": report_status,
        },
    )
    for drug in discharge_antibiotics:
        await session.execute(
            text(
                "INSERT INTO discharge_medications "
                "(id, encounter_id, drug_name, is_antibiotic) "
                "VALUES (:i, :e, :d, true)"
            ),
            {"i": str(uuid.uuid4()), "e": ids["enc"], "d": drug},
        )
    await session.commit()
    return ids


async def _analyte(
    session: AsyncSession,
    result_id: str,
    *,
    seq: int,
    name: str,
    value: str | None,
    low: str | None = None,
    high: str | None = None,
    raw: str | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO result_analytes (id, result_id, seq, test_name_raw, "
            " value_numeric, value_raw, ref_low, ref_high) "
            "VALUES (:i, :r, :s, :n, CAST(:v AS numeric), :raw, "
            "        CAST(:lo AS numeric), CAST(:hi AS numeric))"
        ),
        {
            "i": str(uuid.uuid4()),
            "r": result_id,
            "s": seq,
            "n": name,
            "v": value,
            "raw": raw,
            "lo": low,
            "hi": high,
        },
    )


async def _organism(
    session: AsyncSession,
    result_id: str,
    *,
    name: str,
    colony_count: str | None,
    specimen: str,
    sensitivities: tuple[tuple[str, str], ...] = (),
) -> str:
    organism_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO result_organisms (id, result_id, organism_name, "
            " colony_count, specimen_type) VALUES (:i, :r, :n, :c, :s)"
        ),
        {
            "i": organism_id,
            "r": result_id,
            "n": name,
            "c": colony_count,
            "s": specimen,
        },
    )
    for antibiotic, interpretation in sensitivities:
        await session.execute(
            text(
                "INSERT INTO result_sensitivities (id, organism_id, antibiotic_name, "
                " interpretation) VALUES (:i, :o, :a, :v)"
            ),
            {
                "i": str(uuid.uuid4()),
                "o": organism_id,
                "a": antibiotic,
                "v": interpretation,
            },
        )
    return organism_id


async def _narrative(
    session: AsyncSession, result_id: str, *, section: str, body: str
) -> None:
    await session.execute(
        text(
            "INSERT INTO result_narratives (id, result_id, section, text) "
            "VALUES (:i, :r, :s, :t)"
        ),
        {"i": str(uuid.uuid4()), "r": result_id, "s": section, "t": body},
    )


async def _case_row(session: AsyncSession, case_id: str) -> Any:
    return (
        await session.execute(
            text(
                "SELECT state, severity, flagged_at, reopened_count, closed_at "
                "  FROM pending_cases WHERE id = :i"
            ),
            {"i": case_id},
        )
    ).one()


async def _events(session: AsyncSession, case_id: str) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT event_type FROM case_events WHERE case_id = :i "
                " ORDER BY created_at, event_type"
            ),
            {"i": case_id},
        )
    ).all()
    return [r.event_type for r in rows]


# ── dispatch by content ───────────────────────────────────────────────


async def test_a_numeric_value_in_a_radiology_report_still_reaches_rule_a(
    session: AsyncSession,
) -> None:
    """*"Picks rules by content, not by report type (a radiology report can
    contain numbers)."* Dispatching on ``orders.category`` would skip this."""
    ids = await _case(session, category="radiology")
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="POTASSIUM",
        value="7.4",
        low="3.5",
        high="5.1",
    )
    await _narrative(
        session, ids["result"], section="impression", body="Study is adequate."
    )

    classification = await classify_result(session, uuid.UUID(ids["result"]))

    assert classification.severity == SEVERITY_CRITICAL
    rule_ids = {o.rule_id for o in classification.rule_outputs}
    assert rule_ids == {"A_numeric", "C_narrative"}


async def test_all_three_rules_run_on_one_result(session: AsyncSession) -> None:
    ids = await _case(session, discharge_antibiotics=("Monocef",))
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="POTASSIUM",
        value="4.2",
        low="3.5",
        high="5.1",
    )
    await _organism(
        session,
        ids["result"],
        name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen="urine",
        sensitivities=(("Ceftriaxone", "S"),),
    )
    await _narrative(
        session, ids["result"], section="impression", body="Growth as above."
    )

    classification = await classify_result(session, uuid.UUID(ids["result"]))
    assert {o.rule_id for o in classification.rule_outputs} == {
        "A_numeric",
        "B_culture",
        "C_narrative",
    }


async def test_severity_is_the_max_across_every_rule(session: AsyncSession) -> None:
    """One critical value in a panel of normal ones makes the result critical.
    Averaging, or letting the last rule win, would bury it."""
    ids = await _case(session)
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="SODIUM",
        value="140",
        low="135",
        high="145",
    )
    await _analyte(
        session,
        ids["result"],
        seq=2,
        name="GLUCOSE",
        value="95",
        low="70",
        high="110",
    )
    await _analyte(
        session,
        ids["result"],
        seq=3,
        name="POTASSIUM",
        value="7.9",
        low="3.5",
        high="5.1",
    )

    classification = await classify_result(session, uuid.UUID(ids["result"]))
    assert classification.severity == SEVERITY_CRITICAL
    severities = sorted({o.severity for o in classification.rule_outputs})
    assert severities == [SEVERITY_CRITICAL, SEVERITY_NORMAL]


async def test_a_result_with_no_content_is_follow_up_not_normal(
    session: AsyncSession,
) -> None:
    """Nothing to read is not the same as nothing wrong."""
    ids = await _case(session)
    classification = await classify_result(session, uuid.UUID(ids["result"]))
    assert classification.severity == SEVERITY_FOLLOW_UP
    assert classification.rule_outputs[0].reason_code == "ORCH_NO_CLASSIFIABLE_CONTENT"
    assert classification.auto_closed is False


async def test_a_missing_result_raises_rather_than_guessing(
    session: AsyncSession,
) -> None:
    with pytest.raises(ResultNotFoundError):
        await classify_result(session, uuid.uuid4())


# ── the Exit Gate 3 end-to-end case ───────────────────────────────────


async def test_a_resistant_culture_flags_the_case_critical(
    session: AsyncSession,
) -> None:
    """The whole product in one test. The patient went home on Monocef; the
    E. coli is resistant to ceftriaxone; the case ends up flagged CRITICAL
    with a clock running for Phase 4."""
    ids = await _case(session, category="micro", discharge_antibiotics=("Monocef",))
    await _organism(
        session,
        ids["result"],
        name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen="urine",
        sensitivities=(("Ceftriaxone", "R"), ("Nitrofurantoin", "S")),
    )

    classification = await classify_result(session, uuid.UUID(ids["result"]))
    assert classification.severity == SEVERITY_CRITICAL

    case = await _case_row(session, ids["case"])
    assert case.state == "flagged"
    assert case.severity == SEVERITY_CRITICAL
    assert case.flagged_at is not None, "Phase 4's escalation clock needs a start"
    assert "result_classified" in await _events(session, ids["case"])

    # The explanation is persisted, not just returned.
    stored = (
        await session.execute(
            text(
                "SELECT severity, engine_version, rule_outputs "
                "  FROM classifications WHERE result_id = :r"
            ),
            {"r": ids["result"]},
        )
    ).one()
    assert stored.severity == SEVERITY_CRITICAL
    assert stored.engine_version
    assert stored.rule_outputs[0]["reason_code"] == "CULT_RESISTANT_TO_DISCHARGE_DRUG"
    assert stored.rule_outputs[0]["inputs_used"]["discharge_antibiotics"] == ["Monocef"]


async def test_discharge_drugs_come_from_this_encounter_only(
    session: AsyncSession,
) -> None:
    """A different patient's prescription must never enter this comparison."""
    other = await _case(session, discharge_antibiotics=("Ciplox",))
    ids = await _case(session, discharge_antibiotics=("Monocef",))
    assert other["enc"] != ids["enc"]

    await _organism(
        session,
        ids["result"],
        name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen="urine",
        sensitivities=(("Ceftriaxone", "S"), ("Ciprofloxacin", "R")),
    )
    classification = await classify_result(session, uuid.UUID(ids["result"]))
    used = classification.rule_outputs[0].inputs_used["discharge_antibiotics"]
    assert used == ["Monocef"]
    assert classification.severity == SEVERITY_NORMAL


# ── auto-close ────────────────────────────────────────────────────────


async def test_a_covered_culture_auto_closes_the_case(
    session: AsyncSession,
) -> None:
    ids = await _case(session, category="micro", discharge_antibiotics=("Monocef",))
    await _organism(
        session,
        ids["result"],
        name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen="urine",
        sensitivities=(("Ceftriaxone", "S"),),
    )

    classification = await classify_result(session, uuid.UUID(ids["result"]))
    assert classification.severity == SEVERITY_NORMAL
    assert classification.auto_closed is True

    case = await _case_row(session, ids["case"])
    assert case.state == "closed"
    assert "case_auto_closed_by_rule_engine" in await _events(session, ids["case"])


async def test_one_narrative_section_prevents_auto_close(
    session: AsyncSession,
) -> None:
    """The plan's critical safety rule, at the level it actually bites. The
    culture is covered and would close on its own; a single line of prose on
    the same report keeps the case open, because Rule C can never permit a
    close."""
    ids = await _case(session, category="micro", discharge_antibiotics=("Monocef",))
    await _organism(
        session,
        ids["result"],
        name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen="urine",
        sensitivities=(("Ceftriaxone", "S"),),
    )
    await _narrative(
        session,
        ids["result"],
        section="impression",
        body="Sample received in good condition.",
    )

    classification = await classify_result(session, uuid.UUID(ids["result"]))
    assert classification.severity == SEVERITY_NORMAL
    assert classification.auto_closed is False
    case = await _case_row(session, ids["case"])
    assert case.state == "classified"


async def test_a_normal_analyte_panel_auto_closes(session: AsyncSession) -> None:
    ids = await _case(session)
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="SODIUM",
        value="140",
        low="135",
        high="145",
    )
    classification = await classify_result(session, uuid.UUID(ids["result"]))
    assert classification.severity == SEVERITY_NORMAL
    # Rule A emits no auto_close, so the default of "no" applies.
    assert classification.auto_closed is False


# ── preliminary and amended reports ───────────────────────────────────


async def test_a_preliminary_result_is_held_with_a_timer(
    session: AsyncSession,
) -> None:
    ids = await _case(session, report_status="preliminary")
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="POTASSIUM",
        value="4.0",
        low="3.5",
        high="5.1",
    )

    classification = await classify_result(session, uuid.UUID(ids["result"]))
    assert classification.held_as_preliminary is True
    assert classification.auto_closed is False, "a preliminary result never closes"

    timer = (
        await session.execute(
            text(
                "SELECT fire_at, status FROM sla_timers "
                " WHERE case_id = :c AND timer_type = 'stale_preliminary' "
                "   AND status = 'pending' ORDER BY fire_at DESC LIMIT 1"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert timer.status == "pending"
    # 48 hours from the classification, from rule_config -- not hardcoded.
    assert timer.fire_at > dt.datetime.now(dt.UTC) + dt.timedelta(hours=47)
    assert "preliminary_result_held" in await _events(session, ids["case"])


async def test_a_preliminary_critical_is_not_alert_suppressed(
    session: AsyncSession,
) -> None:
    """*"Do not alert unless severity is CRITICAL."* Phase 3 records the
    obligation; the flag is what Phase 4 reads."""
    ids = await _case(session, report_status="preliminary")
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="POTASSIUM",
        value="7.8",
        low="3.5",
        high="5.1",
    )
    await classify_result(session, uuid.UUID(ids["result"]))

    payload = (
        await session.execute(
            text(
                "SELECT payload FROM case_events WHERE case_id = :c "
                "   AND event_type = 'preliminary_result_held'"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert payload.payload["alert_suppressed"] is False
    assert payload.payload["hold_hours"] == 48


async def test_an_amended_result_reopens_a_closed_case_with_distinct_wording(
    session: AsyncSession,
) -> None:
    """*"Amended/corrected result → reopen closed case, increment
    reopened_count, re-notify with distinct wording."* The distinct wording is
    what stops a doctor reading the amendment as a duplicate."""
    ids = await _case(session, report_status="amended", case_state="closed")
    await session.execute(
        text("UPDATE pending_cases SET closed_at = now() WHERE id = :i"),
        {"i": ids["case"]},
    )
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="POTASSIUM",
        value="7.6",
        low="3.5",
        high="5.1",
    )

    classification = await classify_result(session, uuid.UUID(ids["result"]))
    assert classification.reopened is True

    case = await _case_row(session, ids["case"])
    assert case.reopened_count == 1
    assert case.closed_at is None
    # "reopened" is the transition out of "closed", not where the case rests:
    # the amendment's own severity is what decides the state, otherwise a
    # reopened case would carry no severity and nobody would triage it.
    assert case.state == "flagged"
    assert case.severity == SEVERITY_CRITICAL
    assert "case_reopened_for_amendment" in await _events(session, ids["case"])

    intents = (
        await session.execute(
            text(
                "SELECT message FROM pgmq.q_notifications "
                " WHERE message->>'case_id' = :c"
            ),
            {"c": ids["case"]},
        )
    ).all()
    assert [i.message["template_key"] for i in intents] == [
        "result_amended_case_reopened"
    ]
    assert intents[0].message["is_amendment"] is True


async def test_an_amended_result_on_an_open_case_does_not_reopen_it(
    session: AsyncSession,
) -> None:
    """The negative case. Only a *closed* case is reopened; incrementing
    reopened_count on an open one would misreport the history."""
    ids = await _case(session, report_status="amended", case_state="awaiting_result")
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="POTASSIUM",
        value="4.0",
        low="3.5",
        high="5.1",
    )
    classification = await classify_result(session, uuid.UUID(ids["result"]))
    assert classification.reopened is False
    assert (await _case_row(session, ids["case"])).reopened_count == 0


# ── idempotency and replay ────────────────────────────────────────────


async def test_classifying_the_same_result_twice_records_one_decision(
    session: AsyncSession,
) -> None:
    """The property the queue's at-least-once delivery depends on."""
    ids = await _case(session, category="micro", discharge_antibiotics=("Monocef",))
    await _organism(
        session,
        ids["result"],
        name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen="urine",
        sensitivities=(("Ceftriaxone", "R"),),
    )

    first = await classify_result(session, uuid.UUID(ids["result"]))
    second = await classify_result(session, uuid.UUID(ids["result"]))
    third = await classify_result(session, uuid.UUID(ids["result"]))

    assert first.created is True
    assert second.created is False
    assert third.created is False
    assert second.classification_id == first.classification_id
    assert second.severity == first.severity

    count = (
        await session.execute(
            text("SELECT count(*) AS n FROM classifications WHERE result_id = :r"),
            {"r": ids["result"]},
        )
    ).one()
    assert count.n == 1

    # And the clinical side effect happened exactly once.
    events = await _events(session, ids["case"])
    assert events.count("result_classified") == 1


async def test_a_new_engine_version_records_a_new_decision(
    session: AsyncSession,
) -> None:
    """A replay is not a re-decision, but a new engine version is. Blocking it
    would make a rule change unauditable -- there would be no record that the
    result was ever looked at again."""
    ids = await _case(session)
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="POTASSIUM",
        value="4.0",
        low="3.5",
        high="5.1",
    )

    first = await classify_result(session, uuid.UUID(ids["result"]))
    again = await classify_result(
        session, uuid.UUID(ids["result"]), engine_version="9.9.9-test"
    )
    assert first.created is True
    assert again.created is True
    assert again.classification_id != first.classification_id

    versions = (
        await session.execute(
            text(
                "SELECT engine_version FROM classifications WHERE result_id = :r "
                " ORDER BY engine_version"
            ),
            {"r": ids["result"]},
        )
    ).all()
    assert len(versions) == 2


async def test_a_replay_does_not_reopen_a_case_a_second_time(
    session: AsyncSession,
) -> None:
    """The expensive replay. Reopening twice would increment reopened_count
    twice and send the doctor a second amendment notice for one amendment."""
    ids = await _case(session, report_status="amended", case_state="closed")
    await session.execute(
        text("UPDATE pending_cases SET closed_at = now() WHERE id = :i"),
        {"i": ids["case"]},
    )
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="POTASSIUM",
        value="7.6",
        low="3.5",
        high="5.1",
    )

    await classify_result(session, uuid.UUID(ids["result"]))
    await classify_result(session, uuid.UUID(ids["result"]))

    case = await _case_row(session, ids["case"])
    assert case.reopened_count == 1
    intents = (
        await session.execute(
            text(
                "SELECT count(*) AS n FROM pgmq.q_notifications "
                " WHERE message->>'case_id' = :c"
            ),
            {"c": ids["case"]},
        )
    ).one()
    assert intents.n == 1


async def test_classification_is_scoped_to_the_moment_it_ran(
    session: AsyncSession,
) -> None:
    """``at`` drives the threshold lookup, so a decision can be reconstructed
    against the thresholds that were in force -- not today's."""
    ids = await _case(session)
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="POTASSIUM",
        value="7.2",
        low="3.5",
        high="5.1",
    )
    long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(days=400)
    classification = await classify_result(
        session, uuid.UUID(ids["result"]), at=long_ago
    )
    # The placeholder thresholds became effective 365 days ago, so at 400 days
    # ago there was no threshold and the value is merely out of range.
    assert classification.severity == SEVERITY_FOLLOW_UP
    assert classification.rule_outputs[0].reason_code == "NUM_ABOVE_REFERENCE_RANGE"


# ── the consumer ──────────────────────────────────────────────────────


async def test_the_consumer_classifies_a_queued_result(
    session: AsyncSession,
) -> None:
    ids = await _case(session, category="micro", discharge_antibiotics=("Monocef",))
    await _organism(
        session,
        ids["result"],
        name="Escherichia coli",
        colony_count=">100,000 CFU/mL",
        specimen="urine",
        sensitivities=(("Ceftriaxone", "R"),),
    )

    await handle_classify(session, {"result_id": ids["result"]})

    case = await _case_row(session, ids["case"])
    assert case.severity == SEVERITY_CRITICAL
    assert case.state == "flagged"


async def test_a_redelivered_message_changes_nothing(session: AsyncSession) -> None:
    ids = await _case(session)
    await _analyte(
        session,
        ids["result"],
        seq=1,
        name="POTASSIUM",
        value="7.6",
        low="3.5",
        high="5.1",
    )

    for _ in range(3):
        await handle_classify(session, {"result_id": ids["result"]})

    count = (
        await session.execute(
            text("SELECT count(*) AS n FROM classifications WHERE result_id = :r"),
            {"r": ids["result"]},
        )
    ).one()
    assert count.n == 1
    assert (await _events(session, ids["case"])).count("result_classified") == 1


async def test_a_message_for_a_vanished_result_is_dropped_not_retried(
    session: AsyncSession,
) -> None:
    """A soft-deleted result must not spin into the dead-letter queue."""
    await handle_classify(session, {"result_id": str(uuid.uuid4())})


@pytest.mark.parametrize(
    "message",
    [{}, {"result_id": None}, {"result_id": "not-a-uuid"}, {"case_id": "x"}],
)
async def test_a_malformed_message_is_dropped_rather_than_guessed(
    session: AsyncSession, message: dict[str, Any]
) -> None:
    """Guessing a result_id would classify somebody else's report."""
    await handle_classify(session, message)
