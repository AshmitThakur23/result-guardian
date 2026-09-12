"""The discharge override. Phase 1.3, the emergency path.

    requires reason_code from a fixed list ... requires free-text reason >= 20
    chars ... **still creates a pending_case, flagged to unit head
    immediately** ... appears on an "overrides" audit report

The emphasis is the point. This is an override of the gate, not a way round
it. A patient who leaves against advice, dies, or is transferred still has an
outstanding investigation, and somebody still has to look at the result. So
the override does everything the normal discharge does *plus* records why the
gate was bypassed -- it never drops the investigation from tracking.

``discharge_overrides.order_id`` is NOT NULL, so one override row is written
per uncontracted order rather than one per encounter. That is the schema
answering the question, not an invention.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.discharge import DischargeContract
from app.db.models.encounters import GATED_ENCOUNTER_TYPES, Encounter
from app.db.models.orders import ORDER_STATUSES_NOT_BLOCKING, Order
from app.db.models.organisation import Department
from app.db.types import uuid7
from app.schemas.discharge import (
    CreatedOverride,
    DischargeOverrideRequest,
    DischargeOverrideResult,
    OpenedCase,
)
from app.services.discharge_action import (
    EVENT_CASE_OPENED,
    SLA_TIMER_QUEUE,
    TIMER_TYPE_RESULT_DUE,
    EncounterNotDischargeableError,
)
from app.services.discharge_readiness import EncounterNotFoundError

# A distinct event type so the Phase 5 timeline and the Phase 5.4 overrides
# report can tell a bypassed gate from a normal one at a glance.
EVENT_CASE_OPENED_VIA_OVERRIDE = "case_opened_via_override"


class NoUnitHeadError(Exception):
    """The department has no unit head, so the case would have no owner.

    Refused rather than accepted, because *"still creates a pending_case,
    flagged to unit head immediately"* cannot be honoured without one, and an
    unowned flagged case is precisely the silent failure this product exists
    to prevent. Router -> 409.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class NothingToOverrideError(Exception):
    """No uncontracted outstanding order. Router -> 409.

    The gate is not blocking, so there is nothing to bypass -- the normal
    discharge applies. Refusing keeps the Phase 5.4 overrides report
    meaningful: every row in it is a real bypass.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


async def override_discharge(
    session: AsyncSession,
    encounter_id: uuid.UUID,
    request: DischargeOverrideRequest,
) -> DischargeOverrideResult:
    """Discharge despite the gate, recording why and flagging the fallout.

    Same locking discipline as the normal discharge: the encounter row is
    taken ``FOR UPDATE`` first, so concurrent attempts serialise and a repeat
    request meets ``status = 'discharged'``.
    """
    locked = (
        await session.execute(
            select(
                Encounter.id,
                Encounter.type,
                Encounter.status,
                Encounter.department_id,
            )
            .where(Encounter.id == encounter_id, Encounter.deleted_at.is_(None))
            .with_for_update()
        )
    ).first()

    if locked is None:
        raise EncounterNotFoundError(str(encounter_id))

    _, encounter_type, encounter_status, department_id = locked

    if encounter_type not in GATED_ENCOUNTER_TYPES:
        raise EncounterNotDischargeableError(
            f"Encounter type '{encounter_type}' has no discharge gate, so there "
            "is nothing to override. See ADR 0003."
        )

    if encounter_status != "active":
        # Same idempotency rule as the discharge action: refuse rather than
        # write a second override, a second case and a second event.
        raise EncounterNotDischargeableError(
            f"Encounter is already '{encounter_status}' and cannot be "
            "discharged again."
        )

    # ── who will own the flagged cases ────────────────────────────
    unit_head_id: uuid.UUID | None = None
    if department_id is not None:
        unit_head_id = (
            await session.execute(
                select(Department.unit_head_user_id).where(
                    Department.id == department_id,
                    Department.deleted_at.is_(None),
                )
            )
        ).scalar()

    if unit_head_id is None:
        raise NoUnitHeadError(
            "This encounter's department has no unit head, so an overridden "
            "investigation would have no owner. Assign a unit head "
            "(departments.unit_head_user_id) before overriding the gate."
        )

    # ── what is outstanding, and what already has an owner ────────
    rows = (
        await session.execute(
            select(Order, DischargeContract)
            .outerjoin(
                DischargeContract,
                (DischargeContract.order_id == Order.id)
                & (DischargeContract.deleted_at.is_(None)),
            )
            .where(
                Order.encounter_id == encounter_id,
                Order.deleted_at.is_(None),
                Order.status.notin_(ORDER_STATUSES_NOT_BLOCKING),
            )
            .with_for_update(of=Order)
            .order_by(Order.ordered_at)
        )
    ).all()

    uncontracted = [(o, c) for o, c in rows if c is None]
    contracted = [(o, c) for o, c in rows if c is not None]

    if not uncontracted:
        raise NothingToOverrideError(
            "No outstanding investigation lacks a discharge contract. Use "
            "POST /discharge instead; the gate is not blocking this encounter."
        )

    now = dt.datetime.now(dt.UTC)
    overridden: list[CreatedOverride] = []
    opened: list[OpenedCase] = []

    await session.execute(
        text(
            "UPDATE encounters SET status = 'discharged', discharged_at = :t, "
            "updated_at = :t, updated_by = :a WHERE id = :i"
        ),
        {"t": now, "a": request.overridden_by, "i": encounter_id},
    )

    # ── the bypassed orders: override row + flagged case + event ──
    for order, _ in uncontracted:
        override_id = uuid7()
        case_id = uuid7()

        await session.execute(
            text(
                "INSERT INTO discharge_overrides (id, encounter_id, order_id, "
                "reason_code, reason_text, overridden_by, approved_by, "
                "created_by, updated_by) "
                "VALUES (:id, :e, :o, :rc, :rt, :by, :ap, :by, :by)"
            ),
            {
                "id": override_id,
                "e": encounter_id,
                "o": order.id,
                "rc": request.reason_code,
                "rt": request.reason_text,
                "by": request.overridden_by,
                "ap": request.approved_by,
            },
        )

        # flagged, not awaiting_result: nobody accepted ownership, so this
        # needs a human now rather than at the deadline. flagged_at starts the
        # Phase 4 escalation clock.
        await session.execute(
            text(
                "INSERT INTO pending_cases (id, order_id, encounter_id, "
                "patient_id, contract_id, current_owner_id, state, opened_at, "
                "flagged_at, created_by, updated_by) "
                "VALUES (:id, :o, :e, :p, NULL, :owner, 'flagged', :t, :t, "
                ":by, :by)"
            ),
            {
                "id": case_id,
                "o": order.id,
                "e": encounter_id,
                "p": order.patient_id,
                "owner": unit_head_id,
                "t": now,
                "by": request.overridden_by,
            },
        )

        await session.execute(
            text(
                "INSERT INTO case_events (id, case_id, event_type, "
                "actor_user_id, payload, occurred_at, created_by, updated_by) "
                "VALUES (:id, :case, :type, :by, CAST(:payload AS jsonb), :t, "
                ":by, :by)"
            ),
            {
                "id": uuid7(),
                "case": case_id,
                "type": EVENT_CASE_OPENED_VIA_OVERRIDE,
                "by": request.overridden_by,
                "payload": json.dumps(
                    {
                        "encounter_id": str(encounter_id),
                        "order_id": str(order.id),
                        "test_name": order.test_name,
                        "override_id": str(override_id),
                        "reason_code": request.reason_code,
                        "reason_text": request.reason_text,
                        "overridden_by": str(request.overridden_by),
                        "approved_by": (
                            str(request.approved_by) if request.approved_by else None
                        ),
                        "flagged_to_unit_head": str(unit_head_id),
                    }
                ),
                "t": now,
            },
        )

        overridden.append(
            CreatedOverride(
                override_id=override_id,
                order_id=order.id,
                case_id=case_id,
                flagged_owner_id=unit_head_id,
            )
        )

    # ── contracted orders keep their owner and their timer ────────
    # An override covers only what was bypassed. An investigation somebody
    # already accepted should not lose its deadline because a different one
    # on the same encounter was overridden.
    for order, contract in contracted:
        case_id = uuid7()
        await session.execute(
            text(
                "INSERT INTO pending_cases (id, order_id, encounter_id, "
                "patient_id, contract_id, current_owner_id, state, opened_at, "
                "created_by, updated_by) "
                "VALUES (:id, :o, :e, :p, :c, :owner, 'awaiting_result', :t, "
                ":by, :by)"
            ),
            {
                "id": case_id,
                "o": order.id,
                "e": encounter_id,
                "p": order.patient_id,
                "c": contract.id,
                "owner": contract.responsible_doctor_id,
                "t": now,
                "by": request.overridden_by,
            },
        )
        await session.execute(
            text(
                "INSERT INTO case_events (id, case_id, event_type, "
                "actor_user_id, payload, occurred_at, created_by, updated_by) "
                "VALUES (:id, :case, :type, :by, CAST(:payload AS jsonb), :t, "
                ":by, :by)"
            ),
            {
                "id": uuid7(),
                "case": case_id,
                "type": EVENT_CASE_OPENED,
                "by": request.overridden_by,
                "payload": json.dumps(
                    {
                        "encounter_id": str(encounter_id),
                        "order_id": str(order.id),
                        "contract_id": str(contract.id),
                        "expected_by": contract.expected_by.isoformat(),
                        "reason": "discharge_overridden_on_this_encounter",
                    }
                ),
                "t": now,
            },
        )

        delay_seconds = max(0, int((contract.expected_by - now).total_seconds()))
        msg_id = (
            await session.execute(
                text(
                    "SELECT pgmq.send(:q, CAST(:payload AS jsonb), "
                    "CAST(:d AS integer))"
                ),
                {
                    "q": SLA_TIMER_QUEUE,
                    "payload": json.dumps(
                        {
                            "timer_type": TIMER_TYPE_RESULT_DUE,
                            "case_id": str(case_id),
                            "order_id": str(order.id),
                            "encounter_id": str(encounter_id),
                            "contract_id": str(contract.id),
                            "fire_at": contract.expected_by.isoformat(),
                        }
                    ),
                    "d": delay_seconds,
                },
            )
        ).scalar()

        opened.append(
            OpenedCase(
                case_id=case_id,
                order_id=order.id,
                contract_id=contract.id,
                current_owner_id=contract.responsible_doctor_id,
                expected_by=contract.expected_by,
                timer_msg_id=int(msg_id or 0),
            )
        )

    # One commit for the encounter, every override, every case and every
    # event. A failure anywhere leaves the encounter active and nothing
    # written -- a half-recorded override would be worse than none.
    await session.commit()

    return DischargeOverrideResult(
        encounter_id=encounter_id,
        status="discharged",
        discharged_at=now,
        reason_code=request.reason_code,
        unit_head_id=unit_head_id,
        overridden=overridden,
        opened_cases=opened,
    )
