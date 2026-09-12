"""Discharge readiness. Phase 1.3.

The safety property, computed from the database and nothing else.

RULE 1 says the gate must not depend on AI; it equally must not depend on the
client. Every call re-derives readiness from current PostgreSQL state. There is
no cache, no client-supplied ``can_discharge``, and no way for a caller to
assert readiness -- Phase 1.3's own spec for the discharge action says
*"re-runs readiness check server-side (never trust the client)"*, and this is
the function it re-runs.

Business logic only. No HTTP here; the router translates.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.discharge import DischargeContract
from app.db.models.encounters import GATED_ENCOUNTER_TYPES, Encounter
from app.db.models.orders import ORDER_STATUSES_NOT_BLOCKING, Order
from app.schemas.discharge import (
    BlockingOrder,
    ContractedOrder,
    DischargeReadiness,
)


class EncounterNotFoundError(LookupError):
    """No such encounter. The router turns this into a 404."""


def _suggested_expected_by(order: Order) -> dt.datetime | None:
    """ordered_at + TAT, the default the gate screen pre-fills.

    Null when the order carries no TAT -- the doctor then has to choose a date
    themselves rather than be handed a fabricated one.
    """
    if order.expected_tat_hours is None:
        return None
    return order.ordered_at + dt.timedelta(hours=float(order.expected_tat_hours))


async def get_discharge_readiness(
    session: AsyncSession, encounter_id: uuid.UUID
) -> DischargeReadiness:
    """Derive readiness for one encounter. Read-only.

    An order is **outstanding** when its status is not one of
    ``final | cancelled | rejected``. Outstanding orders split in two:

    * no discharge contract  -> ``blocking_orders``
    * has a discharge contract -> ``already_contracted``

    ``can_discharge`` is true when nothing is left in the first list. That
    split is what makes the gate passable at all: Exit Gate 1 requires
    *"blocked -> assign owners and dates -> discharge succeeds"*, so an order
    whose ownership has been assigned must stop blocking. Were blocking purely
    status-based, no encounter could ever be discharged until the lab returned
    every result, and the contract mechanism would be pointless.
    """
    encounter = (
        await session.execute(
            # Only the two columns needed -- no patient join, no full row.
            select(Encounter.id, Encounter.type).where(
                Encounter.id == encounter_id,
                Encounter.deleted_at.is_(None),
            )
        )
    ).first()

    if encounter is None:
        raise EncounterNotFoundError(str(encounter_id))

    _, encounter_type = encounter

    # One query: outstanding orders for this encounter, each with its contract
    # if it has one. Scoped to this encounter, so an order on another
    # encounter cannot influence the result.
    rows = (
        await session.execute(
            select(Order, DischargeContract)
            .outerjoin(
                DischargeContract,
                and_(
                    DischargeContract.order_id == Order.id,
                    DischargeContract.deleted_at.is_(None),
                ),
            )
            .where(
                Order.encounter_id == encounter_id,
                Order.deleted_at.is_(None),
                Order.status.notin_(ORDER_STATUSES_NOT_BLOCKING),
            )
            .order_by(Order.ordered_at)
        )
    ).all()

    blocking: list[BlockingOrder] = []
    contracted: list[ContractedOrder] = []

    for order, contract in rows:
        if contract is None:
            blocking.append(
                BlockingOrder(
                    order_id=order.id,
                    test_code=order.test_code,
                    test_name=order.test_name,
                    category=order.category,
                    status=order.status,
                    ordered_at=order.ordered_at,
                    expected_tat_hours=order.expected_tat_hours,
                    suggested_expected_by=_suggested_expected_by(order),
                )
            )
        else:
            contracted.append(
                ContractedOrder(
                    order_id=order.id,
                    test_code=order.test_code,
                    test_name=order.test_name,
                    status=order.status,
                    contract_id=contract.id,
                    responsible_doctor_id=contract.responsible_doctor_id,
                    expected_by=contract.expected_by,
                )
            )

    # ADR 0003: the gate binds to ipd | emergency | daycare. OPD has no
    # reliable visit-closure event to hang it on, so the gate does not apply
    # there -- but the outstanding orders are still reported rather than
    # hidden, because the data is true regardless of whether the gate binds.
    gate_applies = encounter_type in GATED_ENCOUNTER_TYPES

    return DischargeReadiness(
        encounter_id=encounter_id,
        encounter_type=encounter_type,
        gate_applies=gate_applies,
        can_discharge=(not gate_applies) or not blocking,
        blocking_orders=blocking,
        already_contracted=contracted,
    )
