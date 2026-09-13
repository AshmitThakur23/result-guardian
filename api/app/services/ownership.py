"""Owner resolution and reassignment. Phase 4.2.

    resolve_owner(case):
      1. contract.responsible_doctor → available? → use
      2. absence.delegate_user_id    → available? → use
      3. duty_roster primary for department at now() → use
      4. duty_roster backup → use
      5. department unit_head → use
      6. admin fallback + high-priority system alert

Six hops, in that order, and **every hop writes a ``case_event`` with the
reason** — the plan asks for that explicitly and it is the difference between
"the system notified someone" and "the system can tell you why it notified
*that* person, six months later".

Two properties matter more than the list:

**It never returns nobody.** Step 6 is an admin fallback precisely so the
function has no "could not resolve" branch. A flag routed to nobody is silence,
and silence is the failure this product exists to prevent. If even the admin
fallback finds no one, that is recorded as a high-priority system alert rather
than swallowed — the case still has its severity, its timers and its events.

**Falling through is measured, not just survived.** [ADR 0004] requires it:

    Metric — count of owner resolutions that fell through to step 5 or 6, per
    department. A rising number is the measurable signature of a roster going
    stale.

So the resolution *level* is recorded on every event, and
``fallthrough_metrics`` reads them back. A roster nobody maintains degrades to
"the unit head gets it" **and says so**, rather than degrading quietly.

No AI, no NODE B. Availability is a date comparison and a table lookup.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.lab_flags import record_event

EVENT_OWNER_RESOLVED = "owner_resolved"
EVENT_CASE_REASSIGNED = "case_reassigned"
EVENT_OWNER_UNRESOLVABLE = "owner_unresolvable"

# The six steps, named so an event payload is readable without the code.
STEP_ASSIGNED_OWNER = "assigned_current_owner"
STEP_CONTRACT_DOCTOR = "contract_responsible_doctor"
STEP_DELEGATE = "absence_delegate"
STEP_ROSTER_PRIMARY = "duty_roster_primary"
STEP_ROSTER_BACKUP = "duty_roster_backup"
STEP_UNIT_HEAD = "department_unit_head"
STEP_ADMIN_FALLBACK = "admin_fallback"

RESOLUTION_STEPS = (
    STEP_ASSIGNED_OWNER,
    STEP_CONTRACT_DOCTOR,
    STEP_DELEGATE,
    STEP_ROSTER_PRIMARY,
    STEP_ROSTER_BACKUP,
    STEP_UNIT_HEAD,
    STEP_ADMIN_FALLBACK,
)

# ADR 0004's metric: "resolutions that fell through to step 5 or 6".
STALE_ROSTER_SIGNAL_STEPS = (STEP_UNIT_HEAD, STEP_ADMIN_FALLBACK)


class CaseNotFoundError(LookupError):
    """No such case. The router turns this into a 404."""


class ReassignmentReasonRequiredError(ValueError):
    """Phase 4.2 makes the reason mandatory. Router -> 422."""


@dataclass(frozen=True)
class OwnerResolution:
    """Who owns the case now, and how that was decided."""

    user_id: uuid.UUID | None
    step: str
    level: int
    """1-6, matching the plan's numbering."""
    reason: str
    unresolved: bool = False
    """True only when even the admin fallback found nobody."""

    @property
    def is_fallthrough(self) -> bool:
        """Did this resolution signal a possibly-stale roster? (ADR 0004)"""
        return self.step in STALE_ROSTER_SIGNAL_STEPS


async def is_available(
    session: AsyncSession, user_id: uuid.UUID | None, at: dt.datetime
) -> bool:
    """Is this user someone the system may route work to right now?

    Two independent reasons not to be: the account is inactive or soft-deleted
    (they left), or an absence row covers ``at`` (they are away). A null
    ``ends_at`` means indefinite, which is what "resigned" needs — an absence
    with an end date would put a resigned doctor back on duty by arithmetic.
    """
    if user_id is None:
        return False

    row = (
        await session.execute(
            text(
                "SELECT u.id "
                "  FROM users u "
                " WHERE u.id = :u AND u.is_active AND u.deleted_at IS NULL "
                "   AND NOT EXISTS ( "
                "         SELECT 1 FROM user_absences a "
                "          WHERE a.user_id = u.id AND a.deleted_at IS NULL "
                "            AND a.starts_at <= :at "
                "            AND (a.ends_at IS NULL OR a.ends_at > :at)) "
            ),
            {"u": str(user_id), "at": at},
        )
    ).first()
    return row is not None


async def _delegate_for(
    session: AsyncSession, user_id: uuid.UUID, at: dt.datetime
) -> uuid.UUID | None:
    """Step 2. Who is covering for an absent user?

    Ordered by ``starts_at DESC`` so the most recently opened absence wins when
    two overlap — a doctor who goes on leave during a training block is covered
    by the leave delegate, which is the later fact about them.
    """
    row = (
        await session.execute(
            text(
                "SELECT delegate_user_id FROM user_absences "
                " WHERE user_id = :u AND deleted_at IS NULL "
                "   AND delegate_user_id IS NOT NULL "
                "   AND starts_at <= :at AND (ends_at IS NULL OR ends_at > :at) "
                " ORDER BY starts_at DESC, id "
                " LIMIT 1"
            ),
            {"u": str(user_id), "at": at},
        )
    ).first()
    return row.delegate_user_id if row else None


async def _roster_on_duty(
    session: AsyncSession,
    department_id: uuid.UUID | None,
    role_on_duty: str,
    at: dt.datetime,
) -> uuid.UUID | None:
    """Steps 3 and 4. Who is on shift in this department, in this role?

    Only returns someone who is *also* available: a roster row for a doctor who
    is simultaneously on leave is stale data, and honouring it is exactly the
    "confidently notify someone who left" failure ADR 0004 names.

    ``ORDER BY shift_start DESC, id`` so the answer is deterministic when two
    rows overlap — an unordered pick would route the same case to different
    people on different days.
    """
    if department_id is None:
        return None

    rows = (
        await session.execute(
            text(
                "SELECT r.user_id FROM duty_roster r "
                " WHERE r.department_id = :d AND r.deleted_at IS NULL "
                "   AND r.role_on_duty = :role "
                "   AND r.shift_start <= :at AND r.shift_end > :at "
                " ORDER BY r.shift_start DESC, r.id"
            ),
            {"d": str(department_id), "role": role_on_duty, "at": at},
        )
    ).all()

    for row in rows:
        if await is_available(session, row.user_id, at):
            return uuid.UUID(str(row.user_id))
    return None


async def _admin_fallback(session: AsyncSession, at: dt.datetime) -> uuid.UUID | None:
    """Step 6. Any available admin. Ordered so the choice is reproducible."""
    rows = (
        await session.execute(
            text(
                "SELECT id FROM users "
                " WHERE role = 'admin' AND is_active AND deleted_at IS NULL "
                " ORDER BY employee_code, id"
            )
        )
    ).all()
    for row in rows:
        if await is_available(session, row.id, at):
            return uuid.UUID(str(row.id))
    return None


async def _case_context(session: AsyncSession, case_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text(
                "SELECT pc.id, pc.current_owner_id, pc.severity, pc.state, "
                "       pc.encounter_id, e.department_id, "
                "       dc.responsible_doctor_id, d.unit_head_user_id "
                "  FROM pending_cases pc "
                "  JOIN encounters e ON e.id = pc.encounter_id "
                "  LEFT JOIN discharge_contracts dc ON dc.id = pc.contract_id "
                "  LEFT JOIN departments d ON d.id = e.department_id "
                " WHERE pc.id = :c AND pc.deleted_at IS NULL"
            ),
            {"c": str(case_id)},
        )
    ).first()


async def resolve_owner(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    at: dt.datetime | None = None,
    actor_user_id: uuid.UUID | None = None,
    record: bool = True,
) -> OwnerResolution:
    """The plan's six-step fallthrough, in order.

    Writes one ``owner_resolved`` event naming the step and the reason, unless
    ``record=False`` (used by the read-only preview the dashboard needs, which
    must not append to an append-only log just to answer a question).

    **Does not itself write ``pending_cases.current_owner_id``.** Resolution and
    assignment are separate on purpose: the escalation ladder resolves at each
    rung to decide whom to notify, and that must not silently re-own a case a
    human deliberately reassigned. ``assign_resolved_owner`` does the write when
    a caller genuinely wants it.
    """
    moment = at or dt.datetime.now(dt.UTC)
    case = await _case_context(session, case_id)
    if case is None:
        raise CaseNotFoundError(str(case_id))

    resolution = await _resolve(session, case, moment)

    if record:
        await record_event(
            session,
            case_id,
            EVENT_OWNER_UNRESOLVABLE if resolution.unresolved else EVENT_OWNER_RESOLVED,
            {
                "user_id": str(resolution.user_id) if resolution.user_id else None,
                "step": resolution.step,
                "level": resolution.level,
                "reason": resolution.reason,
                # ADR 0004's staleness signal, on the event so the metric is a
                # query rather than a recomputation.
                "roster_fallthrough": resolution.is_fallthrough,
                "department_id": (
                    str(case.department_id) if case.department_id else None
                ),
            },
            actor_user_id=actor_user_id,
            now=moment,
        )

    return resolution


async def _resolve(
    session: AsyncSession, case: Any, at: dt.datetime
) -> OwnerResolution:
    """The ladder itself, kept separate so it is readable in one screen."""
    contract_doctor = getattr(case, "responsible_doctor_id", None)
    department_id = getattr(case, "department_id", None)
    unit_head = getattr(case, "unit_head_user_id", None)
    current_owner = getattr(case, "current_owner_id", None)

    # ── 0 · whoever the case is actually assigned to ──────────────
    # ⚠️ A step the plan's list does not have, and it is here because
    # without it `POST /api/cases/{id}/reassign` is cosmetic. The chain
    # starts at `contract.responsible_doctor`, so a case a human
    # deliberately handed to a colleague would keep escalating to the
    # original doctor -- measured: after a reassignment, rung 1 notified the
    # contract doctor, not the new owner.
    #
    # An explicit assignment is a human decision about *this* case and
    # outranks the automatic chain. It is still subject to availability, so a
    # reassignment to someone who then goes on leave falls through to the
    # rest of the ladder exactly as before.
    reassigned = current_owner is not None and str(current_owner) != str(
        contract_doctor or ""
    )
    if reassigned and await is_available(session, uuid.UUID(str(current_owner)), at):
        return OwnerResolution(
            user_id=uuid.UUID(str(current_owner)),
            step=STEP_ASSIGNED_OWNER,
            level=0,
            reason=(
                "the case was explicitly assigned to this user, which "
                "outranks the automatic chain"
            ),
        )

    # ── 1 · the doctor who signed the contract ────────────────────
    if await is_available(session, contract_doctor, at):
        return OwnerResolution(
            user_id=uuid.UUID(str(contract_doctor)),
            step=STEP_CONTRACT_DOCTOR,
            level=1,
            reason="responsible doctor on the discharge contract is available",
        )

    # ── 2 · their delegate ────────────────────────────────────────
    if contract_doctor is not None:
        delegate = await _delegate_for(session, uuid.UUID(str(contract_doctor)), at)
        if await is_available(session, delegate, at):
            assert delegate is not None
            return OwnerResolution(
                user_id=delegate,
                step=STEP_DELEGATE,
                level=2,
                reason=(
                    "responsible doctor is absent; their recorded delegate is "
                    "available"
                ),
            )

    # ── 3 · roster primary ────────────────────────────────────────
    primary = await _roster_on_duty(session, department_id, "primary", at)
    if primary is not None:
        return OwnerResolution(
            user_id=primary,
            step=STEP_ROSTER_PRIMARY,
            level=3,
            reason="on-duty primary from the department roster",
        )

    # ── 4 · roster backup ─────────────────────────────────────────
    backup = await _roster_on_duty(session, department_id, "backup", at)
    if backup is not None:
        return OwnerResolution(
            user_id=backup,
            step=STEP_ROSTER_BACKUP,
            level=4,
            reason="on-duty backup from the department roster",
        )

    # ── 5 · the unit head ─────────────────────────────────────────
    # ADR 0004's "graceful fallthrough": a stale roster degrades to the person
    # responsible for maintaining it, not to silence.
    if await is_available(session, unit_head, at):
        return OwnerResolution(
            user_id=uuid.UUID(str(unit_head)),
            step=STEP_UNIT_HEAD,
            level=5,
            reason=(
                "no available contract doctor, delegate or rostered cover; "
                "falling through to the department unit head"
            ),
        )

    # ── 6 · admin fallback ────────────────────────────────────────
    admin = await _admin_fallback(session, at)
    if admin is not None:
        return OwnerResolution(
            user_id=admin,
            step=STEP_ADMIN_FALLBACK,
            level=6,
            reason=(
                "nobody in the department chain is available; assigned to an "
                "administrator as a high-priority system alert"
            ),
        )

    # Nothing left. Recorded loudly rather than returned as a quiet None: the
    # case keeps its severity, its timers and its events, and the ladder will
    # try again at the next rung.
    return OwnerResolution(
        user_id=None,
        step=STEP_ADMIN_FALLBACK,
        level=6,
        reason=(
            "no available user at any step, including the admin fallback — "
            "this needs a human to look at the user directory"
        ),
        unresolved=True,
    )


async def assign_resolved_owner(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    at: dt.datetime | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> OwnerResolution:
    """Resolve **and** write the owner onto the case.

    Used when a case first becomes flagged. The write is guarded on the case
    not being closed or acknowledged, matching every other case transition in
    the codebase — re-owning a case somebody already dealt with would put it
    back in their queue.
    """
    moment = at or dt.datetime.now(dt.UTC)
    resolution = await resolve_owner(
        session, case_id, at=moment, actor_user_id=actor_user_id
    )

    if resolution.user_id is not None:
        await session.execute(
            text(
                "UPDATE pending_cases "
                "   SET current_owner_id = :u, updated_at = now(), updated_by = :a "
                " WHERE id = :c AND state NOT IN ('closed', 'acknowledged') "
                "   AND deleted_at IS NULL"
            ),
            {
                "c": str(case_id),
                "u": str(resolution.user_id),
                "a": str(actor_user_id) if actor_user_id else None,
            },
        )

    return resolution


@dataclass(frozen=True)
class Reassignment:
    case_id: uuid.UUID
    previous_owner_id: uuid.UUID | None
    new_owner_id: uuid.UUID
    reason: str


async def reassign_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    new_owner_id: uuid.UUID,
    reason: str,
    actor_user_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> Reassignment:
    """``POST /api/cases/{id}/reassign`` — manual reassign with a **mandatory
    reason**.

    The plan attaches a warning to this endpoint, and it is the whole design:

        **Reassignment does not reset the escalation clock**, otherwise it
        becomes a dodge.

    So this function touches ``pending_cases.current_owner_id`` and writes an
    event, and deliberately does **not** touch ``sla_timers``. A case handed
    round three doctors escalates on the schedule set when it was flagged.
    Passing it on is not progress.
    """
    if not reason or not reason.strip():
        raise ReassignmentReasonRequiredError(
            "a reassignment must say why — an unexplained handover is the thing "
            "this endpoint exists to make visible"
        )

    moment = now or dt.datetime.now(dt.UTC)
    case = await _case_context(session, case_id)
    if case is None:
        raise CaseNotFoundError(str(case_id))

    previous = getattr(case, "current_owner_id", None)

    await session.execute(
        text(
            "UPDATE pending_cases "
            "   SET current_owner_id = :u, updated_at = now(), updated_by = :a "
            " WHERE id = :c AND deleted_at IS NULL"
        ),
        {
            "c": str(case_id),
            "u": str(new_owner_id),
            "a": str(actor_user_id) if actor_user_id else None,
        },
    )

    await record_event(
        session,
        case_id,
        EVENT_CASE_REASSIGNED,
        {
            "previous_owner_id": str(previous) if previous else None,
            "new_owner_id": str(new_owner_id),
            "reason": reason.strip(),
            # Stated in the event so an auditor reading the trail does not have
            # to know the rule.
            "escalation_clock_reset": False,
        },
        actor_user_id=actor_user_id,
        now=moment,
    )

    return Reassignment(
        case_id=case_id,
        previous_owner_id=uuid.UUID(str(previous)) if previous else None,
        new_owner_id=new_owner_id,
        reason=reason.strip(),
    )


# ── ADR 0004's staleness guards ───────────────────────────────────────


async def departments_with_stale_roster(
    session: AsyncSession, at: dt.datetime | None = None
) -> list[dict[str, object]]:
    """ADR 0004 guard 3: *"the admin and unit-head dashboards show a warning
    when a department has no active shift row covering now()."*

    Returns the data; Phase 5.4 renders the badge.
    """
    moment = at or dt.datetime.now(dt.UTC)
    rows = (
        await session.execute(
            text(
                "SELECT d.id, d.code, d.name, d.unit_head_user_id, "
                "       u.full_name AS unit_head_name "
                "  FROM departments d "
                "  LEFT JOIN users u ON u.id = d.unit_head_user_id "
                " WHERE d.active AND d.deleted_at IS NULL "
                "   AND NOT EXISTS ( "
                "         SELECT 1 FROM duty_roster r "
                "          WHERE r.department_id = d.id AND r.deleted_at IS NULL "
                "            AND r.shift_start <= :at AND r.shift_end > :at) "
                " ORDER BY d.code"
            ),
            {"at": moment},
        )
    ).all()
    return [
        {
            "department_id": str(r.id),
            "code": r.code,
            "name": r.name,
            "unit_head_user_id": (
                str(r.unit_head_user_id) if r.unit_head_user_id else None
            ),
            "unit_head_name": r.unit_head_name,
        }
        for r in rows
    ]


async def fallthrough_metrics(
    session: AsyncSession, *, since: dt.datetime | None = None
) -> list[dict[str, object]]:
    """ADR 0004 guard 4: *"count of owner resolutions that fell through to step
    5 or 6, per department. A rising number is the measurable signature of a
    roster going stale."*

    Read back out of ``case_events``, which is append-only — so the number
    cannot be quietly revised, only added to.
    """
    window = since or (dt.datetime.now(dt.UTC) - dt.timedelta(days=30))
    rows = (
        await session.execute(
            text(
                "SELECT COALESCE(ce.payload->>'department_id', 'unknown') AS dept, "
                "       count(*) AS total, "
                "       count(*) FILTER ( "
                "           WHERE ce.payload->>'roster_fallthrough' = 'true') "
                "         AS fell_through "
                "  FROM case_events ce "
                " WHERE ce.event_type = :ev AND ce.occurred_at >= :since "
                " GROUP BY 1 ORDER BY 1"
            ),
            {"ev": EVENT_OWNER_RESOLVED, "since": window},
        )
    ).all()
    return [
        {
            "department_id": r.dept,
            "resolutions": int(r.total),
            "fell_through_to_unit_head_or_admin": int(r.fell_through),
        }
        for r in rows
    ]
