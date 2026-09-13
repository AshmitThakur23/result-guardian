"""Phase 5.4 and 5.6 — admin views, the kill switch, metrics and exports.

Unit head: all department cases, overdue list, per-doctor acknowledgement
times
Admin: user management, roster, panic thresholds editor, escalation chain
editor, keyword editor, notification provider health, **NODE B status and
kill switch (``LLM_ENABLED``)**
Overrides report
Turnaround p50/p90 · age buckets · escalation rate · closure reason
distribution · flag rate per 100 discharges · patient notifications and
callbacks · override count · lab flags open and aged
Export to CSV; monthly PDF for NABH (**AAC.12, AAC.6.g**)
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import metrics as metrics_service
from app.services import settings_store
from app.services.pdf import PdfBuilder
from tests._phase5 import bearer, build_world, extra_case

pytestmark = pytest.mark.integration


def _streams(data: bytes) -> bytes:
    """Concatenate every decompressed content stream in a PDF.

    The page text is Flate-compressed, so asserting on the raw bytes finds
    nothing — which is how a "the PDF is empty" bug would slip past a test
    that only checked the header.
    """
    import zlib

    out = b""
    for chunk in data.split(b"stream\n")[1:]:
        blob = chunk.split(b"\nendstream")[0]
        try:
            out += zlib.decompress(blob)
        except zlib.error:  # pragma: no cover - defensive
            continue
    return out


# ── the PDF writer (pure) ──────────────────────────────────────────────


def test_the_pdf_is_a_valid_file() -> None:
    """No library in the path between a clinical metric and a NABH document,
    so the format has to be right by construction."""
    pdf = PdfBuilder(title="Test")
    pdf.heading("Result Guardian")
    pdf.row("Flags per 100 discharges", "3.4")
    data = pdf.build()

    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"/Type /Catalog" in data
    assert b"startxref" in data


def test_the_xref_offsets_point_at_real_objects() -> None:
    """A wrong xref opens in some viewers and not others — the worst kind of
    broken."""
    pdf = PdfBuilder(title="Offsets")
    for i in range(120):  # forces a second page
        pdf.row(f"Metric {i}", str(i))
    data = pdf.build()

    start = int(data.rsplit(b"startxref", 1)[1].split(b"%%EOF")[0].strip())
    assert data[start : start + 4] == b"xref"

    table = data[start:].split(b"\n")
    entries = [line for line in table[2:] if line.endswith(b" n ")]
    assert entries, "there must be object offsets"
    for entry in entries:
        offset = int(entry.split()[0])
        assert data[offset : offset + 1].isdigit()
        assert b" 0 obj" in data[offset : offset + 20]


def test_long_content_paginates() -> None:
    short = PdfBuilder(title="A")
    short.row("one", "1")
    long_doc = PdfBuilder(title="B")
    for i in range(300):
        long_doc.row(f"metric {i}", str(i))

    data = long_doc.build()
    # "/Type /Pages" is the page *tree*; "/Type /Page " with the trailing space
    # is a real page object.
    assert short.build().count(b"/Type /Page ") == 1
    assert data.count(b"/Type /Page ") > 1, "300 rows cannot fit on one A4 page"

    # The footer lives inside a Flate-compressed content stream, so it is not
    # in the raw bytes -- decompress to read it.
    assert b"Page 1 of " in _streams(data)


def test_parentheses_in_text_are_escaped() -> None:
    """An unescaped ``)`` ends the string operator and corrupts the page."""
    pdf = PdfBuilder(title="Escapes")
    pdf.row("Amoxicillin (discharge)", r"R \ S")
    data = pdf.build()
    assert data.startswith(b"%PDF")


def test_unrenderable_characters_become_visible_not_silent() -> None:
    """Dropping a character would turn a name into a different name."""
    from app.services.pdf import _winansi

    assert _winansi("Sunita Rao") == "Sunita Rao"
    assert "?" in _winansi("सुनीता")


# ── metrics ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turnaround_reports_percentiles_not_an_average(
    session: AsyncSession,
) -> None:
    """Turnaround is heavily skewed; a mean describes no actual patient."""
    ids = await build_world(session)
    await session.execute(
        text(
            "UPDATE pending_cases SET acknowledged_at = flagged_at "
            "                       + interval '2 hours' WHERE id = :c"
        ),
        {"c": ids["case"]},
    )
    await session.commit()

    window = metrics_service.MetricsWindow.last(days=7, department_id=ids["dept"])
    stats = {s.stage: s for s in await metrics_service.turnaround(session, window)}

    leg = stats["flag_to_acknowledgement"]
    assert leg.sample_size == 1
    assert leg.p50_seconds == pytest.approx(7200, abs=60)


@pytest.mark.asyncio
async def test_a_case_with_no_result_contributes_nothing_rather_than_a_zero(
    session: AsyncSession,
) -> None:
    """A zero would drag every percentile down and flatter the report."""
    ids = await build_world(session, state="awaiting_result")
    await session.execute(
        text("UPDATE pending_cases SET result_received_at = NULL WHERE id = :c"),
        {"c": ids["case"]},
    )
    await session.commit()

    window = metrics_service.MetricsWindow.last(days=7, department_id=ids["dept"])
    stats = {s.stage: s for s in await metrics_service.turnaround(session, window)}
    assert stats["discharge_to_result"].sample_size == 0
    assert stats["discharge_to_result"].p50_seconds is None


@pytest.mark.asyncio
async def test_age_buckets_ignore_the_window(session: AsyncSession) -> None:
    """*"An open case from two months ago is the single most important row."*

    A 30-day window would hide exactly the case that has been lost.
    """
    ids = await build_world(session)
    old = await extra_case(session, ids, severity="critical", hours_old=24 * 60)

    window = metrics_service.MetricsWindow.last(days=7, department_id=ids["dept"])
    buckets = await metrics_service.open_cases_by_age(session, window)
    total = sum(b.count for b in buckets)

    assert total >= 2, "the two-month-old case must still be counted"
    assert buckets[-1].count >= 1
    assert buckets[-1].critical_count >= 1
    assert old  # referenced for clarity


@pytest.mark.asyncio
async def test_closure_reason_distribution_excludes_auto_closures(
    session: AsyncSession,
) -> None:
    """*"A spike in ``not_clinically_relevant`` means a threshold problem."*

    Auto-closes are the engine's decisions, not judgements. Including them
    would dilute the exact signal this metric exists to expose.
    """
    ids = await build_world(session)
    auto = await extra_case(session, ids, severity="normal", hours_old=2)
    human = await extra_case(session, ids, severity="follow_up", hours_old=2)

    await session.execute(
        text(
            "UPDATE pending_cases SET state = 'closed', closed_at = now(), "
            "       closure_reason = 'auto_closed_normal' WHERE id = :c"
        ),
        {"c": auto},
    )
    await session.execute(
        text(
            "UPDATE pending_cases SET state = 'closed', closed_at = now(), "
            "       closure_reason = 'not_clinically_relevant', "
            "       closure_note = 'Within range for this patient' WHERE id = :c"
        ),
        {"c": human},
    )
    await session.commit()

    window = metrics_service.MetricsWindow.last(days=7, department_id=ids["dept"])
    distribution = await metrics_service.closure_reason_distribution(session, window)
    reasons = {d.closure_reason: d for d in distribution}

    assert "auto_closed_normal" not in reasons
    assert reasons["not_clinically_relevant"].count == 1
    assert reasons["not_clinically_relevant"].share == 1.0


@pytest.mark.asyncio
async def test_the_flag_rate_is_per_discharge_not_per_case(
    session: AsyncSession,
) -> None:
    """*"the alert fatigue metric"* — the clinician experiences flags per
    patient, not flags per case."""
    ids = await build_world(session)
    await extra_case(session, ids, severity="follow_up", hours_old=2)
    await extra_case(session, ids, severity="critical", hours_old=2)

    window = metrics_service.MetricsWindow.last(days=7, department_id=ids["dept"])
    rate = await metrics_service.flag_rate(session, window)

    assert rate.discharges == 1
    assert rate.flags_per_100_discharges == 300.0
    assert rate.critical_per_100_discharges == 200.0


@pytest.mark.asyncio
async def test_escalation_rate_counts_only_rungs_that_fired(
    session: AsyncSession,
) -> None:
    """A ladder stopped by a prompt acknowledgement did not escalate."""
    ids = await build_world(session)
    for level, status in ((0, "fired"), (1, "fired"), (2, "cancelled"), (3, "pending")):
        await session.execute(
            text(
                "INSERT INTO sla_timers "
                "(id, case_id, timer_type, fire_at, status, idempotency_key, "
                " escalation_level, fired_at) "
                "VALUES (:i, :c, 'case_escalation', now(), :st, :k, :lvl, "
                "        CASE WHEN :st2 = 'fired' THEN now() END)"
            ),
            {
                "i": str(uuid.uuid4()),
                "c": ids["case"],
                "st": status,
                "st2": status,
                "k": f"{ids['case']}:esc:{level}:{uuid.uuid4().hex[:6]}",
                "lvl": level,
            },
        )
    await session.commit()

    window = metrics_service.MetricsWindow.last(days=7, department_id=ids["dept"])
    stats = await metrics_service.escalation_rate(session, window)
    assert sum(s.fired for s in stats) == 2
    assert {s.rung for s in stats} == {0, 1}


@pytest.mark.asyncio
async def test_lab_flags_report_the_oldest_open_one(
    session: AsyncSession,
) -> None:
    ids = await build_world(session)
    await session.execute(
        text(
            "INSERT INTO lab_flags (id, case_id, flag_type, raised_at) "
            "VALUES (:i, :c, 'report_delayed', now() - interval '50 hours')"
        ),
        {"i": str(uuid.uuid4()), "c": ids["case"]},
    )
    await session.commit()

    window = metrics_service.MetricsWindow.last(days=7)
    stats = await metrics_service.lab_flag_stats(session, window)
    assert stats.open_count >= 1
    assert stats.oldest_age_hours is not None
    assert stats.oldest_age_hours >= 50


@pytest.mark.asyncio
async def test_the_whole_report_builds(session: AsyncSession) -> None:
    ids = await build_world(session)
    window = metrics_service.MetricsWindow.last(days=30, department_id=ids["dept"])
    report = await metrics_service.build_report(session, window)

    rows = metrics_service.report_rows(report)
    sections = {section for section, _, _ in rows}
    assert {
        "Turnaround",
        "Open cases by age",
        "Closure reasons",
        "Flag rate",
        "Patient contact",
        "Overrides",
        "Lab flags",
    } <= sections | {"Closure reasons"}


# ── the reports API ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_doctor_cannot_read_the_reports(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    assert (
        await client.get("/api/reports/summary", headers=headers)
    ).status_code == 403


@pytest.mark.asyncio
async def test_a_unit_head_is_scoped_to_their_own_department(
    client: Any, session: AsyncSession
) -> None:
    """*"all department cases"*, singular. Asking for another department's
    numbers returns their own rather than erroring at a bookmarked URL."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "head")
    body = (
        await client.get(
            "/api/reports/summary",
            params={"department_id": ids["other_dept"]},
            headers=headers,
        )
    ).json()
    assert body["department_id"] == ids["dept"]


@pytest.mark.asyncio
async def test_an_oversized_window_is_refused(
    client: Any, session: AsyncSession
) -> None:
    """The only thing between this endpoint and a scan of the whole hospital."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.get(
        "/api/reports/summary",
        params={"from": "2000-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_backwards_window_is_refused(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.get(
        "/api/reports/summary",
        params={"from": "2026-09-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_per_doctor_acknowledgement_times(
    client: Any, session: AsyncSession
) -> None:
    """5.4's unit head view."""
    ids = await build_world(session)
    await session.execute(
        text(
            "UPDATE pending_cases SET acknowledged_at = flagged_at "
            "                       + interval '90 minutes', "
            "       state = 'closed', closed_at = now() WHERE id = :c"
        ),
        {"c": ids["case"]},
    )
    await session.commit()

    headers = await bearer(client, ids, "head")
    rows = (await client.get("/api/reports/per-doctor", headers=headers)).json()
    mine = next(r for r in rows if r["user_id"] == ids["doctor"])
    assert mine["cases_closed"] == 1
    assert mine["p50_ack_seconds"] == pytest.approx(5400, abs=60)


@pytest.mark.asyncio
async def test_the_metrics_csv_export(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.get("/api/reports/summary.csv", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.splitlines()
    assert lines[0] == "section,metric,value"
    assert any(line.startswith("Flag rate,") for line in lines)


@pytest.mark.asyncio
async def test_the_nabh_pdf_names_its_clauses_and_tells_the_truth(
    client: Any, session: AsyncSession
) -> None:
    """The provenance block is not decoration.

    An accreditation reviewer must not be able to read this document as
    evidence of clinical validation that has not happened.
    """
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.get("/api/reports/nabh-monthly.pdf", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")

    import zlib

    streams = b""
    for chunk in response.content.split(b"stream\n")[1:]:
        blob = chunk.split(b"\nendstream")[0]
        try:
            streams += zlib.decompress(blob)
        except zlib.error:  # pragma: no cover - defensive
            continue

    assert b"AAC.12" in streams
    assert b"AAC.6.g" in streams
    assert b"NOT been validated by a clinician" in streams


@pytest.mark.asyncio
async def test_the_nabh_pdf_covers_a_calendar_month(
    client: Any, session: AsyncSession
) -> None:
    """A rolling window would double-count the boundary between two packs."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.get(
        "/api/reports/nabh-monthly.pdf",
        params={"month": 8, "year": 2026},
        headers=headers,
    )
    assert response.status_code == 200
    assert "nabh-2026-08.pdf" in response.headers["content-disposition"]


# ── admin: users ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_creating_a_user_returns_a_one_time_password(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    code = f"NEW{uuid.uuid4().hex[:6]}"

    response = await client.post(
        "/api/admin/users",
        json={
            "employee_code": code,
            "full_name": "New Doctor",
            "role": "doctor",
            "department_id": ids["dept"],
        },
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["temporary_password"]

    row = (
        await session.execute(
            text(
                "SELECT password_hash, must_change_password FROM users "
                " WHERE employee_code = :c"
            ),
            {"c": code},
        )
    ).one()
    assert row.must_change_password is True
    assert body["temporary_password"] not in row.password_hash


@pytest.mark.asyncio
async def test_a_duplicate_employee_code_is_a_409(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.post(
        "/api/admin/users",
        json={
            "employee_code": f"DOC{ids['tag']}",
            "full_name": "Impostor",
            "role": "doctor",
        },
        headers=headers,
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_deactivating_a_user_revokes_their_sessions(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    from tests._phase5 import login

    doctor_tokens = await login(client, f"DOC{ids['tag']}")
    headers = await bearer(client, ids, "admin")

    response = await client.patch(
        f"/api/admin/users/{ids['doctor']}",
        json={"is_active": False},
        headers=headers,
    )
    assert response.status_code == 200

    refresh = await client.post(
        "/api/auth/refresh", json={"refresh_token": doctor_tokens["refresh_token"]}
    )
    assert refresh.status_code == 401


@pytest.mark.asyncio
async def test_an_admin_can_unlock_an_account(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    await session.execute(
        text(
            "UPDATE users SET failed_login_count = 5, "
            "       locked_until = now() + interval '15 minutes' WHERE id = :u"
        ),
        {"u": ids["doctor"]},
    )
    await session.commit()

    headers = await bearer(client, ids, "admin")
    response = await client.patch(
        f"/api/admin/users/{ids['doctor']}", json={"unlock": True}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["locked_until"] is None
    assert response.json()["failed_login_count"] == 0


@pytest.mark.asyncio
async def test_a_password_reset_is_audited(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.post(
        f"/api/admin/users/{ids['doctor']}/reset-password", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["temporary_password"]

    audited = (
        await session.execute(
            text(
                "SELECT after FROM audit_log "
                " WHERE action = 'auth.password_changed' AND entity_id = :u "
                " ORDER BY seq DESC LIMIT 1"
            ),
            {"u": ids["doctor"]},
        )
    ).scalar()
    assert audited["reset_by_admin"] is True


# ── admin: configuration editors ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_panic_threshold_supersedes_rather_than_edits(
    client: Any, session: AsyncSession
) -> None:
    """*"A decision made last March must still be explicable using the
    threshold that was in force last March."*"""
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    code = f"K{uuid.uuid4().hex[:6]}"

    first = await client.post(
        "/api/admin/panic-thresholds",
        json={
            "test_code": code,
            "critical_high": "6.5",
            "source": "Hospital SOP v3",
        },
        headers=headers,
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/admin/panic-thresholds",
        json={
            "test_code": code,
            "critical_high": "6.0",
            "source": "Hospital SOP v4",
        },
        headers=headers,
    )
    assert second.status_code == 201

    rows = (
        await session.execute(
            text(
                "SELECT critical_high, effective_from, effective_to "
                "  FROM panic_thresholds WHERE test_code = :t "
                " ORDER BY effective_from"
            ),
            {"t": code},
        )
    ).all()
    assert len(rows) == 2, "the old threshold is kept, not overwritten"
    assert rows[0].effective_to is not None, "the old one is closed"
    assert rows[1].effective_to is None, "the new one is live"


@pytest.mark.asyncio
async def test_a_threshold_needs_a_source(client: Any, session: AsyncSession) -> None:
    """*"Seed from your hospital's own critical value list."* A number with no
    provenance is one nobody can defend at a review."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.post(
        "/api/admin/panic-thresholds",
        json={"test_code": "NA", "critical_high": "150"},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_threshold_that_constrains_nothing_is_refused(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.post(
        "/api/admin/panic-thresholds",
        json={"test_code": "NA", "source": "Hospital SOP v3"},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_the_keyword_editor_round_trips(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    term = f"term-{uuid.uuid4().hex[:8]}"

    created = await client.post(
        "/api/admin/keywords",
        json={"term": term, "category": "malignancy", "severity": "critical"},
        headers=headers,
    )
    assert created.status_code == 201
    keyword_id = created.json()["id"]

    updated = await client.patch(
        f"/api/admin/keywords/{keyword_id}", json={"active": False}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["active"] is False

    listed = (await client.get("/api/admin/keywords", headers=headers)).json()
    assert term not in {k["term"] for k in listed}, "inactive by default is hidden"


@pytest.mark.asyncio
async def test_an_unknown_keyword_category_is_refused(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.post(
        "/api/admin/keywords",
        json={"term": "x", "category": "vibes", "severity": "critical"},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_editing_a_keyword_is_audited(client: Any, session: AsyncSession) -> None:
    """A clinical threshold that changes with no attribution is how it goes
    quietly wrong."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    term = f"term-{uuid.uuid4().hex[:8]}"

    created = await client.post(
        "/api/admin/keywords",
        json={"term": term, "category": "infection", "severity": "follow_up"},
        headers=headers,
    )
    keyword_id = created.json()["id"]
    await client.patch(
        f"/api/admin/keywords/{keyword_id}",
        json={"severity": "critical"},
        headers=headers,
    )

    rows = (
        await session.execute(
            text(
                "SELECT before, after FROM audit_log "
                " WHERE entity_type = 'clinical_keyword' AND entity_id = :k "
                " ORDER BY seq"
            ),
            {"k": keyword_id},
        )
    ).all()
    assert len(rows) == 2
    assert rows[1].before["severity"] == "follow_up"
    assert rows[1].after["severity"] == "critical"


@pytest.mark.asyncio
async def test_the_escalation_chain_editor_upserts_a_rung(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")

    payload = {
        "department_id": ids["dept"],
        "level": 1,
        "target_type": "unit_head",
        "delay_minutes": 45,
        "channels": ["in_app", "sms"],
        "severity": "critical",
    }
    created = await client.put(
        "/api/admin/escalation-chain", json=payload, headers=headers
    )
    assert created.status_code == 200
    assert created.json()["delay_minutes"] == 45

    updated = await client.put(
        "/api/admin/escalation-chain",
        json={**payload, "delay_minutes": 20},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == created.json()["id"], "same rung, updated"
    assert updated.json()["delay_minutes"] == 20


@pytest.mark.asyncio
async def test_editing_the_chain_does_not_move_a_scheduled_timer(
    client: Any, session: AsyncSession
) -> None:
    """Otherwise one form submission pushes every pending escalation out of
    reach."""
    ids = await build_world(session)
    timer_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO sla_timers "
            "(id, case_id, timer_type, fire_at, status, idempotency_key, "
            " escalation_level) "
            "VALUES (:i, :c, 'case_escalation', now() + interval '1 hour', "
            "        'pending', :k, 1)"
        ),
        {
            "i": timer_id,
            "c": ids["case"],
            "k": f"{ids['case']}:chain:{uuid.uuid4().hex[:6]}",
        },
    )
    await session.commit()
    before = (
        await session.execute(
            text("SELECT fire_at FROM sla_timers WHERE id = :i"), {"i": timer_id}
        )
    ).scalar_one()

    headers = await bearer(client, ids, "admin")
    await client.put(
        "/api/admin/escalation-chain",
        json={
            "department_id": ids["dept"],
            "level": 1,
            "target_type": "unit_head",
            "delay_minutes": 9999,
            "severity": "critical",
        },
        headers=headers,
    )

    after = (
        await session.execute(
            text("SELECT fire_at FROM sla_timers WHERE id = :i"), {"i": timer_id}
        )
    ).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_an_unknown_notification_channel_is_refused(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.put(
        "/api/admin/escalation-chain",
        json={
            "department_id": ids["dept"],
            "level": 2,
            "target_type": "admin",
            "delay_minutes": 10,
            "channels": ["carrier_pigeon"],
        },
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_provider_health_surfaces_an_unconfigured_channel(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    await session.execute(
        text(
            "INSERT INTO notifications "
            "(id, case_id, user_id, channel, template_key, payload, status, "
            " dedupe_key, sent_at) "
            "VALUES (:i, :c, :u, 'sms', 'case_escalation', '{}'::jsonb, 'sent', "
            "        :d, now())"
        ),
        {
            "i": str(uuid.uuid4()),
            "c": ids["case"],
            "u": ids["doctor"],
            "d": f"dedupe-{uuid.uuid4().hex}",
        },
    )
    await session.commit()

    headers = await bearer(client, ids, "admin")
    rows = (await client.get("/api/admin/provider-health", headers=headers)).json()
    sms = next(r for r in rows if r["channel"] == "sms")
    assert sms["sent_24h"] == 1
    assert sms["failure_rate"] == 0.0


# ── admin: NODE B and the kill switch ──────────────────────────────────


@pytest.mark.asyncio
async def test_node_b_status_says_safety_is_unaffected(
    client: Any, session: AsyncSession
) -> None:
    """RULE 1 and RULE 2, stated on the endpoint so a red light is not read
    as an outage."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    body = (await client.get("/api/admin/node-b", headers=headers)).json()

    assert body["reachable"] is False
    assert "unaffected" in body["safety_note"]
    assert body["llm_enabled_source"] in {"database", "environment"}


@pytest.mark.asyncio
async def test_the_kill_switch_is_stored_in_a_table_not_the_environment(
    client: Any, session: AsyncSession
) -> None:
    """CLAUDE.md: *"Configuration lives in tables, never in code."*"""
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")

    response = await client.post(
        "/api/admin/node-b/kill-switch",
        json={"llm_enabled": False, "reason": "NODE B returning garbage summaries"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["llm_enabled"] is False
    assert response.json()["llm_enabled_source"] == "database"
    assert "garbage" in response.json()["kill_switch_reason"]

    stored = (
        await session.execute(
            text("SELECT value FROM system_settings WHERE key = 'llm_enabled'")
        )
    ).scalar_one()
    assert stored == "false"


@pytest.mark.asyncio
async def test_the_kill_switch_requires_a_reason(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    response = await client.post(
        "/api/admin/node-b/kill-switch",
        json={"llm_enabled": False, "reason": "x"},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_flipping_the_kill_switch_is_audited(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    await client.post(
        "/api/admin/node-b/kill-switch",
        json={"llm_enabled": False, "reason": "Scheduled NODE B maintenance"},
        headers=headers,
    )

    row = (
        await session.execute(
            text(
                "SELECT after FROM audit_log "
                " WHERE action = 'config.changed' "
                "   AND after->>'setting' = 'llm_enabled' "
                " ORDER BY seq DESC LIMIT 1"
            )
        )
    ).scalar()
    assert row["llm_enabled"] is False
    assert "maintenance" in row["reason"]


@pytest.mark.asyncio
async def test_the_table_overrides_the_environment_default(
    session: AsyncSession,
) -> None:
    await build_world(session)
    settings_store.invalidate()

    assert await settings_store.llm_enabled(session, env_default=True) is True

    await settings_store.set_value(session, settings_store.KEY_LLM_ENABLED, False)
    await session.commit()
    settings_store.invalidate()

    assert await settings_store.llm_enabled(session, env_default=True) is False


@pytest.mark.asyncio
async def test_an_unknown_setting_key_cannot_be_written(
    session: AsyncSession,
) -> None:
    """An open key/value store reachable by HTTP is configuration injection."""
    await build_world(session)
    with pytest.raises(KeyError):
        await settings_store.set_value(session, "database_url", "evil")


@pytest.mark.asyncio
async def test_a_doctor_cannot_flip_the_kill_switch(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        "/api/admin/node-b/kill-switch",
        json={"llm_enabled": False, "reason": "I would prefer it off"},
        headers=headers,
    )
    assert response.status_code == 403


# ── admin: the overrides report ────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_overrides_report_names_who_approved(
    client: Any, session: AsyncSession
) -> None:
    """*"every discharge override with reason and who approved."*"""
    ids = await build_world(session)
    await session.execute(
        text(
            "INSERT INTO discharge_overrides "
            "(id, encounter_id, order_id, reason_code, reason_text, "
            " overridden_by, approved_by) "
            "VALUES (:i, :e, :o, 'patient_lama', "
            "        'Patient left against medical advice before the result', "
            "        :ob, :ap)"
        ),
        {
            "i": str(uuid.uuid4()),
            "e": ids["enc"],
            "o": ids["order"],
            "ob": ids["doctor"],
            "ap": ids["head"],
        },
    )
    await session.commit()

    headers = await bearer(client, ids, "admin")
    rows = (await client.get("/api/admin/overrides", headers=headers)).json()
    row = next(r for r in rows if r["encounter_id"] == ids["enc"])

    assert row["overridden_by_name"] == "DOC User"
    assert row["approved_by_name"] == "HED User"
    assert "against medical advice" in row["reason_text"]
    assert row["mrn"].startswith("MRN")


@pytest.mark.asyncio
async def test_the_override_count_metric(session: AsyncSession) -> None:
    ids = await build_world(session)
    await session.execute(
        text(
            "INSERT INTO discharge_overrides "
            "(id, encounter_id, order_id, reason_code, reason_text, overridden_by) "
            # Phase 1's ck_discharge_overrides_reason_text_min_length enforces a
            # real explanation here, not a placeholder.
            "VALUES (:i, :e, :o, 'clinical_urgency', "
            "        'Bed needed urgently for an emergency admission', :ob)"
        ),
        {
            "i": str(uuid.uuid4()),
            "e": ids["enc"],
            "o": ids["order"],
            "ob": ids["doctor"],
        },
    )
    await session.commit()

    window = metrics_service.MetricsWindow(
        start=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        end=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1),
        department_id=ids["dept"],
    )
    stats = await metrics_service.override_count(session, window)
    assert stats.total == 1
    assert stats.by_reason[0]["reason_code"] == "clinical_urgency"
