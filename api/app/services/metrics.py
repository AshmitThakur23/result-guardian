"""Reports and metrics. Phase 5.6.

Nine numbers the plan asks for, and one of them is the reason the rest exist:

    Closure reason distribution — **a spike in ``not_clinically_relevant``
    means a threshold problem**

That is the self-criticism metric. A system that flags too much gets closed
as irrelevant by tired clinicians, and the closure mix is the only place that
shows up before the flags start being ignored entirely. Paired with

    Flag rate per 100 discharges (the alert fatigue metric)

it is the honest answer to "is this thing helping or is it noise?" — so both
are computed here, plainly, with no smoothing that would flatter them.

**Percentiles are computed in the database.** ``percentile_cont`` over the
real distribution, not an average pretending to be a median: turnaround times
are heavily skewed by a handful of very old cases, and a mean would report a
number that describes no actual patient.

Every query is bounded by a date window and scoped by department where the
caller is not hospital-wide. An unbounded metrics query over a year of a live
hospital is the one place this API could put the database on the floor.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_WINDOW_DAYS = 30

# "Open cases by age bucket." Hours, deliberately fine-grained at the short
# end: the difference between 2 hours and 8 hours old is the difference
# between "being handled" and "nobody has looked".
AGE_BUCKETS = ((0, 4), (4, 24), (24, 72), (72, 168), (168, None))


def _bucket_label(low: int, high: int | None) -> str:
    if high is None:
        return f"{low // 24}d+"
    if high <= 24:
        return f"{low}–{high}h"
    return f"{low // 24}–{high // 24}d"


@dataclass
class MetricsWindow:
    start: dt.datetime
    end: dt.datetime
    department_id: Any | None = None

    @classmethod
    def last(cls, days: int = DEFAULT_WINDOW_DAYS, **kw: Any) -> MetricsWindow:
        end = dt.datetime.now(dt.UTC)
        return cls(start=end - dt.timedelta(days=days), end=end, **kw)


def _scope(window: MetricsWindow) -> tuple[str, dict[str, Any]]:
    clause = "pc.deleted_at IS NULL AND pc.opened_at >= :start AND pc.opened_at <= :end"
    params: dict[str, Any] = {"start": window.start, "end": window.end}
    if window.department_id is not None:
        clause += " AND e.department_id = :dept"
        params["dept"] = str(window.department_id)
    return clause, params


@dataclass
class TurnaroundStats:
    """*"discharge → result → flag → acknowledgement (p50/p90)."*

    Four separate legs, because a slow total can mean four different problems
    and only one of them is the doctor's.
    """

    stage: str
    p50_seconds: float | None
    p90_seconds: float | None
    sample_size: int


async def turnaround(
    session: AsyncSession, window: MetricsWindow
) -> list[TurnaroundStats]:
    where, params = _scope(window)
    # Each leg's own sample: a case with no result yet contributes to
    # "discharge → result" as nothing rather than as a zero, which would drag
    # every percentile down and make the report look better than reality.
    legs = {
        "discharge_to_result": (
            "EXTRACT(EPOCH FROM (pc.result_received_at - e.discharged_at))",
            "e.discharged_at IS NOT NULL AND pc.result_received_at IS NOT NULL",
        ),
        "result_to_flag": (
            "EXTRACT(EPOCH FROM (pc.flagged_at - pc.result_received_at))",
            "pc.result_received_at IS NOT NULL AND pc.flagged_at IS NOT NULL",
        ),
        "flag_to_acknowledgement": (
            "EXTRACT(EPOCH FROM (pc.acknowledged_at - pc.flagged_at))",
            "pc.flagged_at IS NOT NULL AND pc.acknowledged_at IS NOT NULL",
        ),
        "discharge_to_acknowledgement": (
            "EXTRACT(EPOCH FROM (pc.acknowledged_at - e.discharged_at))",
            "e.discharged_at IS NOT NULL AND pc.acknowledged_at IS NOT NULL",
        ),
    }

    results: list[TurnaroundStats] = []
    for stage, (expr, guard) in legs.items():
        row = (
            await session.execute(
                text(f"""
                    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY v) AS p50,
                           percentile_cont(0.9) WITHIN GROUP (ORDER BY v) AS p90,
                           count(*) AS n
                      FROM (
                        SELECT {expr} AS v
                          FROM pending_cases pc
                          JOIN encounters e ON e.id = pc.encounter_id
                         WHERE {where} AND {guard}
                      ) s
                     WHERE v IS NOT NULL AND v >= 0
                    """),
                params,
            )
        ).one()
        results.append(
            TurnaroundStats(
                stage=stage,
                p50_seconds=float(row.p50) if row.p50 is not None else None,
                p90_seconds=float(row.p90) if row.p90 is not None else None,
                sample_size=int(row.n),
            )
        )
    return results


@dataclass
class AgeBucket:
    label: str
    lower_hours: int
    upper_hours: int | None
    count: int
    critical_count: int


async def open_cases_by_age(
    session: AsyncSession, window: MetricsWindow, *, now: dt.datetime | None = None
) -> list[AgeBucket]:
    """*"Open cases by age bucket."*

    Deliberately **not** bounded by the window's start: an open case from two
    months ago is the single most important row in this report, and a
    30-day window would hide exactly the case that has been lost.
    """
    moment = now or dt.datetime.now(dt.UTC)
    params: dict[str, Any] = {"now": moment}
    dept = ""
    if window.department_id is not None:
        dept = " AND e.department_id = :dept"
        params["dept"] = str(window.department_id)

    rows = (
        await session.execute(
            text(f"""
                SELECT EXTRACT(EPOCH FROM (:now - COALESCE(pc.flagged_at,
                                                           pc.opened_at)))/3600
                         AS age_hours,
                       pc.severity
                  FROM pending_cases pc
                  JOIN encounters e ON e.id = pc.encounter_id
                 WHERE pc.deleted_at IS NULL AND pc.closed_at IS NULL{dept}
                """),
            params,
        )
    ).all()

    buckets = [AgeBucket(_bucket_label(lo, hi), lo, hi, 0, 0) for lo, hi in AGE_BUCKETS]
    for row in rows:
        age = float(row.age_hours or 0)
        for bucket in buckets:
            if age >= bucket.lower_hours and (
                bucket.upper_hours is None or age < bucket.upper_hours
            ):
                bucket.count += 1
                if row.severity == "critical":
                    bucket.critical_count += 1
                break
    return buckets


@dataclass
class EscalationStats:
    department_id: Any | None
    department_name: str | None
    rung: int | None
    fired: int


async def escalation_rate(
    session: AsyncSession, window: MetricsWindow
) -> list[EscalationStats]:
    """*"Escalation rate by rung, by department."*

    Counts rungs that actually **fired**. Pending and cancelled rungs are not
    escalations — counting them would report a ladder that was stopped by a
    prompt acknowledgement as if it had gone all the way up.
    """
    where, params = _scope(window)
    rows = (
        await session.execute(
            text(f"""
                SELECT e.department_id, d.name AS department_name,
                       t.escalation_level AS rung, count(*) AS fired
                  FROM sla_timers t
                  JOIN pending_cases pc ON pc.id = t.case_id
                  JOIN encounters e ON e.id = pc.encounter_id
                  LEFT JOIN departments d ON d.id = e.department_id
                 WHERE {where}
                   AND t.timer_type = 'case_escalation'
                   AND t.status = 'fired'
                   AND t.deleted_at IS NULL
                 GROUP BY e.department_id, d.name, t.escalation_level
                 ORDER BY d.name NULLS LAST, t.escalation_level
                """),
            params,
        )
    ).all()
    return [
        EscalationStats(
            department_id=r.department_id,
            department_name=r.department_name,
            rung=r.rung,
            fired=int(r.fired),
        )
        for r in rows
    ]


@dataclass
class ClosureReasonStat:
    closure_reason: str
    count: int
    share: float


async def closure_reason_distribution(
    session: AsyncSession, window: MetricsWindow
) -> list[ClosureReasonStat]:
    """*"A spike in ``not_clinically_relevant`` means a threshold problem."*

    ``share`` is of **clinician** closures only. Auto-closed normals are
    excluded from the denominator: they are the engine's decisions, not
    judgements, and including them would dilute exactly the signal this metric
    exists to expose.
    """
    where, params = _scope(window)
    rows = (
        await session.execute(
            text(f"""
                SELECT pc.closure_reason, count(*) AS n
                  FROM pending_cases pc
                  JOIN encounters e ON e.id = pc.encounter_id
                 WHERE {where} AND pc.closed_at IS NOT NULL
                   AND pc.closure_reason IS NOT NULL
                   AND pc.closure_reason <> 'auto_closed_normal'
                 GROUP BY pc.closure_reason
                 ORDER BY n DESC
                """),
            params,
        )
    ).all()
    total = sum(int(r.n) for r in rows) or 1
    return [
        ClosureReasonStat(
            closure_reason=r.closure_reason,
            count=int(r.n),
            share=round(int(r.n) / total, 4),
        )
        for r in rows
    ]


@dataclass
class FlagRate:
    discharges: int
    flagged_cases: int
    flags_per_100_discharges: float
    critical_per_100_discharges: float


async def flag_rate(session: AsyncSession, window: MetricsWindow) -> FlagRate:
    """*"Flag rate per 100 discharges (the alert fatigue metric)."*

    The denominator is **discharges in the window**, not cases: a system that
    opens three cases per discharge and flags one of them is not flagging 33%,
    it is flagging every third patient, and the clinician experiences the
    latter.
    """
    params: dict[str, Any] = {"start": window.start, "end": window.end}
    dept = ""
    if window.department_id is not None:
        dept = " AND e.department_id = :dept"
        params["dept"] = str(window.department_id)

    discharges = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM encounters e "
                    " WHERE e.deleted_at IS NULL AND e.discharged_at >= :start "
                    f"   AND e.discharged_at <= :end{dept}"
                ),
                params,
            )
        ).scalar_one()
    )
    row = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE pc.flagged_at IS NOT NULL) AS flagged, "
                "       count(*) FILTER (WHERE pc.severity = 'critical') AS crit "
                "  FROM pending_cases pc "
                "  JOIN encounters e ON e.id = pc.encounter_id "
                " WHERE pc.deleted_at IS NULL AND e.discharged_at >= :start "
                f"   AND e.discharged_at <= :end{dept}"
            ),
            params,
        )
    ).one()

    base = discharges or 1
    return FlagRate(
        discharges=discharges,
        flagged_cases=int(row.flagged),
        flags_per_100_discharges=round(int(row.flagged) * 100 / base, 2),
        critical_per_100_discharges=round(int(row.crit) * 100 / base, 2),
    )


@dataclass
class PatientContactStats:
    """*"Patient notifications sent / patients who called back."*"""

    notifications_sent: int
    notifications_suppressed: int
    notifications_failed: int
    outbound_contacts: int
    inbound_callbacks: int
    callback_rate: float


async def patient_contact_stats(
    session: AsyncSession, window: MetricsWindow
) -> PatientContactStats:
    params: dict[str, Any] = {"start": window.start, "end": window.end}
    dept = ""
    join = ""
    if window.department_id is not None:
        join = (
            " JOIN pending_cases pc ON pc.id = n.case_id "
            " JOIN encounters e ON e.id = pc.encounter_id "
        )
        dept = " AND e.department_id = :dept"
        params["dept"] = str(window.department_id)

    notif = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE n.status = 'sent') AS sent, "
                "       count(*) FILTER (WHERE n.status = 'suppressed') AS suppressed, "
                "       count(*) FILTER (WHERE n.status = 'failed') AS failed "
                "  FROM notifications n "
                f"{join}"
                " WHERE n.patient_id IS NOT NULL AND n.deleted_at IS NULL "
                f"   AND n.created_at >= :start AND n.created_at <= :end{dept}"
            ),
            params,
        )
    ).one()

    contact_join = ""
    contact_dept = ""
    if window.department_id is not None:
        contact_join = (
            " JOIN pending_cases pc ON pc.id = c.case_id "
            " JOIN encounters e ON e.id = pc.encounter_id "
        )
        contact_dept = " AND e.department_id = :dept"

    contacts = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE c.direction = 'outbound') AS outbound, "
                "       count(*) FILTER (WHERE c.direction = 'inbound') AS inbound "
                "  FROM patient_contacts c "
                f"{contact_join}"
                " WHERE c.deleted_at IS NULL AND c.contacted_at >= :start "
                f"   AND c.contacted_at <= :end{contact_dept}"
            ),
            params,
        )
    ).one()

    sent = int(notif.sent)
    inbound = int(contacts.inbound)
    return PatientContactStats(
        notifications_sent=sent,
        notifications_suppressed=int(notif.suppressed),
        notifications_failed=int(notif.failed),
        outbound_contacts=int(contacts.outbound),
        inbound_callbacks=inbound,
        callback_rate=round(inbound / sent, 4) if sent else 0.0,
    )


@dataclass
class OverrideStats:
    total: int
    by_reason: list[dict[str, Any]] = field(default_factory=list)


async def override_count(session: AsyncSession, window: MetricsWindow) -> OverrideStats:
    """*"Override count."*

    Every discharge that bypassed the contract gate. A rising count means the
    gate is being routed around, which is a process finding, not a bug.
    """
    params: dict[str, Any] = {"start": window.start, "end": window.end}
    dept = ""
    if window.department_id is not None:
        dept = " AND e.department_id = :dept"
        params["dept"] = str(window.department_id)

    rows = (
        await session.execute(
            text(
                "SELECT o.reason_code, count(*) AS n "
                "  FROM discharge_overrides o "
                "  JOIN encounters e ON e.id = o.encounter_id "
                " WHERE o.deleted_at IS NULL AND o.created_at >= :start "
                f"   AND o.created_at <= :end{dept} "
                " GROUP BY o.reason_code ORDER BY n DESC"
            ),
            params,
        )
    ).all()
    return OverrideStats(
        total=sum(int(r.n) for r in rows),
        by_reason=[{"reason_code": r.reason_code, "count": int(r.n)} for r in rows],
    )


@dataclass
class LabFlagStats:
    """*"Lab flags open and aged."*"""

    open_count: int
    oldest_age_hours: float | None
    by_type: list[dict[str, Any]] = field(default_factory=list)


async def lab_flag_stats(
    session: AsyncSession, window: MetricsWindow, *, now: dt.datetime | None = None
) -> LabFlagStats:
    moment = now or dt.datetime.now(dt.UTC)
    rows = (
        await session.execute(
            text(
                "SELECT flag_type, count(*) AS n, min(raised_at) AS oldest "
                "  FROM lab_flags "
                " WHERE resolved_at IS NULL AND deleted_at IS NULL "
                " GROUP BY flag_type ORDER BY n DESC"
            )
        )
    ).all()
    oldest = min((r.oldest for r in rows if r.oldest is not None), default=None)
    return LabFlagStats(
        open_count=sum(int(r.n) for r in rows),
        oldest_age_hours=(
            round((moment - oldest).total_seconds() / 3600, 1)
            if oldest is not None
            else None
        ),
        by_type=[
            {
                "flag_type": r.flag_type,
                "count": int(r.n),
                "oldest_raised_at": r.oldest,
            }
            for r in rows
        ],
    )


@dataclass
class DoctorAckStats:
    """5.4: *"per-doctor acknowledgement times."*"""

    user_id: Any
    full_name: str
    employee_code: str
    cases_closed: int
    open_cases: int
    p50_ack_seconds: float | None
    p90_ack_seconds: float | None


async def per_doctor_acknowledgement(
    session: AsyncSession, window: MetricsWindow
) -> list[DoctorAckStats]:
    where, params = _scope(window)
    rows = (
        await session.execute(
            text(f"""
                SELECT u.id, u.full_name, u.employee_code,
                       count(*) FILTER (WHERE pc.closed_at IS NOT NULL)
                         AS cases_closed,
                       count(*) FILTER (WHERE pc.closed_at IS NULL) AS open_cases,
                       percentile_cont(0.5) WITHIN GROUP (
                           ORDER BY EXTRACT(EPOCH FROM
                               (pc.acknowledged_at - pc.flagged_at))
                       ) AS p50,
                       percentile_cont(0.9) WITHIN GROUP (
                           ORDER BY EXTRACT(EPOCH FROM
                               (pc.acknowledged_at - pc.flagged_at))
                       ) AS p90
                  FROM pending_cases pc
                  JOIN encounters e ON e.id = pc.encounter_id
                  JOIN users u ON u.id = pc.current_owner_id
                 WHERE {where}
                 GROUP BY u.id, u.full_name, u.employee_code
                 ORDER BY u.full_name
                """),
            params,
        )
    ).all()
    return [
        DoctorAckStats(
            user_id=r.id,
            full_name=r.full_name,
            employee_code=r.employee_code,
            cases_closed=int(r.cases_closed),
            open_cases=int(r.open_cases),
            p50_ack_seconds=float(r.p50) if r.p50 is not None else None,
            p90_ack_seconds=float(r.p90) if r.p90 is not None else None,
        )
        for r in rows
    ]


@dataclass
class MetricsReport:
    """Everything 5.6 asks for, in one object the PDF and the API share."""

    window_start: dt.datetime
    window_end: dt.datetime
    department_id: Any | None
    turnaround: list[TurnaroundStats]
    age_buckets: list[AgeBucket]
    escalations: list[EscalationStats]
    closure_reasons: list[ClosureReasonStat]
    flag_rate: FlagRate
    patient_contacts: PatientContactStats
    overrides: OverrideStats
    lab_flags: LabFlagStats


async def build_report(
    session: AsyncSession, window: MetricsWindow, *, now: dt.datetime | None = None
) -> MetricsReport:
    return MetricsReport(
        window_start=window.start,
        window_end=window.end,
        department_id=window.department_id,
        turnaround=await turnaround(session, window),
        age_buckets=await open_cases_by_age(session, window, now=now),
        escalations=await escalation_rate(session, window),
        closure_reasons=await closure_reason_distribution(session, window),
        flag_rate=await flag_rate(session, window),
        patient_contacts=await patient_contact_stats(session, window),
        overrides=await override_count(session, window),
        lab_flags=await lab_flag_stats(session, window, now=now),
    )


def _hours(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    return f"{Decimal(seconds / 3600).quantize(Decimal('0.1'))}h"


def report_rows(report: MetricsReport) -> list[tuple[str, str, str]]:
    """Flatten the report into ``(section, metric, value)`` rows.

    One flattening shared by the CSV export and the NABH PDF, so the two can
    never disagree about what a number is — which would be the worst possible
    failure mode for a document an accreditation reviewer reads.
    """
    rows: list[tuple[str, str, str]] = []
    for t in report.turnaround:
        rows.append(("Turnaround", f"{t.stage} p50", _hours(t.p50_seconds)))
        rows.append(("Turnaround", f"{t.stage} p90", _hours(t.p90_seconds)))
        rows.append(("Turnaround", f"{t.stage} n", str(t.sample_size)))
    for b in report.age_buckets:
        rows.append(
            ("Open cases by age", b.label, f"{b.count} ({b.critical_count} critical)")
        )
    for esc in report.escalations:
        rows.append(
            (
                "Escalations",
                f"{esc.department_name or 'Unassigned'} rung {esc.rung}",
                str(esc.fired),
            )
        )
    for c in report.closure_reasons:
        rows.append(("Closure reasons", c.closure_reason, f"{c.count} ({c.share:.1%})"))
    rows.append(("Flag rate", "Discharges", str(report.flag_rate.discharges)))
    rows.append(
        (
            "Flag rate",
            "Flags per 100 discharges",
            str(report.flag_rate.flags_per_100_discharges),
        )
    )
    rows.append(
        (
            "Flag rate",
            "CRITICAL per 100 discharges",
            str(report.flag_rate.critical_per_100_discharges),
        )
    )
    pc = report.patient_contacts
    rows.append(("Patient contact", "Notifications sent", str(pc.notifications_sent)))
    rows.append(
        (
            "Patient contact",
            "Notifications suppressed",
            str(pc.notifications_suppressed),
        )
    )
    rows.append(("Patient contact", "Callbacks received", str(pc.inbound_callbacks)))
    rows.append(("Patient contact", "Callback rate", f"{pc.callback_rate:.1%}"))
    rows.append(("Overrides", "Total", str(report.overrides.total)))
    for o in report.overrides.by_reason:
        rows.append(("Overrides", str(o["reason_code"]), str(o["count"])))
    rows.append(("Lab flags", "Open", str(report.lab_flags.open_count)))
    rows.append(
        ("Lab flags", "Oldest open", f"{report.lab_flags.oldest_age_hours or 0}h")
    )
    return rows
