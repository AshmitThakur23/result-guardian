"""The doctor's worklist and case detail. Phase 5.2 / 5.7.

    Default view: my open flags, sorted **CRITICAL first, then oldest**
    Rows show: patient name + MRN, test, severity chip, age of flag,
    escalation rung, next escalation time (countdown)
    Pagination everywhere (cursor-based on UUIDv7)

**Sorting is the product.** A worklist that puts a mildly raised potassium
above a critical troponin has quietly undone everything the rule engine did.
So the order is ``severity`` worst-first, then ``opened_at`` oldest-first, and
the tiebreaker is ``id`` — UUIDv7, therefore time-sortable, therefore a stable
total order, which is what makes the cursor work.

**Cursor, not OFFSET.** With a live worklist, page 2 of an OFFSET query skips
rows whenever page 1 shrinks — and the row it skips is a case nobody then
sees. A keyset cursor over ``(severity_rank, opened_at, id)`` cannot skip.

Every query here is scoped by :func:`app.security.scope_clause`. Filtering in
Python after the fetch would paginate and count the unscoped set, which is the
usual way row-level security gets quietly lost.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.security import Scope, scope_clause
from app.services.auth import AuthenticatedUser
from app.services.explain import Explanation, explain_classification, summarise

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Worst first. Expressed as SQL so the sort happens in the index scan rather
# than in Python over a page that was already chosen by a different order.
SEVERITY_RANK_SQL = (
    "CASE pc.severity WHEN 'critical' THEN 0 WHEN 'follow_up' THEN 1 "
    "WHEN 'normal' THEN 2 ELSE 3 END"
)

# What "open" means for the default view: anything a human has not finished
# with. `reopened` is open again by definition.
OPEN_STATES = (
    "awaiting_result",
    "result_received",
    "classified",
    "flagged",
    "reopened",
)


class InvalidCursorError(ValueError):
    """The cursor did not decode. The router turns this into a 422."""


def encode_cursor(
    severity_rank: int, opened_at: dt.datetime, case_id: uuid.UUID
) -> str:
    """Opaque to the client on purpose.

    Not encrypted — it carries no secret, only a position — but opaque enough
    that nobody builds a client that parses it and then breaks when the sort
    changes.
    """
    payload = json.dumps(
        {
            "r": severity_rank,
            "t": opened_at.astimezone(dt.UTC).isoformat(),
            "i": str(case_id),
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, dt.datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return (
            int(data["r"]),
            dt.datetime.fromisoformat(data["t"]),
            uuid.UUID(data["i"]),
        )
    except (
        ValueError,
        KeyError,
        TypeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise InvalidCursorError("The pagination cursor is not valid.") from exc


@dataclass
class WorklistFilters:
    """*"Filters: severity, department, date range, state."*"""

    severity: list[str] | None = None
    state: list[str] | None = None
    department_id: uuid.UUID | None = None
    opened_from: dt.datetime | None = None
    opened_to: dt.datetime | None = None
    owner_id: uuid.UUID | None = None
    mine_only: bool = False
    include_closed: bool = False
    overdue_only: bool = False
    search: str | None = None


@dataclass
class WorklistRow:
    case_id: uuid.UUID
    patient_name: str
    mrn: str
    patient_id: uuid.UUID
    test_name: str
    severity: str | None
    state: str
    opened_at: dt.datetime
    flagged_at: dt.datetime | None
    age_seconds: int
    escalation_level: int | None
    next_escalation_at: dt.datetime | None
    seconds_to_next_escalation: int | None
    owner_id: uuid.UUID | None
    owner_name: str | None
    department_id: uuid.UUID | None
    department_name: str | None
    summary: str
    reopened_count: int


@dataclass
class WorklistPage:
    rows: list[WorklistRow] = field(default_factory=list)
    next_cursor: str | None = None
    total_open: int | None = None


# One shared FROM/JOIN block. The worklist, the counts and the CSV export must
# agree about what a case *is*; three hand-written joins would drift.
_BASE_FROM = """
  FROM pending_cases pc
  JOIN patients p ON p.id = pc.patient_id
  JOIN encounters e ON e.id = pc.encounter_id
  JOIN orders o ON o.id = pc.order_id
  LEFT JOIN departments d ON d.id = e.department_id
  LEFT JOIN users owner ON owner.id = pc.current_owner_id
"""

# The live escalation rung and when it next fires. A lateral subquery rather
# than a join+GROUP BY: it must return the *earliest pending* rung, and an
# aggregate over a joined set would need the whole timer table grouped per
# case for no gain.
_NEXT_ESCALATION = """
  LEFT JOIN LATERAL (
      SELECT t.fire_at, t.escalation_level
        FROM sla_timers t
       WHERE t.case_id = pc.id
         AND t.status = 'pending'
         AND t.deleted_at IS NULL
       ORDER BY t.fire_at
       LIMIT 1
  ) nxt ON TRUE
  LEFT JOIN LATERAL (
      SELECT MAX(t.escalation_level) AS level
        FROM sla_timers t
       WHERE t.case_id = pc.id
         AND t.timer_type = 'case_escalation'
         AND t.status = 'fired'
         AND t.deleted_at IS NULL
  ) rung ON TRUE
"""


def _filter_sql(
    filters: WorklistFilters, scope: Scope, user: AuthenticatedUser
) -> tuple[str, dict[str, Any]]:
    clauses = ["pc.deleted_at IS NULL", scope.clause]
    params: dict[str, Any] = dict(scope.params)

    if not filters.include_closed:
        clauses.append("pc.state = ANY(CAST(:open_states AS text[]))")
        params["open_states"] = list(OPEN_STATES)
    if filters.state:
        clauses.append("pc.state = ANY(CAST(:f_states AS text[]))")
        params["f_states"] = filters.state
    if filters.severity:
        clauses.append("pc.severity = ANY(CAST(:f_sev AS text[]))")
        params["f_sev"] = filters.severity
    if filters.department_id is not None:
        clauses.append("e.department_id = :f_dept")
        params["f_dept"] = str(filters.department_id)
    if filters.opened_from is not None:
        clauses.append("pc.opened_at >= :f_from")
        params["f_from"] = filters.opened_from
    if filters.opened_to is not None:
        clauses.append("pc.opened_at <= :f_to")
        params["f_to"] = filters.opened_to
    if filters.owner_id is not None:
        clauses.append("pc.current_owner_id = :f_owner")
        params["f_owner"] = str(filters.owner_id)
    if filters.mine_only:
        # "My open flags" -- the default view.
        clauses.append("pc.current_owner_id = :f_me")
        params["f_me"] = str(user.id)
    if filters.overdue_only:
        # A rung that should already have fired. The unit head's overdue list
        # in 5.4 is exactly this filter.
        clauses.append(
            "EXISTS (SELECT 1 FROM sla_timers t WHERE t.case_id = pc.id "
            "          AND t.status = 'pending' AND t.deleted_at IS NULL "
            "          AND t.fire_at < now())"
        )
    if filters.search:
        # Patient name or MRN. Capped by the router's length limit; ILIKE
        # rather than trigram similarity because the doctor typing here knows
        # the name and wants the case, not the nearest match.
        clauses.append("(p.name ILIKE :f_q OR p.mrn ILIKE :f_q)")
        params["f_q"] = f"%{filters.search.strip()}%"

    return " AND ".join(f"({c})" for c in clauses), params


async def fetch_worklist(
    session: AsyncSession,
    user: AuthenticatedUser,
    *,
    filters: WorklistFilters | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    now: dt.datetime | None = None,
) -> WorklistPage:
    """CRITICAL first, then oldest, keyset-paginated."""
    filters = filters or WorklistFilters()
    moment = now or dt.datetime.now(dt.UTC)
    page_size = max(1, min(int(limit), MAX_PAGE_SIZE))

    scope = scope_clause(user)
    where, params = _filter_sql(filters, scope, user)

    if cursor:
        rank, opened_at, last_id = decode_cursor(cursor)
        # Row-value comparison: the database's own lexicographic ordering,
        # which is exactly the ORDER BY below. Writing this as nested ORs is
        # where keyset pagination usually goes wrong.
        where += (
            f" AND (({SEVERITY_RANK_SQL}), pc.opened_at, pc.id) "
            "     > (:c_rank, :c_opened, CAST(:c_id AS uuid))"
        )
        params |= {"c_rank": rank, "c_opened": opened_at, "c_id": str(last_id)}

    sql = f"""
        SELECT pc.id AS case_id, pc.severity, pc.state, pc.opened_at,
               pc.flagged_at, pc.current_owner_id, pc.reopened_count,
               {SEVERITY_RANK_SQL} AS severity_rank,
               p.id AS patient_id, p.name AS patient_name, p.mrn,
               o.test_name, e.department_id, d.name AS department_name,
               owner.full_name AS owner_name,
               nxt.fire_at AS next_escalation_at,
               rung.level AS escalation_level,
               cl.severity AS classified_severity, cl.rule_outputs
          {_BASE_FROM}
          {_NEXT_ESCALATION}
          LEFT JOIN LATERAL (
              SELECT c.severity, c.rule_outputs
                FROM classifications c
               WHERE c.case_id = pc.id AND c.deleted_at IS NULL
               ORDER BY c.classified_at DESC
               LIMIT 1
          ) cl ON TRUE
         WHERE {where}
         ORDER BY severity_rank, pc.opened_at, pc.id
         LIMIT :page_size
    """
    params["page_size"] = page_size + 1  # one extra to detect a next page

    rows = (await session.execute(text(sql), params)).all()
    has_more = len(rows) > page_size
    rows = rows[:page_size]

    result = WorklistPage(
        rows=[_to_row(r, moment) for r in rows],
        next_cursor=(
            encode_cursor(
                int(rows[-1].severity_rank), rows[-1].opened_at, rows[-1].case_id
            )
            if has_more and rows
            else None
        ),
    )
    return result


def _to_row(r: Any, now: dt.datetime) -> WorklistRow:
    explanations = explain_classification(r.rule_outputs)
    next_at = r.next_escalation_at
    return WorklistRow(
        case_id=r.case_id,
        patient_name=r.patient_name,
        mrn=r.mrn,
        patient_id=r.patient_id,
        test_name=r.test_name,
        severity=r.severity,
        state=r.state,
        opened_at=r.opened_at,
        flagged_at=r.flagged_at,
        # Age of the *flag* where there is one, else of the case. A case that
        # has been waiting for a result is not an unattended flag.
        age_seconds=int((now - (r.flagged_at or r.opened_at)).total_seconds()),
        escalation_level=r.escalation_level,
        next_escalation_at=next_at,
        seconds_to_next_escalation=(
            int((next_at - now).total_seconds()) if next_at is not None else None
        ),
        owner_id=r.current_owner_id,
        owner_name=r.owner_name,
        department_id=r.department_id,
        department_name=r.department_name,
        summary=summarise(r.severity, explanations),
        reopened_count=int(r.reopened_count or 0),
    )


async def count_open(
    session: AsyncSession,
    user: AuthenticatedUser,
    *,
    filters: WorklistFilters | None = None,
) -> int:
    filters = filters or WorklistFilters()
    scope = scope_clause(user)
    where, params = _filter_sql(filters, scope, user)
    total = await session.execute(
        text(f"SELECT count(*) {_BASE_FROM} WHERE {where}"), params
    )
    return int(total.scalar_one())


# ── case detail ────────────────────────────────────────────────────────


@dataclass
class ResultAnalyteView:
    seq: int
    test_name: str
    value: str | None
    unit: str | None
    ref_low: str | None
    ref_high: str | None
    ref_text: str | None
    abnormal: bool
    abnormal_direction: str | None
    lab_flag: str | None


@dataclass
class SensitivityView:
    organism: str
    colony_count: str | None
    specimen_type: str | None
    # antibiotic -> S / I / R
    sensitivities: list[dict[str, str | None]] = field(default_factory=list)


@dataclass
class TimelineEvent:
    occurred_at: dt.datetime
    event_type: str
    actor_name: str | None
    payload: dict[str, Any]


@dataclass
class CaseDetail:
    case_id: uuid.UUID
    state: str
    severity: str | None
    opened_at: dt.datetime
    flagged_at: dt.datetime | None
    acknowledged_at: dt.datetime | None
    closed_at: dt.datetime | None
    closure_reason: str | None
    closure_note: str | None
    reopened_count: int
    patient: dict[str, Any]
    encounter: dict[str, Any]
    order: dict[str, Any]
    owner: dict[str, Any] | None
    result: dict[str, Any] | None
    analytes: list[ResultAnalyteView] = field(default_factory=list)
    organisms: list[SensitivityView] = field(default_factory=list)
    narratives: list[dict[str, Any]] = field(default_factory=list)
    explanations: list[Explanation] = field(default_factory=list)
    timeline: list[TimelineEvent] = field(default_factory=list)
    escalation_level: int | None = None
    next_escalation_at: dt.datetime | None = None
    can_acknowledge: bool = True


class CaseNotFoundError(LookupError):
    """No such case, or not one this caller may see."""


def _is_abnormal(
    value: Any, low: Any, high: Any, lab_flag: str | None
) -> tuple[bool, str | None]:
    """*"abnormal values highlighted with the reference range."*

    The lab's own flag wins where it exists — it knows the method and the
    population — and the range comparison is the fallback for reports that
    carry a range but no flag.
    """
    if lab_flag and lab_flag.upper() not in {"N", "NORMAL", ""}:
        upper = lab_flag.upper()
        if upper.startswith("H"):
            return True, "high"
        if upper.startswith("L"):
            return True, "low"
        return True, None
    if value is None:
        return False, None
    if high is not None and value > high:
        return True, "high"
    if low is not None and value < low:
        return True, "low"
    return False, None


async def fetch_case_detail(
    session: AsyncSession,
    case_id: uuid.UUID,
    user: AuthenticatedUser,
    *,
    now: dt.datetime | None = None,
) -> CaseDetail:
    """Everything the case page renders, in one place.

    Out-of-scope cases raise :class:`CaseNotFoundError`, which becomes a
    **404** — see :func:`app.security.assert_can_see` for why not 403.
    """
    moment = now or dt.datetime.now(dt.UTC)
    scope = scope_clause(user)

    head = (
        await session.execute(
            text(f"""
                SELECT pc.id, pc.state, pc.severity, pc.opened_at, pc.flagged_at,
                       pc.acknowledged_at, pc.closed_at, pc.closure_reason,
                       pc.closure_note, pc.reopened_count, pc.current_owner_id,
                       pc.result_received_at,
                       p.id AS patient_id, p.name AS patient_name, p.mrn,
                       p.sex, p.dob, p.phone_primary_e164 AS patient_phone,
                       e.id AS encounter_id, e.department_id,
                       e.admitted_at, e.discharged_at, e.status AS encounter_status,
                       d.name AS department_name,
                       o.id AS order_id, o.test_name, o.test_code, o.category,
                       owner.full_name AS owner_name, owner.employee_code
                                                       AS owner_employee_code,
                       nxt.fire_at AS next_escalation_at,
                       rung.level AS escalation_level
                  {_BASE_FROM}
                  {_NEXT_ESCALATION}
                 WHERE pc.id = :case_id AND pc.deleted_at IS NULL
                   AND ({scope.clause})
                """),
            {"case_id": str(case_id), **scope.params},
        )
    ).first()
    if head is None:
        raise CaseNotFoundError(str(case_id))

    detail = CaseDetail(
        case_id=head.id,
        state=head.state,
        severity=head.severity,
        opened_at=head.opened_at,
        flagged_at=head.flagged_at,
        acknowledged_at=head.acknowledged_at,
        closed_at=head.closed_at,
        closure_reason=head.closure_reason,
        closure_note=head.closure_note,
        reopened_count=int(head.reopened_count or 0),
        patient={
            "id": head.patient_id,
            "full_name": head.patient_name,
            "mrn": head.mrn,
            "sex": head.sex,
            "dob": head.dob,
            "phone_e164": head.patient_phone,
        },
        encounter={
            "id": head.encounter_id,
            "department_id": head.department_id,
            "department_name": head.department_name,
            "admitted_at": head.admitted_at,
            "discharged_at": head.discharged_at,
            "status": head.encounter_status,
        },
        order={
            "id": head.order_id,
            "test_name": head.test_name,
            "test_code": head.test_code,
            "category": head.category,
        },
        owner=(
            {
                "id": head.current_owner_id,
                "full_name": head.owner_name,
                "employee_code": head.owner_employee_code,
            }
            if head.current_owner_id
            else None
        ),
        result=None,
        escalation_level=head.escalation_level,
        next_escalation_at=head.next_escalation_at,
        can_acknowledge=head.closed_at is None,
    )

    # The current result. `superseded_by_result_id IS NULL` matters: an
    # amended report must not render underneath the original.
    result_row = (
        await session.execute(
            text(
                "SELECT id, report_status, reported_at, received_at, source, "
                "       source_ref "
                "  FROM results "
                " WHERE case_id = :c AND deleted_at IS NULL "
                "   AND superseded_by_result_id IS NULL "
                " ORDER BY received_at DESC LIMIT 1"
            ),
            {"c": str(case_id)},
        )
    ).first()

    if result_row is not None:
        detail.result = {
            "id": result_row.id,
            "report_status": result_row.report_status,
            "reported_at": result_row.reported_at,
            "received_at": result_row.received_at,
            "source": result_row.source,
            "source_ref": result_row.source_ref,
        }
        await _load_result_content(session, result_row.id, detail)

    classification = (
        await session.execute(
            text(
                "SELECT rule_outputs FROM classifications "
                " WHERE case_id = :c AND deleted_at IS NULL "
                " ORDER BY classified_at DESC LIMIT 1"
            ),
            {"c": str(case_id)},
        )
    ).first()
    if classification is not None:
        detail.explanations = explain_classification(classification.rule_outputs)

    events = (
        await session.execute(
            text(
                "SELECT ce.occurred_at, ce.event_type, ce.payload, "
                "       u.full_name AS actor_name "
                "  FROM case_events ce "
                "  LEFT JOIN users u ON u.id = ce.actor_user_id "
                " WHERE ce.case_id = :c "
                " ORDER BY ce.occurred_at, ce.id"
            ),
            {"c": str(case_id)},
        )
    ).all()
    detail.timeline = [
        TimelineEvent(
            occurred_at=e.occurred_at,
            event_type=e.event_type,
            actor_name=e.actor_name,
            payload=dict(e.payload or {}),
        )
        for e in events
    ]

    _ = moment
    return detail


async def _load_result_content(
    session: AsyncSession, result_id: uuid.UUID, detail: CaseDetail
) -> None:
    """*"rendered as a table (analytes), grid (sensitivities) or text."*"""
    analytes = (
        await session.execute(
            text(
                "SELECT seq, test_name_raw, value_raw, value_numeric, "
                "       unit_normalized, unit_raw, ref_low, ref_high, ref_text, "
                "       abnormal_flag_from_lab "
                "  FROM result_analytes "
                " WHERE result_id = :r AND deleted_at IS NULL ORDER BY seq"
            ),
            {"r": str(result_id)},
        )
    ).all()
    for a in analytes:
        abnormal, direction = _is_abnormal(
            a.value_numeric, a.ref_low, a.ref_high, a.abnormal_flag_from_lab
        )
        detail.analytes.append(
            ResultAnalyteView(
                seq=a.seq,
                test_name=a.test_name_raw,
                value=a.value_raw
                or (str(a.value_numeric) if a.value_numeric is not None else None),
                unit=a.unit_normalized or a.unit_raw,
                ref_low=str(a.ref_low) if a.ref_low is not None else None,
                ref_high=str(a.ref_high) if a.ref_high is not None else None,
                ref_text=a.ref_text,
                abnormal=abnormal,
                abnormal_direction=direction,
                lab_flag=a.abnormal_flag_from_lab,
            )
        )

    organisms = (
        await session.execute(
            text(
                "SELECT id, organism_name, colony_count, specimen_type "
                "  FROM result_organisms "
                " WHERE result_id = :r AND deleted_at IS NULL "
                " ORDER BY organism_name"
            ),
            {"r": str(result_id)},
        )
    ).all()
    for org in organisms:
        sens = (
            await session.execute(
                text(
                    "SELECT antibiotic_name, interpretation, mic_value "
                    "  FROM result_sensitivities "
                    " WHERE organism_id = :o AND deleted_at IS NULL "
                    " ORDER BY antibiotic_name"
                ),
                {"o": str(org.id)},
            )
        ).all()
        detail.organisms.append(
            SensitivityView(
                organism=org.organism_name,
                colony_count=org.colony_count,
                specimen_type=org.specimen_type,
                sensitivities=[
                    {
                        "antibiotic": s.antibiotic_name,
                        "interpretation": s.interpretation,
                        "mic": s.mic_value,
                    }
                    for s in sens
                ],
            )
        )

    narratives = (
        await session.execute(
            text(
                "SELECT section, text FROM result_narratives "
                " WHERE result_id = :r AND deleted_at IS NULL ORDER BY section"
            ),
            {"r": str(result_id)},
        )
    ).all()
    detail.narratives = [{"section": n.section, "text": n.text} for n in narratives]
