"""Discharge contract creation. Phase 1.3.

Bulk, atomic, all-or-nothing. The build plan's validations are exactly three:
*"responsible doctor is active; expected_by is future; expected_by <= 30
days"*. The rest of the checks here are structural -- the order must exist, on
this encounter, and still be outstanding -- not invented clinical policy.

Two layers guard against a duplicate contract, and both are needed:

1. a SELECT before writing, so a normal duplicate produces a clear error; and
2. ``UNIQUE (order_id)`` in the database, which is the only thing that holds
   when two requests race. A check-then-insert cannot be safe on its own.

Business logic only; the router translates outcomes into HTTP.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.discharge import DischargeContract
from app.db.models.encounters import GATED_ENCOUNTER_TYPES, Encounter
from app.db.models.orders import ORDER_STATUSES_NOT_BLOCKING, Order
from app.db.models.organisation import User
from app.db.types import uuid7
from app.schemas.discharge import (
    MAX_CONTRACT_HORIZON_DAYS,
    CreatedContract,
    DischargeContractRequest,
    DischargeContractsCreated,
)
from app.services.discharge_readiness import EncounterNotFoundError


@dataclass(frozen=True)
class Violation:
    """One reason a requested contract was refused, tied to its order."""

    order_id: uuid.UUID | None
    code: str
    detail: str


class ContractValidationError(Exception):
    """The batch is invalid. Nothing was written. Router -> 422.

    Carries *every* violation, not just the first: a doctor fixing a gate
    screen should see all of the problems at once.
    """

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        super().__init__(f"{len(violations)} contract violation(s)")


class ContractConflictError(Exception):
    """An order already has a contract. Router -> 409.

    Raised both by the pre-check and by the UNIQUE(order_id) constraint when a
    concurrent request wins the race.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


async def create_discharge_contracts(
    session: AsyncSession,
    encounter_id: uuid.UUID,
    requests: list[DischargeContractRequest],
    actor_user_id: uuid.UUID | None = None,
) -> DischargeContractsCreated:
    """Create every requested contract, or none of them.

    No ``pending_cases`` are opened here and no order status is touched. The
    build plan creates pending cases in the *discharge action*, inside its own
    transaction -- not at contract time. No revision row is written either:
    ``discharge_contract_revisions`` records *changes* to a contract, and a
    creation is not a change.
    """
    now = dt.datetime.now(dt.UTC)
    horizon = now + dt.timedelta(days=MAX_CONTRACT_HORIZON_DAYS)

    encounter = (
        await session.execute(
            select(Encounter.id, Encounter.type).where(
                Encounter.id == encounter_id,
                Encounter.deleted_at.is_(None),
            )
        )
    ).first()
    if encounter is None:
        raise EncounterNotFoundError(str(encounter_id))

    _, encounter_type = encounter

    violations: list[Violation] = []

    # ADR 0003: the gate binds to ipd | emergency | daycare. A contract on a
    # non-gated encounter would be an accountability record nothing ever acts
    # on -- no pending case, no timer, no escalation.
    if encounter_type not in GATED_ENCOUNTER_TYPES:
        violations.append(
            Violation(
                order_id=None,
                code="encounter_not_gated",
                detail=(
                    f"Encounter type '{encounter_type}' is outside the discharge "
                    f"gate ({', '.join(GATED_ENCOUNTER_TYPES)}). See ADR 0003."
                ),
            )
        )
        raise ContractValidationError(violations)

    # A batch that names the same order twice would race itself.
    order_ids = [r.order_id for r in requests]
    duplicates = {oid for oid in order_ids if order_ids.count(oid) > 1}
    for oid in sorted(duplicates, key=str):
        violations.append(
            Violation(
                order_id=oid,
                code="duplicate_in_request",
                detail="The same order appears more than once in this request.",
            )
        )

    # Three set-based lookups rather than per-item queries.
    orders = {
        o.id: o
        for o in (
            await session.execute(
                select(Order).where(
                    Order.id.in_(order_ids),
                    Order.encounter_id == encounter_id,
                    Order.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    doctors = {
        u.id: u
        for u in (
            await session.execute(
                select(User).where(
                    User.id.in_([r.responsible_doctor_id for r in requests]),
                    User.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    already_contracted = set(
        (
            await session.execute(
                select(DischargeContract.order_id).where(
                    DischargeContract.order_id.in_(order_ids),
                    DischargeContract.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    conflicts: list[str] = []

    for request in requests:
        order = orders.get(request.order_id)
        if order is None:
            # Covers both "no such order" and "belongs to another encounter".
            # Deliberately one message: telling an unauthenticated caller which
            # of the two it is leaks whether an order id exists.
            violations.append(
                Violation(
                    order_id=request.order_id,
                    code="order_not_on_encounter",
                    detail="Order does not exist on this encounter.",
                )
            )
        elif order.status in ORDER_STATUSES_NOT_BLOCKING:
            violations.append(
                Violation(
                    order_id=request.order_id,
                    code="order_not_outstanding",
                    detail=(
                        f"Order status '{order.status}' is already resolved; "
                        "there is nothing pending to take ownership of."
                    ),
                )
            )

        if request.order_id in already_contracted:
            conflicts.append(str(request.order_id))

        doctor = doctors.get(request.responsible_doctor_id)
        if doctor is None:
            violations.append(
                Violation(
                    order_id=request.order_id,
                    code="doctor_not_found",
                    detail="Responsible doctor does not exist.",
                )
            )
        elif not doctor.is_active:
            # The plan names this one explicitly. An inactive owner is how a
            # flag ends up routed to someone who has left.
            violations.append(
                Violation(
                    order_id=request.order_id,
                    code="doctor_not_active",
                    detail="Responsible doctor is not active.",
                )
            )

        if request.expected_by <= now:
            violations.append(
                Violation(
                    order_id=request.order_id,
                    code="expected_by_not_future",
                    detail="expected_by must be in the future.",
                )
            )
        elif request.expected_by > horizon:
            violations.append(
                Violation(
                    order_id=request.order_id,
                    code="expected_by_beyond_horizon",
                    detail=(
                        f"expected_by must be within {MAX_CONTRACT_HORIZON_DAYS} "
                        "days."
                    ),
                )
            )

    # Everything is validated before anything is written.
    if violations:
        raise ContractValidationError(violations)
    if conflicts:
        raise ContractConflictError(
            "A discharge contract already exists for order(s): "
            + ", ".join(sorted(conflicts))
        )

    session.add_all(
        [
            DischargeContract(
                id=uuid7(),
                encounter_id=encounter_id,
                order_id=request.order_id,
                responsible_doctor_id=request.responsible_doctor_id,
                expected_by=request.expected_by,
                note=request.note,
                created_by=actor_user_id,
                updated_by=actor_user_id,
            )
            for request in requests
        ]
    )

    try:
        # One transaction for the whole batch: a failure anywhere leaves zero
        # rows, which is what "all-or-nothing" has to mean under concurrency.
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # UNIQUE(order_id) is the backstop the pre-check cannot be: another
        # request contracted one of these orders between our SELECT and our
        # INSERT. Nothing of this batch survives.
        raise ContractConflictError(
            "A discharge contract for one of these orders was created "
            "concurrently. No contracts were created by this request."
        ) from exc

    created = (
        (
            await session.execute(
                select(DischargeContract).where(
                    DischargeContract.order_id.in_(order_ids),
                    DischargeContract.encounter_id == encounter_id,
                    DischargeContract.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    return DischargeContractsCreated(
        encounter_id=encounter_id,
        created=[
            CreatedContract(
                contract_id=c.id,
                order_id=c.order_id,
                encounter_id=c.encounter_id,
                responsible_doctor_id=c.responsible_doctor_id,
                expected_by=c.expected_by,
                note=c.note,
            )
            for c in created
        ],
    )
