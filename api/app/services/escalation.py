"""The escalation ladder. Phase 4.4.

    | Rung | Default | Critical | Target    | Channels            |
    |    0 | now     | now      | owner     | in_app + email      |
    |    1 | +4h     | +1h      | owner     | + SMS               |
    |    2 | +12h    | +4h      | unit head | all                 |
    |    3 | +24h    | +8h      | patient   | SMS                 |
    |    4 | +48h    | +48h     | admin     | all                 |

    - Delays read from ``escalation_chain``, differentiated by severity
    - Each rung fires a timer; **acknowledgement cancels all remaining rungs in
      one transaction**
    - ``case_events`` records every rung with target and channel

Three design points worth stating, because each prevents a specific failure:

**Every rung is scheduled up front, not chained.** When a case is flagged, all
five timers are created at once, each at `flagged_at + delay`. A chained ladder
-- where rung 2 is only created when rung 1 fires -- loses every remaining rung
if one firing fails. Scheduling up front means the ladder survives a worker
that dies at rung 1, because rungs 2-4 are already durable rows in PostgreSQL
and the Phase 2.1 sweep will re-enqueue them. **PostgreSQL is the truth; pgmq
is only a doorbell** -- Phase 2's principle, inherited whole.

**Delays are measured from ``flagged_at``, never from the previous rung.** A
rung that fires an hour late must not push the rest of the ladder an hour out.

**Acknowledgement cancels everything remaining in one statement.** Phase 2.2's
``cancel_case_timers`` already does this atomically under the same row locks
the fire handler takes, so "acknowledge while rung 2 is firing" is safe in
both orderings.

No AI, no NODE B. A rung is a row, a clock and a comparison.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rules import SEVERITY_CRITICAL
from app.services import notifications as notify
from app.services.lab_flags import record_event
from app.services.ownership import (
    STEP_ADMIN_FALLBACK,
    OwnerResolution,
    assign_resolved_owner,
    resolve_owner,
)
from app.services.timers import cancel_case_timers, create_timer

log = structlog.get_logger(__name__)

ESCALATION_TIMER_TYPE = "case_escalation"

EVENT_LADDER_STARTED = "escalation_ladder_started"
EVENT_RUNG_FIRED = "escalation_rung_fired"
EVENT_LADDER_CANCELLED = "escalation_ladder_cancelled"
EVENT_CASE_ACKNOWLEDGED = "case_acknowledged"

# Which template each target gets. Kept here rather than in the chain table
# because a template is code (it must exist as a file), while a delay is
# configuration (an admin may set it to anything).
TEMPLATE_FOR_TARGET = {
    "owner": "case_escalation_owner_reminder",
    "roster_on_duty": "case_escalation_owner_reminder",
    "unit_head": "case_escalation_unit_head",
    "admin": "case_escalation_admin",
    "patient": "patient_result_pending",
}
# Rung 0 is the first contact rather than a reminder, so it reads differently.
TEMPLATE_RUNG_ZERO = "case_flagged_owner"

SEVERITY_LABELS = {
    "critical": "CRITICAL",
    "follow_up": "follow-up",
    "normal": "normal",
}


@dataclass(frozen=True)
class Rung:
    """One configured rung."""

    level: int
    target_type: str
    delay_minutes: int
    channels: list[str]
    severity: str


@dataclass
class LadderStarted:
    case_id: uuid.UUID
    rungs: list[Rung] = field(default_factory=list)
    timer_ids: list[uuid.UUID] = field(default_factory=list)
    created: int = 0
    already_present: int = 0


async def chain_for(
    session: AsyncSession,
    *,
    department_id: uuid.UUID | None,
    severity: str | None,
) -> list[Rung]:
    """The ladder for this department and severity.

    A department's own rows win over the global defaults, per level. That is
    what *"one row per rung, per department… configurable per hospital"* means:
    a department may override rung 2 alone and inherit the rest.

    ``severity``-specific rows win over ``'any'`` rows for the same reason.
    """
    wanted = severity if severity in ("critical", "follow_up") else "any"
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT ON (level) "
                "       level, target_type, delay_minutes, channels, severity "
                "  FROM escalation_chain "
                " WHERE active AND deleted_at IS NULL "
                "   AND (department_id = :dept OR department_id IS NULL) "
                "   AND (severity = :sev OR severity = 'any') "
                " ORDER BY level, "
                # Most specific first: this department beats global, this
                # severity beats 'any'. DISTINCT ON then keeps that one.
                "          (department_id IS NOT NULL) DESC, "
                "          (severity <> 'any') DESC"
            ),
            {"dept": str(department_id) if department_id else None, "sev": wanted},
        )
    ).all()
    return [
        Rung(
            level=int(r.level),
            target_type=r.target_type,
            delay_minutes=int(r.delay_minutes),
            channels=list(r.channels) if isinstance(r.channels, list) else ["in_app"],
            severity=r.severity,
        )
        for r in rows
    ]


async def start_ladder(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    at: dt.datetime | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> LadderStarted:
    """Schedule every rung for a freshly flagged case.

    Idempotent twice over: ``create_timer`` keys on
    ``(case_id, timer_type, fire_at)``, and a second call with the same
    ``flagged_at`` produces the same instants and therefore the same keys. A
    replayed classification does not double the ladder.
    """
    moment = at or dt.datetime.now(dt.UTC)
    case = (
        await session.execute(
            text(
                "SELECT pc.id, pc.severity, pc.state, pc.flagged_at, "
                "       pc.order_id, pc.encounter_id, e.department_id "
                "  FROM pending_cases pc "
                "  JOIN encounters e ON e.id = pc.encounter_id "
                " WHERE pc.id = :c AND pc.deleted_at IS NULL"
            ),
            {"c": str(case_id)},
        )
    ).first()
    if case is None:
        return LadderStarted(case_id=case_id)

    # A case nobody needs to chase gets no ladder. `normal` is not flagged.
    if case.severity not in ("follow_up", "critical"):
        return LadderStarted(case_id=case_id)
    if case.state in ("closed", "acknowledged"):
        return LadderStarted(case_id=case_id)

    # The clock starts when the case was flagged, so a ladder started late
    # (a replay, a sweep) still lands its rungs on the original schedule.
    anchor = case.flagged_at or moment
    rungs = await chain_for(
        session, department_id=case.department_id, severity=case.severity
    )

    started = LadderStarted(case_id=case_id, rungs=rungs)
    for rung in rungs:
        fire_at = anchor + dt.timedelta(minutes=rung.delay_minutes)
        created = await create_timer(
            session,
            case_id=case_id,
            timer_type=ESCALATION_TIMER_TYPE,
            fire_at=fire_at,
            order_id=case.order_id,
            encounter_id=case.encounter_id,
            actor_user_id=actor_user_id,
            now=moment,
            escalation_level=rung.level,
        )
        started.timer_ids.append(created.timer_id)
        if created.created:
            started.created += 1
        else:
            started.already_present += 1

    if started.created:
        await record_event(
            session,
            case_id,
            EVENT_LADDER_STARTED,
            {
                "severity": case.severity,
                "anchor": anchor.isoformat(),
                "rungs": [
                    {
                        "level": r.level,
                        "target": r.target_type,
                        "delay_minutes": r.delay_minutes,
                        "channels": r.channels,
                    }
                    for r in rungs
                ],
                "timers_created": started.created,
            },
            actor_user_id=actor_user_id,
            now=moment,
        )
    return started


async def _target_recipient(
    session: AsyncSession,
    case_id: uuid.UUID,
    rung: Rung,
    *,
    at: dt.datetime,
) -> tuple[uuid.UUID | None, uuid.UUID | None, OwnerResolution | None]:
    """Who this rung is aimed at: ``(user_id, patient_id, resolution)``."""
    if rung.target_type == "patient":
        row = (
            await session.execute(
                text("SELECT patient_id FROM pending_cases WHERE id = :c"),
                {"c": str(case_id)},
            )
        ).first()
        return (None, row.patient_id if row else None, None)

    if rung.target_type == "unit_head":
        row = (
            await session.execute(
                text(
                    "SELECT d.unit_head_user_id "
                    "  FROM pending_cases pc "
                    "  JOIN encounters e ON e.id = pc.encounter_id "
                    "  LEFT JOIN departments d ON d.id = e.department_id "
                    " WHERE pc.id = :c"
                ),
                {"c": str(case_id)},
            )
        ).first()
        return (row.unit_head_user_id if row else None, None, None)

    if rung.target_type == "admin":
        # Resolve rather than hardcode: the admin fallback already knows how to
        # find an available administrator.
        resolution = await resolve_owner(session, case_id, at=at, record=False)
        admin = (
            await session.execute(
                text(
                    "SELECT id FROM users WHERE role = 'admin' AND is_active "
                    "   AND deleted_at IS NULL ORDER BY employee_code, id LIMIT 1"
                )
            )
        ).first()
        return (admin.id if admin else None, None, resolution)

    # owner / roster_on_duty -- resolve through the Phase 4.2 ladder.
    resolution = await resolve_owner(session, case_id, at=at, record=False)
    return (resolution.user_id, None, resolution)


async def _render_context(
    session: AsyncSession, case_id: uuid.UUID, rung: Rung, at: dt.datetime
) -> dict[str, object]:
    row = (
        await session.execute(
            text(
                "SELECT p.name AS patient_name, p.mrn, o.test_name, "
                "       pc.severity, pc.flagged_at, e.discharged_at, "
                "       COALESCE(d.name, 'the department') AS department_name, "
                "       COALESCE(u.full_name, 'unassigned') AS owner_name "
                "  FROM pending_cases pc "
                "  JOIN patients p ON p.id = pc.patient_id "
                "  JOIN orders o ON o.id = pc.order_id "
                "  JOIN encounters e ON e.id = pc.encounter_id "
                "  LEFT JOIN departments d ON d.id = e.department_id "
                "  LEFT JOIN users u ON u.id = pc.current_owner_id "
                " WHERE pc.id = :c"
            ),
            {"c": str(case_id)},
        )
    ).one()

    elapsed = (at - row.flagged_at).total_seconds() / 3600 if row.flagged_at else 0.0
    return {
        "patient_name": row.patient_name,
        "mrn": row.mrn,
        "test_name": row.test_name,
        "severity_label": SEVERITY_LABELS.get(row.severity or "", "flagged"),
        "department_name": row.department_name,
        "owner_name": row.owner_name,
        "discharged_on": (
            row.discharged_at.strftime("%d %b %Y") if row.discharged_at else "recently"
        ),
        "elapsed_label": f"{elapsed:.0f} hours",
        "case_url": f"/cases/{case_id}",
        "reason_summary": "",
        # Patient template variables. Deliberately carry no clinical content.
        "hospital_name": "the hospital",
        "hospital_phone": "the number on your discharge summary",
    }


@dataclass
class RungFired:
    case_id: uuid.UUID
    level: int
    target_type: str
    dispatches: list[notify.Dispatch] = field(default_factory=list)
    skipped_reason: str | None = None


async def fire_rung(
    session: AsyncSession,
    case_id: uuid.UUID,
    level: int,
    *,
    timer_id: uuid.UUID | None = None,
    at: dt.datetime | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> RungFired:
    """Run one rung: resolve the target, notify on every configured channel,
    and record it.

    A rung never raises. A provider outage, a missing recipient or an
    unverified phone all come back as dispatch outcomes, and the ladder keeps
    its remaining timers.
    """
    moment = at or dt.datetime.now(dt.UTC)
    case = (
        await session.execute(
            text(
                "SELECT pc.severity, pc.state, e.department_id "
                "  FROM pending_cases pc "
                "  JOIN encounters e ON e.id = pc.encounter_id "
                " WHERE pc.id = :c AND pc.deleted_at IS NULL"
            ),
            {"c": str(case_id)},
        )
    ).first()
    if case is None:
        return RungFired(case_id, level, "unknown", skipped_reason="case_missing")

    # Acknowledged or closed between scheduling and firing. The cancel should
    # have caught it; checking costs nothing and makes the trail honest.
    if case.state in ("closed", "acknowledged"):
        await record_event(
            session,
            case_id,
            EVENT_RUNG_FIRED,
            {"level": level, "outcome": "skipped", "reason": case.state},
            actor_user_id=actor_user_id,
            now=moment,
        )
        return RungFired(case_id, level, "unknown", skipped_reason=case.state)

    rungs = {
        r.level: r
        for r in await chain_for(
            session, department_id=case.department_id, severity=case.severity
        )
    }
    rung = rungs.get(level)
    if rung is None:
        await record_event(
            session,
            case_id,
            EVENT_RUNG_FIRED,
            {"level": level, "outcome": "skipped", "reason": "rung_not_configured"},
            actor_user_id=actor_user_id,
            now=moment,
        )
        return RungFired(case_id, level, "unknown", skipped_reason="not_configured")

    user_id, patient_id, resolution = await _target_recipient(
        session, case_id, rung, at=moment
    )

    # Rung 0 assigns the case as well as notifying: it is the first contact,
    # and the owner it resolves is who the case belongs to from now on.
    if level == 0 and rung.target_type in ("owner", "roster_on_duty"):
        assigned = await assign_resolved_owner(
            session, case_id, at=moment, actor_user_id=actor_user_id
        )
        user_id = assigned.user_id
        resolution = assigned

    # 4.6: a patient rung that cannot go to the patient goes to the unit head
    # instead, rather than being dropped.
    template_key = TEMPLATE_FOR_TARGET[rung.target_type]
    if level == 0 and rung.target_type in ("owner", "roster_on_duty"):
        template_key = TEMPLATE_RUNG_ZERO

    redirected = False
    if rung.target_type == "patient":
        gate = await notify.patient_contact_gate(session, case_id)
        if not gate.allowed and gate.redirect_to_unit_head:
            redirected = True

    context = await _render_context(session, case_id, rung, moment)
    locale = await _patient_locale(session, case_id) if patient_id else "en"

    fired = RungFired(case_id, level, rung.target_type)

    if redirected:
        # Record the patient suppression, then notify the unit head instead.
        fired.dispatches.append(
            await notify.dispatch(
                session,
                case_id=case_id,
                template_key=TEMPLATE_FOR_TARGET["patient"],
                channel="sms",
                patient_id=patient_id,
                context=context,
                severity=case.severity,
                escalation_level=level,
                locale=locale,
                at=moment,
                actor_user_id=actor_user_id,
            )
        )
        unit_head, _, _ = await _target_recipient(
            session,
            case_id,
            Rung(level, "unit_head", rung.delay_minutes, rung.channels, rung.severity),
            at=moment,
        )
        for channel in rung.channels or ["in_app"]:
            fired.dispatches.append(
                await notify.dispatch(
                    session,
                    case_id=case_id,
                    template_key=TEMPLATE_FOR_TARGET["unit_head"],
                    channel=channel,
                    user_id=unit_head,
                    context=context,
                    severity=case.severity,
                    escalation_level=level,
                    at=moment,
                    actor_user_id=actor_user_id,
                )
            )
    else:
        for channel in rung.channels or ["in_app"]:
            fired.dispatches.append(
                await notify.dispatch(
                    session,
                    case_id=case_id,
                    template_key=template_key,
                    channel=channel,
                    user_id=user_id,
                    patient_id=patient_id,
                    context=context,
                    severity=case.severity,
                    escalation_level=level,
                    locale=locale,
                    at=moment,
                    actor_user_id=actor_user_id,
                )
            )

    await record_event(
        session,
        case_id,
        EVENT_RUNG_FIRED,
        {
            "level": level,
            "target": rung.target_type,
            "channels": rung.channels,
            "timer_id": str(timer_id) if timer_id else None,
            "recipient_user_id": str(user_id) if user_id else None,
            "recipient_patient_id": str(patient_id) if patient_id else None,
            "redirected_to_unit_head": redirected,
            "owner_resolution_step": resolution.step if resolution else None,
            "owner_resolution_level": resolution.level if resolution else None,
            "outcome": "fired",
            "dispatches": [
                {
                    "channel": d.channel,
                    "status": d.status,
                    "suppression_reason": d.suppression_reason,
                }
                for d in fired.dispatches
            ],
        },
        actor_user_id=actor_user_id,
        now=moment,
    )

    if resolution is not None and resolution.step == STEP_ADMIN_FALLBACK:
        log.warning(
            "escalation_reached_admin_fallback",
            case_id=str(case_id),
            level=level,
            unresolved=resolution.unresolved,
        )

    return fired


async def _patient_locale(session: AsyncSession, case_id: uuid.UUID) -> str:
    row = (
        await session.execute(
            text(
                "SELECT p.preferred_language FROM pending_cases pc "
                "  JOIN patients p ON p.id = pc.patient_id WHERE pc.id = :c"
            ),
            {"c": str(case_id)},
        )
    ).first()
    return str(row.preferred_language) if row else "en"


@dataclass
class Acknowledged:
    case_id: uuid.UUID
    cancelled_timer_ids: list[uuid.UUID] = field(default_factory=list)
    already_acknowledged: bool = False


async def acknowledge_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    acknowledged_by: uuid.UUID,
    note: str | None = None,
    at: dt.datetime | None = None,
) -> Acknowledged:
    """*"Acknowledgement cancels all remaining rungs in one transaction."*

    Exit Gate 4's second clause: *"Acknowledging at **any** rung stops
    everything."* The cancel is one UPDATE over the case's pending
    ``case_escalation`` timers, taking the same row locks the fire handler
    takes — so acknowledging while a rung is mid-flight is safe in both
    orderings. If the handler commits first, that rung fired and the rest are
    cancelled; if the acknowledgement commits first, the handler's claim finds
    ``status = 'cancelled'`` and declines.

    Scope note: this is the *acknowledgement* Phase 4 needs to stop the ladder.
    The full closure workflow — reason codes, outcomes, reopening — is Phase
    5.3 and is deliberately not built here.
    """
    moment = at or dt.datetime.now(dt.UTC)

    row = (
        await session.execute(
            text(
                "SELECT id, state, acknowledged_at FROM pending_cases "
                " WHERE id = :c AND deleted_at IS NULL FOR UPDATE"
            ),
            {"c": str(case_id)},
        )
    ).first()
    if row is None:
        raise LookupError(str(case_id))

    if row.acknowledged_at is not None:
        return Acknowledged(case_id=case_id, already_acknowledged=True)

    await session.execute(
        text(
            "UPDATE pending_cases "
            "   SET state = 'acknowledged', acknowledged_at = :t, "
            "       updated_at = now(), updated_by = :a "
            " WHERE id = :c AND acknowledged_at IS NULL"
        ),
        {"c": str(case_id), "t": moment, "a": str(acknowledged_by)},
    )

    cancelled = await cancel_case_timers(
        session, case_id, only_types=(ESCALATION_TIMER_TYPE,)
    )

    await record_event(
        session,
        case_id,
        EVENT_CASE_ACKNOWLEDGED,
        {
            "acknowledged_by": str(acknowledged_by),
            "note": note,
            "cancelled_timer_ids": [str(t) for t in cancelled],
        },
        actor_user_id=acknowledged_by,
        now=moment,
    )
    if cancelled:
        await record_event(
            session,
            case_id,
            EVENT_LADDER_CANCELLED,
            {"reason": "acknowledged", "cancelled": len(cancelled)},
            actor_user_id=acknowledged_by,
            now=moment,
        )

    return Acknowledged(case_id=case_id, cancelled_timer_ids=cancelled)


def is_critical_ladder(severity: str | None) -> bool:
    return severity == SEVERITY_CRITICAL
