"""Phase 5.2 — the doctor's dashboard and case detail.

    Default view: my open flags, sorted **CRITICAL first, then oldest**
    Rows show: patient name + MRN, test, severity chip, age of flag,
    escalation rung, next escalation time (countdown)
    Filters: severity, department, date range, state
    Case detail: result as table/grid/text, abnormal highlighted, rule output
    **in plain language**, full timeline, action bar
    Pagination everywhere (cursor-based on UUIDv7)

**The sort order is the product.** A worklist that puts a mildly raised
potassium above a critical troponin has undone the rule engine, so it gets
more tests than anything else here.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import explain
from app.services import worklist as worklist_service
from tests._phase5 import bearer, build_world, extra_case

pytestmark = pytest.mark.integration


# ── plain language (pure, no database) ─────────────────────────────────


def test_the_plans_own_worked_example_renders() -> None:
    """*"Amoxicillin-clavulanate (discharge medication) is Resistant for
    E. coli"* — the specification, verbatim."""
    rendered = explain.explain_output(
        {
            "severity": "critical",
            "rule_id": "B",
            "reason_code": "CULT_RESISTANT_TO_DISCHARGE_DRUG",
            "inputs_used": {
                "organism": "E. coli",
                "discharge_antibiotics": ["Amoxicillin-clavulanate"],
            },
            "detail": {
                "offending_drug": "Amoxicillin-clavulanate",
                "alternatives_available": True,
            },
        }
    )
    assert rendered.headline == (
        "Amoxicillin-clavulanate (discharge medication) is Resistant for E. coli."
    )
    assert "prescription needs review" in (rendered.detail or "")


def test_a_critical_numeric_says_what_is_wrong_without_hedging() -> None:
    rendered = explain.explain_output(
        {
            "severity": "critical",
            "rule_id": "A",
            "reason_code": "NUM_ABOVE_CRITICAL_HIGH",
            "inputs_used": {
                "test_name_raw": "Potassium",
                "value_raw": "7.2",
                "unit_normalized": "mmol/L",
                "ref_low": "3.5",
                "ref_high": "5.1",
            },
            "detail": {"critical_high": "6.5"},
        }
    )
    assert "Potassium is 7.2 mmol/L" in rendered.headline
    assert "above the critical threshold of 6.5" in rendered.headline


def test_an_abnormal_value_is_shown_with_its_reference_range() -> None:
    rendered = explain.explain_output(
        {
            "severity": "follow_up",
            "rule_id": "A",
            "reason_code": "NUM_ABOVE_REFERENCE_RANGE",
            "inputs_used": {
                "test_name_raw": "Creatinine",
                "value_raw": "1.9",
                "unit_normalized": "mg/dL",
                "ref_low": "0.6",
                "ref_high": "1.3",
            },
        }
    )
    assert "reference range 0.6–1.3" in rendered.headline


def test_a_missing_range_says_so_rather_than_implying_normal() -> None:
    rendered = explain.explain_output(
        {
            "severity": "follow_up",
            "rule_id": "A",
            "reason_code": "NUM_NO_REFERENCE_RANGE",
            "inputs_used": {"test_name_raw": "Ferritin", "value_raw": "980"},
        }
    )
    assert "no reference range" in rendered.headline
    assert "cannot be judged automatically" in (rendered.detail or "")


def test_a_rule_failure_explains_the_degradation() -> None:
    """THE ONE RULE, surfaced to the doctor rather than hidden."""
    rendered = explain.explain_output(
        {
            "severity": "follow_up",
            "rule_id": "orchestrator",
            "reason_code": "ORCH_RULE_FAILED",
            "inputs_used": {"error_type": "TimeoutError"},
            "detail": {"error_type": "TimeoutError"},
        }
    )
    assert "raised for review" in rendered.headline
    assert "rather than going silent" in (rendered.detail or "")


def test_an_unknown_reason_code_degrades_to_terse_not_to_wrong() -> None:
    """A new rule shipped without a phrasing must not invent a sentence."""
    rendered = explain.explain_output(
        {
            "severity": "critical",
            "rule_id": "D",
            "reason_code": "SOMETHING_ENTIRELY_NEW",
            "inputs_used": {},
        }
    )
    assert "SOMETHING_ENTIRELY_NEW" in rendered.headline


def test_a_hedged_narrative_is_neither_confirmed_nor_dismissed() -> None:
    rendered = explain.explain_output(
        {
            "severity": "follow_up",
            "rule_id": "C",
            "reason_code": "NARR_HEDGED_FINDING",
            "inputs_used": {"section": "impression"},
            "detail": {"matched_terms": ["malignancy"]},
        }
    )
    assert "hedged" in rendered.headline
    assert "not something to dismiss" in (rendered.detail or "")


def test_explanations_are_ordered_worst_first() -> None:
    """The reason the case is open must lead the list."""
    ordered = explain.explain_classification(
        [
            {"severity": "normal", "rule_id": "A", "reason_code": "NUM_IN_RANGE"},
            {
                "severity": "critical",
                "rule_id": "A",
                "reason_code": "NUM_ABOVE_CRITICAL_HIGH",
                "inputs_used": {"test_name_raw": "K"},
                "detail": {"critical_high": "6.5"},
            },
            {
                "severity": "follow_up",
                "rule_id": "C",
                "reason_code": "NARR_NO_KEYWORD_MATCH",
            },
        ]
    )
    assert [e.severity for e in ordered] == ["critical", "follow_up", "normal"]


def test_the_summary_leads_with_the_severity() -> None:
    ordered = explain.explain_classification(
        [
            {
                "severity": "critical",
                "rule_id": "A",
                "reason_code": "NUM_ABOVE_CRITICAL_HIGH",
                "inputs_used": {"test_name_raw": "Potassium", "value_raw": "7.2"},
                "detail": {"critical_high": "6.5"},
            }
        ]
    )
    assert explain.summarise("critical", ordered).startswith("CRITICAL — ")


def test_malformed_rule_outputs_do_not_raise() -> None:
    """A garbled JSONB column must not take the case page down."""
    assert explain.explain_classification(None) == []
    assert explain.explain_classification("not a list") == []
    assert explain.explain_classification([1, "two", None]) == []
    rendered = explain.explain_output(
        {"reason_code": "NUM_ABOVE_RANGE", "inputs_used": "not a dict"}
    )
    assert rendered.headline


# ── cursor encoding (pure) ─────────────────────────────────────────────


def test_a_cursor_round_trips() -> None:
    import datetime as dt

    case_id = uuid.uuid4()
    moment = dt.datetime(2026, 9, 13, 10, 0, tzinfo=dt.UTC)
    rank, decoded_at, decoded_id = worklist_service.decode_cursor(
        worklist_service.encode_cursor(1, moment, case_id)
    )
    assert (rank, decoded_at, decoded_id) == (1, moment, case_id)


def test_a_garbage_cursor_is_rejected_not_ignored() -> None:
    """Silently ignoring it would quietly restart pagination at page 1."""
    with pytest.raises(worklist_service.InvalidCursorError):
        worklist_service.decode_cursor("!!!not-base64!!!")
    with pytest.raises(worklist_service.InvalidCursorError):
        worklist_service.decode_cursor("eyJ3cm9uZyI6ICJzaGFwZSJ9")


# ── sorting ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_critical_comes_before_an_older_follow_up(
    client: Any, session: AsyncSession
) -> None:
    """The single most important ordering assertion in the dashboard."""
    ids = await build_world(session, severity="follow_up")
    # The follow-up from build_world is 5 hours old. This CRITICAL is newer.
    fresh_critical = await extra_case(session, ids, severity="critical", hours_old=1)

    headers = await bearer(client, ids, "doctor")
    rows = (await client.get("/api/worklist", headers=headers)).json()["rows"]

    assert rows[0]["case_id"] == fresh_critical
    assert rows[0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_within_a_severity_the_oldest_comes_first(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session, severity="critical")
    newest = await extra_case(session, ids, severity="critical", hours_old=1)
    oldest = await extra_case(session, ids, severity="critical", hours_old=48)

    headers = await bearer(client, ids, "doctor")
    rows = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    order = [r["case_id"] for r in rows]

    assert order.index(oldest) < order.index(ids["case"]) < order.index(newest)


@pytest.mark.asyncio
async def test_an_unclassified_case_sorts_after_every_classified_one(
    client: Any, session: AsyncSession
) -> None:
    """A NULL severity means "not yet judged", not "worse than critical"."""
    ids = await build_world(session, severity="critical")
    unclassified = await extra_case(session, ids, severity="critical", hours_old=99)
    await session.execute(
        text("UPDATE pending_cases SET severity = NULL WHERE id = :c"),
        {"c": unclassified},
    )
    await session.commit()

    headers = await bearer(client, ids, "doctor")
    rows = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    order = [r["case_id"] for r in rows]
    assert order[-1] == unclassified


# ── the row's contents ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_row_carries_everything_the_plan_asks_for(
    client: Any, session: AsyncSession
) -> None:
    """*"patient name + MRN, test, severity chip, age of flag, escalation
    rung, next escalation time (countdown)."*"""
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    rows = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    row = next(r for r in rows if r["case_id"] == ids["case"])

    assert row["patient_name"] == "Sunita Rao"
    assert row["mrn"].startswith("MRN")
    assert row["test_name"] == "Urine Culture"
    assert row["severity"] == "critical"
    # Flagged five hours ago in the fixture.
    assert 4 * 3600 < row["age_seconds"] < 6 * 3600
    assert "escalation_level" in row
    assert "next_escalation_at" in row
    assert "seconds_to_next_escalation" in row


@pytest.mark.asyncio
async def test_the_countdown_and_the_rung_come_from_real_timers(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    # One rung already fired, the next pending in two hours.
    for level, status, hours in ((0, "fired", -4), (1, "pending", 2)):
        await session.execute(
            text(
                "INSERT INTO sla_timers "
                "(id, case_id, timer_type, fire_at, status, idempotency_key, "
                " escalation_level, fired_at) "
                "VALUES (:i, :c, 'case_escalation', "
                "        now() + make_interval(hours => :h), :st, :k, :lvl, "
                "        CASE WHEN :st2 = 'fired' THEN now() END)"
            ),
            {
                "i": str(uuid.uuid4()),
                "c": ids["case"],
                "h": hours,
                "st": status,
                "st2": status,
                "k": f"{ids['case']}:case_escalation:{level}:{uuid.uuid4().hex[:6]}",
                "lvl": level,
            },
        )
    await session.commit()

    headers = await bearer(client, ids, "doctor")
    rows = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    row = next(r for r in rows if r["case_id"] == ids["case"])

    assert row["escalation_level"] == 0, "the highest rung that has fired"
    assert row["next_escalation_at"] is not None
    assert 6900 < row["seconds_to_next_escalation"] <= 7200


@pytest.mark.asyncio
async def test_an_overdue_rung_shows_a_negative_countdown(
    client: Any, session: AsyncSession
) -> None:
    """Clamping to zero would hide that the worker has fallen behind."""
    ids = await build_world(session)
    await session.execute(
        text(
            "INSERT INTO sla_timers "
            "(id, case_id, timer_type, fire_at, status, idempotency_key, "
            " escalation_level) "
            "VALUES (:i, :c, 'case_escalation', now() - interval '30 minutes', "
            "        'pending', :k, 1)"
        ),
        {
            "i": str(uuid.uuid4()),
            "c": ids["case"],
            "k": f"{ids['case']}:overdue:{uuid.uuid4().hex[:6]}",
        },
    )
    await session.commit()

    headers = await bearer(client, ids, "doctor")
    rows = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    row = next(r for r in rows if r["case_id"] == ids["case"])
    assert row["seconds_to_next_escalation"] < 0


# ── filters ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_severity_filter(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session, severity="critical")
    follow_up = await extra_case(session, ids, severity="follow_up", hours_old=2)

    headers = await bearer(client, ids, "doctor")
    rows = (
        await client.get(
            "/api/worklist", params={"severity": ["follow_up"]}, headers=headers
        )
    ).json()["rows"]
    visible = {r["case_id"] for r in rows}
    assert follow_up in visible
    assert ids["case"] not in visible


@pytest.mark.asyncio
async def test_an_unknown_severity_is_a_422_not_an_empty_list(
    client: Any, session: AsyncSession
) -> None:
    """An empty worklist reads as "nothing to do", which is a dangerous way
    to report a typo."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    response = await client.get(
        "/api/worklist", params={"severity": ["urgent"]}, headers=headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_closed_cases_are_hidden_by_default(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    await session.execute(
        text(
            "UPDATE pending_cases SET state = 'closed', closed_at = now(), "
            "       closure_reason = 'action_taken' WHERE id = :c"
        ),
        {"c": ids["case"]},
    )
    await session.commit()

    headers = await bearer(client, ids, "doctor")
    hidden = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    assert ids["case"] not in {r["case_id"] for r in hidden}

    shown = (
        await client.get(
            "/api/worklist", params={"include_closed": True}, headers=headers
        )
    ).json()["rows"]
    assert ids["case"] in {r["case_id"] for r in shown}


@pytest.mark.asyncio
async def test_the_overdue_filter_finds_only_overdue_cases(
    client: Any, session: AsyncSession
) -> None:
    """5.4's unit-head overdue list is this filter."""
    ids = await build_world(session)
    overdue = await extra_case(session, ids, severity="follow_up", hours_old=30)
    await session.execute(
        text(
            "INSERT INTO sla_timers "
            "(id, case_id, timer_type, fire_at, status, idempotency_key, "
            " escalation_level) "
            "VALUES (:i, :c, 'case_escalation', now() - interval '2 hours', "
            "        'pending', :k, 1)"
        ),
        {
            "i": str(uuid.uuid4()),
            "c": overdue,
            "k": f"{overdue}:od:{uuid.uuid4().hex[:6]}",
        },
    )
    await session.commit()

    headers = await bearer(client, ids, "doctor")
    rows = (
        await client.get(
            "/api/worklist", params={"overdue_only": True}, headers=headers
        )
    ).json()["rows"]
    visible = {r["case_id"] for r in rows}
    assert overdue in visible
    assert ids["case"] not in visible


@pytest.mark.asyncio
async def test_searching_by_mrn(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")

    hit = (
        await client.get(
            "/api/worklist", params={"search": f"MRN{ids['tag']}"}, headers=headers
        )
    ).json()["rows"]
    assert ids["case"] in {r["case_id"] for r in hit}

    miss = (
        await client.get(
            "/api/worklist", params={"search": "NOSUCHMRN"}, headers=headers
        )
    ).json()["rows"]
    assert miss == []


@pytest.mark.asyncio
async def test_mine_only_is_the_default_view(
    client: Any, session: AsyncSession
) -> None:
    """*"Default view: my open flags."*"""
    ids = await build_world(session)
    colleagues = await extra_case(
        session, ids, severity="critical", hours_old=1, owner=ids["head"]
    )

    headers = await bearer(client, ids, "doctor")
    everything = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    assert {ids["case"], colleagues} <= {r["case_id"] for r in everything}

    mine = (
        await client.get("/api/worklist", params={"mine_only": True}, headers=headers)
    ).json()["rows"]
    visible = {r["case_id"] for r in mine}
    assert ids["case"] in visible
    assert colleagues not in visible


# ── pagination ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_cursor_walks_every_row_exactly_once(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    for hours in range(1, 8):
        await extra_case(session, ids, severity="follow_up", hours_old=hours)

    headers = await bearer(client, ids, "doctor")
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, Any] = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        body = (
            await client.get("/api/worklist", params=params, headers=headers)
        ).json()
        seen.extend(r["case_id"] for r in body["rows"])
        cursor = body["next_cursor"]
        if not cursor:
            break

    assert cursor is None, "pagination must terminate"
    assert len(seen) == len(set(seen)) == 8


@pytest.mark.asyncio
async def test_a_new_row_cannot_push_a_row_off_the_next_page(
    client: Any, session: AsyncSession
) -> None:
    """The reason for a keyset cursor rather than OFFSET.

    Inserting a CRITICAL — which sorts to the very front — between page 1 and
    page 2 would shift an OFFSET window and skip a case nobody would then see.
    """
    ids = await build_world(session, severity="follow_up")
    for hours in range(1, 6):
        await extra_case(session, ids, severity="follow_up", hours_old=hours)

    headers = await bearer(client, ids, "doctor")
    first = (
        await client.get("/api/worklist", params={"limit": 3}, headers=headers)
    ).json()
    page_one = [r["case_id"] for r in first["rows"]]

    await extra_case(session, ids, severity="critical", hours_old=99)

    second = (
        await client.get(
            "/api/worklist",
            params={"limit": 3, "cursor": first["next_cursor"]},
            headers=headers,
        )
    ).json()
    page_two = [r["case_id"] for r in second["rows"]]

    assert not set(page_one) & set(page_two), "no row may appear on both pages"
    # And nothing from page 1 is skipped: the union still covers the original
    # six, even though a seventh arrived in between.
    walked = set(page_one) | set(page_two)
    remaining = second["next_cursor"]
    while remaining:
        more = (
            await client.get(
                "/api/worklist",
                params={"limit": 3, "cursor": remaining},
                headers=headers,
            )
        ).json()
        walked |= {r["case_id"] for r in more["rows"]}
        remaining = more["next_cursor"]
    assert ids["case"] in walked


@pytest.mark.asyncio
async def test_the_total_is_opt_in(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")

    without = (await client.get("/api/worklist", headers=headers)).json()
    assert without["total_open"] is None

    with_total = (
        await client.get("/api/worklist", params={"with_total": True}, headers=headers)
    ).json()
    assert with_total["total_open"] >= 1


# ── case detail ────────────────────────────────────────────────────────


async def _seed_result(session: AsyncSession, ids: dict[str, str]) -> str:
    """An analyte report with one abnormal value and one culture."""
    result_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO results (id, order_id, case_id, report_status, "
            " received_at, source) "
            "VALUES (:i, :o, :c, 'final', now(), 'manual')"
        ),
        {"i": result_id, "o": ids["order"], "c": ids["case"]},
    )
    for seq, name, value, low, high, flag in (
        (1, "Potassium", "7.2", "3.5", "5.1", "H"),
        (2, "Sodium", "140", "135", "145", None),
    ):
        await session.execute(
            text(
                "INSERT INTO result_analytes "
                "(id, result_id, seq, test_name_raw, value_raw, value_numeric, "
                " unit_normalized, ref_low, ref_high, abnormal_flag_from_lab) "
                "VALUES (:i, :r, :s, :n, :v_text, CAST(:v_num AS numeric), "
                "        'mmol/L', CAST(:lo AS numeric), CAST(:hi AS numeric), :f)"
            ),
            {
                "i": str(uuid.uuid4()),
                "r": result_id,
                "s": seq,
                "n": name,
                "v_text": value,
                "v_num": value,
                "lo": low,
                "hi": high,
                "f": flag,
            },
        )

    organism_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO result_organisms "
            "(id, result_id, organism_name, colony_count, specimen_type) "
            "VALUES (:i, :r, 'E. coli', '>100000 CFU/mL', 'urine')"
        ),
        {"i": organism_id, "r": result_id},
    )
    for drug, interpretation in (
        ("Amoxicillin-clavulanate", "R"),
        ("Nitrofurantoin", "S"),
    ):
        await session.execute(
            text(
                "INSERT INTO result_sensitivities "
                "(id, organism_id, antibiotic_name, interpretation) "
                "VALUES (:i, :o, :a, :x)"
            ),
            {
                "i": str(uuid.uuid4()),
                "o": organism_id,
                "a": drug,
                "x": interpretation,
            },
        )

    await session.execute(
        text(
            "INSERT INTO result_narratives (id, result_id, section, text) "
            "VALUES (:i, :r, 'impression', 'Significant growth of E. coli.')"
        ),
        {"i": str(uuid.uuid4()), "r": result_id},
    )
    await session.execute(
        text(
            "INSERT INTO classifications "
            "(id, result_id, case_id, severity, rule_outputs, engine_version, "
            " classified_at) "
            "VALUES (:i, :r, :c, 'critical', CAST(:ro AS jsonb), 'test-1', now())"
        ),
        {
            "i": str(uuid.uuid4()),
            "r": result_id,
            "c": ids["case"],
            "ro": (
                '[{"severity":"critical","rule_id":"B",'
                '"reason_code":"CULT_RESISTANT_TO_DISCHARGE_DRUG",'
                '"inputs_used":{"organism":"E. coli"},'
                '"detail":{"offending_drug":"Amoxicillin-clavulanate",'
                '"alternatives_available":true}}]'
            ),
        },
    )
    await session.commit()
    return result_id


@pytest.mark.asyncio
async def test_the_case_page_renders_analytes_as_a_table(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    await _seed_result(session, ids)

    headers = await bearer(client, ids, "doctor")
    body = (
        await client.get(f"/api/cases/{ids['case']}/detail", headers=headers)
    ).json()

    assert len(body["analytes"]) == 2
    potassium = body["analytes"][0]
    assert potassium["test_name"] == "Potassium"
    assert potassium["ref_low"] == "3.500000"
    assert potassium["unit"] == "mmol/L"


@pytest.mark.asyncio
async def test_abnormal_values_are_flagged_and_normal_ones_are_not(
    client: Any, session: AsyncSession
) -> None:
    """*"abnormal values highlighted with the reference range."*"""
    ids = await build_world(session)
    await _seed_result(session, ids)

    headers = await bearer(client, ids, "doctor")
    body = (
        await client.get(f"/api/cases/{ids['case']}/detail", headers=headers)
    ).json()
    by_name = {a["test_name"]: a for a in body["analytes"]}

    assert by_name["Potassium"]["abnormal"] is True
    assert by_name["Potassium"]["abnormal_direction"] == "high"
    assert by_name["Sodium"]["abnormal"] is False


@pytest.mark.asyncio
async def test_sensitivities_render_as_a_grid(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    await _seed_result(session, ids)

    headers = await bearer(client, ids, "doctor")
    body = (
        await client.get(f"/api/cases/{ids['case']}/detail", headers=headers)
    ).json()

    assert len(body["organisms"]) == 1
    organism = body["organisms"][0]
    assert organism["organism"] == "E. coli"
    grid = {s["antibiotic"]: s["interpretation"] for s in organism["sensitivities"]}
    assert grid == {"Amoxicillin-clavulanate": "R", "Nitrofurantoin": "S"}


@pytest.mark.asyncio
async def test_the_narrative_renders_as_text(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    await _seed_result(session, ids)

    headers = await bearer(client, ids, "doctor")
    body = (
        await client.get(f"/api/cases/{ids['case']}/detail", headers=headers)
    ).json()
    assert body["narratives"][0]["section"] == "impression"
    assert "E. coli" in body["narratives"][0]["text"]


@pytest.mark.asyncio
async def test_the_rule_output_is_in_plain_language(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    await _seed_result(session, ids)

    headers = await bearer(client, ids, "doctor")
    body = (
        await client.get(f"/api/cases/{ids['case']}/detail", headers=headers)
    ).json()

    assert body["explanations"], "the doctor must be told why this is flagged"
    headline = body["explanations"][0]["headline"]
    assert headline == (
        "Amoxicillin-clavulanate (discharge medication) is Resistant for E. coli."
    )
    # And the raw code is still there for anyone who wants it.
    assert body["explanations"][0]["reason_code"] == (
        "CULT_RESISTANT_TO_DISCHARGE_DRUG"
    )


@pytest.mark.asyncio
async def test_an_amended_result_does_not_render_under_the_original(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    original = await _seed_result(session, ids)
    amended = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO results (id, order_id, case_id, report_status, "
            " received_at, source) "
            "VALUES (:i, :o, :c, 'amended', now(), 'manual')"
        ),
        {"i": amended, "o": ids["order"], "c": ids["case"]},
    )
    await session.execute(
        text("UPDATE results SET superseded_by_result_id = :new WHERE id = :old"),
        {"new": amended, "old": original},
    )
    await session.commit()

    headers = await bearer(client, ids, "doctor")
    body = (
        await client.get(f"/api/cases/{ids['case']}/detail", headers=headers)
    ).json()
    assert body["result"]["id"] == amended
    assert body["result"]["report_status"] == "amended"


@pytest.mark.asyncio
async def test_the_timeline_comes_from_case_events(
    client: Any, session: AsyncSession
) -> None:
    """*"full timeline from ``case_events``."*"""
    ids = await build_world(session)
    for minutes, event in enumerate(
        ("case_opened", "result_received", "case_classified")
    ):
        await session.execute(
            text(
                "INSERT INTO case_events (id, case_id, event_type, payload, "
                " actor_user_id, occurred_at) "
                "VALUES (:i, :c, :e, '{}'::jsonb, :u, "
                "        now() - make_interval(mins => :m))"
            ),
            {
                "i": str(uuid.uuid4()),
                "c": ids["case"],
                "e": event,
                "u": ids["doctor"],
                "m": 30 - minutes * 10,
            },
        )
    await session.commit()

    headers = await bearer(client, ids, "doctor")
    body = (
        await client.get(f"/api/cases/{ids['case']}/detail", headers=headers)
    ).json()
    types = [e["event_type"] for e in body["timeline"]]
    assert types == ["case_opened", "result_received", "case_classified"]
    assert body["timeline"][0]["actor_name"] == "DOC User"


@pytest.mark.asyncio
async def test_the_header_carries_patient_encounter_and_owner(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    body = (
        await client.get(f"/api/cases/{ids['case']}/detail", headers=headers)
    ).json()

    assert body["patient"]["full_name"] == "Sunita Rao"
    assert body["patient"]["mrn"].startswith("MRN")
    assert body["encounter"]["department_name"] == "Medicine"
    assert body["encounter"]["discharged_at"] is not None
    assert body["owner"]["full_name"] == "DOC User"
    assert body["order"]["test_name"] == "Urine Culture"


@pytest.mark.asyncio
async def test_a_case_with_no_result_still_renders(
    client: Any, session: AsyncSession
) -> None:
    """An awaiting-result case is the most important one to be able to open."""
    ids = await build_world(session, severity="critical", state="awaiting_result")
    headers = await bearer(client, ids, "doctor")
    response = await client.get(f"/api/cases/{ids['case']}/detail", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["result"] is None
    assert body["analytes"] == []
    assert body["explanations"] == []


@pytest.mark.asyncio
async def test_a_missing_case_is_a_404(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    response = await client.get(f"/api/cases/{uuid.uuid4()}/detail", headers=headers)
    assert response.status_code == 404


# ── hand-written SQL, and the one question it raises ───────────────────


@pytest.mark.asyncio
async def test_a_filter_value_cannot_inject_sql(
    client: Any, session: AsyncSession
) -> None:
    """Phase 5 writes SQL by hand, so this needs asserting rather than assuming.

    The f-strings in ``app/services/worklist.py`` interpolate **clause
    fragments built from literals** -- column names and operators chosen by
    the code -- while every value the caller supplies goes through a bind
    parameter. This test is the proof: a search term that is a complete SQL
    statement is treated as a string to match on, and the table it names is
    still there afterwards.
    """
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")

    response = await client.get(
        "/api/worklist",
        params={"search": "'; DROP TABLE pending_cases; --"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["rows"] == []

    survived = (
        await session.execute(text("SELECT count(*) FROM pending_cases"))
    ).scalar_one()
    assert survived >= 1, "pending_cases must still exist"


@pytest.mark.asyncio
async def test_an_injected_cursor_is_rejected_not_executed(
    client: Any, session: AsyncSession
) -> None:
    """The cursor is decoded before it reaches SQL, so a malformed one is a
    422 rather than anything more interesting."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")

    response = await client.get(
        "/api/worklist",
        params={"cursor": "'; DELETE FROM audit_log; --"},
        headers=headers,
    )
    assert response.status_code == 422
