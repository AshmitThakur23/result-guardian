"""The discharge action. Phase 1.3.

The moment the product exists for. The build plan specifies it precisely:

    re-runs readiness check server-side (**never trust the client**)
    returns **409** with the blocking list if unsatisfied
    inside one transaction: set encounters.discharged_at, status discharged,
    create pending_cases, enqueue SLA timers, write case_events

All five of those writes share **one** transaction, including the pgmq
enqueue. That is not incidental -- ADR 0001 chose a single Postgres precisely
so it would be possible:

    Creating a pending case and enqueueing its SLA timer happen in one
    transaction -- there is no window where the case exists and the timer does
    not. With an external queue that window exists, and a patient falls into
    it.

So there is deliberately no "commit the database, then best-effort enqueue"
step here. ``pgmq.send`` is just another statement in the transaction.
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
from app.db.types import uuid7
from app.schemas.discharge import DischargeResult, OpenedCase
from app.services.discharge_readiness import (
    EncounterNotFoundError,
    get_discharge_readiness,
)
from app.services.timers import SLA_TIMER_QUEUE, create_timer

# Phase 2.2: "Create on discharge: result_due at contract.expected_by".
TIMER_TYPE_RESULT_DUE = "result_due"

# Re-exported from app.services.timers, which now owns the queue name. Kept
# here because Phase 1 tests and callers import it from this module.
__all__ = ["SLA_TIMER_QUEUE", "TIMER_TYPE_RESULT_DUE", "discharge_encounter"]

# Phase 1.1's case_events. One per case opened.
EVENT_CASE_OPENED = "case_opened"


class EncounterNotDischargeableError(Exception):
    """Wrong encounter type, or already discharged. Router -> 409."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class DischargeBlockedError(Exception):
    """Outstanding investigations without contracts. Router -> 409.

    Carries the blocking list, which the build plan requires the 409 to
    include -- a bare refusal tells the doctor nothing about what to fix.
    """

    def __init__(self, blocking: list[dict[str, object]]) -> None:
        self.blocking = blocking
        super().__init__(f"{len(blocking)} blocking order(s)")


async def discharge_encounter(
    session: AsyncSession,
    encounter_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
) -> DischargeResult:
    """Discharge an encounter, opening a tracking case per outstanding order.

    Concurrency: the encounter row is taken ``FOR UPDATE`` before anything is
    read or written, so two simultaneous discharge attempts serialise and the
    second sees ``status = 'discharged'``. The readiness re-check happens
    *inside* that lock, against the database, never against anything the
    client supplied.
    """
    # ── the serialisation point ───────────────────────────────────
    # Lock first, then read. Taking this lock before the readiness check is
    # what makes the check meaningful: without it, two requests could both
    # read "ready" and both proceed.
    locked = (
        await session.execute(
            select(Encounter.id, Encounter.type, Encounter.status)
            .where(Encounter.id == encounter_id, Encounter.deleted_at.is_(None))
            .with_for_update()
        )
    ).first()

    if locked is None:
        raise EncounterNotFoundError(str(encounter_id))

    _, encounter_type, encounter_status = locked

    if encounter_type not in GATED_ENCOUNTER_TYPES:
        # ADR 0003. OPD has no reliable visit-closure event to hang the gate
        # on, so there is no discharge action for it in v1.
        raise EncounterNotDischargeableError(
            f"Encounter type '{encounter_type}' has no discharge gate "
            f"({', '.join(GATED_ENCOUNTER_TYPES)}). See ADR 0003."
        )

    if encounter_status != "active":
        # Idempotency: a second POST must not open a second set of cases,
        # timers and events. Refuse loudly instead.
        raise EncounterNotDischargeableError(
            f"Encounter is already '{encounter_status}' and cannot be discharged "
            "again."
        )

    # ── the re-check, inside the lock, from the database ──────────
    readiness = await get_discharge_readiness(session, encounter_id)
    if readiness.blocking_orders:
        raise DischargeBlockedError(
            [
                {
                    "order_id": str(o.order_id),
                    "test_name": o.test_name,
                    "status": o.status,
                }
                for o in readiness.blocking_orders
            ]
        )

    # Lock the outstanding orders too, so their status or contract cannot
    # change between this check and the commit.
    contract_rows = (
        await session.execute(
            select(Order, DischargeContract)
            .join(DischargeContract, DischargeContract.order_id == Order.id)
            .where(
                Order.encounter_id == encounter_id,
                Order.deleted_at.is_(None),
                Order.status.notin_(ORDER_STATUSES_NOT_BLOCKING),
                DischargeContract.deleted_at.is_(None),
            )
            .with_for_update(of=Order)
            .order_by(Order.ordered_at)
        )
    ).all()

    now = dt.datetime.now(dt.UTC)
    opened: list[OpenedCase] = []

    # ── the five writes, one transaction ──────────────────────────
    await session.execute(
        text(
            "UPDATE encounters SET status = 'discharged', discharged_at = :t, "
            "updated_at = :t, updated_by = :a WHERE id = :i"
        ),
        {"t": now, "a": actor_user_id, "i": encounter_id},
    )

    for order, contract in contract_rows:
        case_id = uuid7()

        await session.execute(
            text(
                "INSERT INTO pending_cases (id, order_id, encounter_id, patient_id, "
                "contract_id, current_owner_id, state, opened_at, created_by, "
                "updated_by) VALUES (:id, :o, :e, :p, :c, :owner, "
                "'awaiting_result', :t, :a, :a)"
            ),
            {
                "id": case_id,
                "o": order.id,
                "e": encounter_id,
                "p": order.patient_id,
                "c": contract.id,
                "owner": contract.responsible_doctor_id,
                "t": now,
                "a": actor_user_id,
            },
        )

        # Append-only, enforced by the 0003 trigger. Never updated afterwards.
        await session.execute(
            text(
                "INSERT INTO case_events (id, case_id, event_type, actor_user_id, "
                "payload, occurred_at, created_by, updated_by) "
                "VALUES (:id, :case, :type, :a, CAST(:payload AS jsonb), :t, :a, :a)"
            ),
            {
                "id": uuid7(),
                "case": case_id,
                "type": EVENT_CASE_OPENED,
                "a": actor_user_id,
                "payload": json.dumps(
                    {
                        "encounter_id": str(encounter_id),
                        "order_id": str(order.id),
                        "test_name": order.test_name,
                        "contract_id": str(contract.id),
                        "responsible_doctor_id": str(contract.responsible_doctor_id),
                        "expected_by": contract.expected_by.isoformat(),
                        "reason": "discharge_completed_with_pending_investigation",
                    }
                ),
                "t": now,
            },
        )

        # Phase 2.2: "Create on discharge: result_due at contract.expected_by."
        #
        # Phase 1.3 enqueued a bare wake-up here. It now goes through
        # create_timer, which writes the durable sla_timers row FIRST and only
        # then rings the doorbell -- still in this same transaction, so the
        # case, the timer, the wake-up and the event all commit together or
        # none of them do. That atomicity is the reason ADR 0001 put the queue
        # inside Postgres.
        #
        # Idempotent by construction: the timer's key is derived from
        # (case_id, result_due, expected_by), so a replay cannot produce a
        # second timer or a second wake-up. The discharge itself is already
        # refused on replay by the encounter row lock above; this is the
        # second line of defence, in the database rather than in this function.
        timer = await create_timer(
            session,
            case_id=case_id,
            timer_type=TIMER_TYPE_RESULT_DUE,
            fire_at=contract.expected_by,
            order_id=order.id,
            encounter_id=encounter_id,
            contract_id=contract.id,
            actor_user_id=actor_user_id,
            now=now,
        )
        msg_id = timer.pgmq_msg_id

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

    # One commit for all of it. Any failure above leaves the encounter active,
    # with no cases, no events and no queued timers.
    await session.commit()

    return DischargeResult(
        encounter_id=encounter_id,
        status="discharged",
        discharged_at=now,
        opened_cases=opened,
    )
