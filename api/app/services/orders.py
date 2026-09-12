"""Manual order creation. Phase 1.5.

*"Manual order creation (until HIS integration exists)."* A stand-in for the
Phase 9 HIS feed, and the first thing in the product that can add work to an
encounter the discharge gate has already looked at.

──────────────────────────────────────────────────────────────────────────
THE RACE, AND WHY THE FIX LIVES HERE
──────────────────────────────────────────────────────────────────────────

Phase 1.3's discharge action takes ``SELECT ... FOR UPDATE`` on the
**encounter row**, and only then re-derives readiness from the database. That
lock is the product's serialisation point, and it already makes two concurrent
discharges safe.

Adding an order is the other half of that race. Without a lock here, this
interleaving is possible:

    T1 (discharge)      lock encounter, readiness = clear
    T2 (create order)                                     INSERT order, COMMIT
    T1                  UPDATE status = discharged, COMMIT

leaving an encounter discharged with an outstanding, uncontracted order that
no one owns -- precisely the state the product exists to make impossible, and
reached without the gate ever being wrong.

The fix belongs here, not in the discharge algorithm, and it is one line of
intent: **take the same encounter lock before inserting.** Postgres then
serialises the two transactions on that row, and both orderings are safe:

* discharge first -> this transaction blocks, then wakes to find
  ``status = 'discharged'`` and refuses the order.
* order first -> discharge blocks, then wakes and re-derives readiness
  *inside its own lock*, sees the new uncontracted order, and returns 409.

Note what is **not** needed: no change to ``discharge_action``, no advisory
lock, no serializable isolation, no retry loop. The discharge already re-reads
readiness under the lock it holds; it just needed the other writer to respect
the same lock.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.encounters import Encounter
from app.db.models.orders import ORDER_STATUSES_NOT_BLOCKING, Order
from app.db.models.organisation import User
from app.db.types import uuid7
from app.schemas.orders import OrderCreate, OrderCreated, OrderRow
from app.services.discharge_readiness import (
    EncounterNotFoundError,
    get_discharge_readiness,
)

# An order may only be added while the encounter is open. Every other status --
# discharged, lama, transferred, deceased -- means the episode of care is over
# and a new investigation against it cannot be tracked to a discharge.
ORDERABLE_ENCOUNTER_STATUS = "active"


class EncounterNotOrderableError(Exception):
    """The encounter is no longer open. Router -> 409."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class OrderValidationError(Exception):
    """A referenced row does not exist, or a value is unusable. Router -> 422."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class DuplicateExternalOrderError(Exception):
    """That lab accession number is already on another order. Router -> 409."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


async def create_manual_order(
    session: AsyncSession,
    encounter_id: uuid.UUID,
    payload: OrderCreate,
    actor_user_id: uuid.UUID | None = None,
) -> OrderCreated:
    """Add one investigation to an open encounter.

    Everything below happens in the caller's transaction, under a row lock on
    the encounter. See this module's docstring for why that lock is the whole
    safety argument.
    """
    # ── the serialisation point, same row the discharge locks ──────
    locked = (
        await session.execute(
            select(Encounter.id, Encounter.patient_id, Encounter.status)
            .where(Encounter.id == encounter_id, Encounter.deleted_at.is_(None))
            .with_for_update()
        )
    ).first()

    if locked is None:
        raise EncounterNotFoundError(str(encounter_id))

    _, patient_id, status = locked

    if status != ORDERABLE_ENCOUNTER_STATUS:
        # Reached either because the encounter was already closed, or because
        # a concurrent discharge committed while this transaction waited on
        # the lock above. Both are the same refusal, and both are correct.
        raise EncounterNotOrderableError(
            f"Encounter is '{status}' and no longer accepts new orders. "
            "An investigation ordered after discharge cannot be tracked by "
            "the discharge gate."
        )

    ordered_at = payload.ordered_at or dt.datetime.now(dt.UTC)
    if ordered_at > dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5):
        # Five minutes of slack for clock skew between a ward PC and NODE A.
        # Beyond that it is a typo, and it would push the gate's suggested
        # deadline out with it.
        raise OrderValidationError("ordered_at cannot be in the future")

    if payload.ordered_by_user_id is not None:
        exists = (
            await session.execute(
                select(User.id).where(
                    User.id == payload.ordered_by_user_id,
                    User.deleted_at.is_(None),
                )
            )
        ).first()
        if exists is None:
            raise OrderValidationError(f"No such user: {payload.ordered_by_user_id}")

    if payload.external_order_id is not None:
        # Not a database constraint -- external_order_id is indexed but not
        # unique, because two hospitals' accession spaces may legitimately
        # collide once HIS integration lands. Within one hospital it is a
        # duplicate, and Phase 7.5 matches results on it: two orders sharing
        # one accession is a wrong-patient hazard, so refuse it now.
        clash = (
            await session.execute(
                select(Order.id).where(
                    Order.external_order_id == payload.external_order_id,
                    Order.deleted_at.is_(None),
                )
            )
        ).first()
        if clash is not None:
            raise DuplicateExternalOrderError(
                f"external_order_id '{payload.external_order_id}' is already "
                "used by another order."
            )

    order = Order(
        id=uuid7(),
        encounter_id=encounter_id,
        # Denormalised from the encounter, never taken from the client: Phase
        # 7.5 scores candidate matches on this column, and a client-supplied
        # patient_id here would be a wrong-patient hazard by construction.
        patient_id=patient_id,
        test_code=payload.test_code,
        test_name=payload.test_name,
        category=payload.category,
        status=payload.status,
        ordered_at=ordered_at,
        expected_tat_hours=payload.expected_tat_hours,
        external_order_id=payload.external_order_id,
        ordered_by_user_id=payload.ordered_by_user_id,
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(order)
    # Make the INSERT visible to the readiness re-read below without ending
    # the transaction. The encounter lock is still held.
    await session.flush()

    # Re-derive the gate's answer from the database, inside the same lock, so
    # the caller is told what this order actually did to the encounter.
    readiness = await get_discharge_readiness(session, encounter_id)

    # Commit last, and only here: the encounter lock taken above is held for
    # the whole insert-and-recheck, and releasing it is the point at which a
    # waiting discharge is allowed to proceed and see this order.
    await session.commit()

    return OrderCreated(
        order=OrderRow(
            id=order.id,
            test_code=order.test_code,
            test_name=order.test_name,
            category=order.category,
            status=order.status,
            ordered_at=order.ordered_at,
            sample_collected_at=order.sample_collected_at,
            expected_tat_hours=order.expected_tat_hours,
            external_order_id=order.external_order_id,
            is_outstanding=order.status not in ORDER_STATUSES_NOT_BLOCKING,
            contract_id=None,
            responsible_doctor_id=None,
            responsible_doctor_name=None,
            expected_by=None,
        ),
        encounter_id=encounter_id,
        encounter_can_discharge=readiness.can_discharge,
        blocking_order_count=len(readiness.blocking_orders),
    )
