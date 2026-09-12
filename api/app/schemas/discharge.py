"""Discharge gate response schemas. Phase 1.3.

Read-only shapes. Nothing here accepts a client-supplied readiness value --
the gate is derived from the database on every call, never asserted by the
caller.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class BlockingOrder(BaseModel):
    """An outstanding investigation with no owner and no expected-by date.

    Carries exactly what the Phase 1.4 gate screen needs to let a doctor
    create a contract without a second round trip -- the build plan lists
    test_name, ordered_at, status, expected TAT and the suggested expected_by.
    """

    model_config = ConfigDict(from_attributes=True)

    order_id: uuid.UUID
    test_code: str
    test_name: str
    category: str
    status: str
    ordered_at: dt.datetime
    expected_tat_hours: Decimal | None = None
    suggested_expected_by: dt.datetime | None = Field(
        default=None,
        description=(
            "ordered_at + expected_tat_hours. Null when the order carries no "
            "TAT, in which case the doctor must choose a date themselves."
        ),
    )


class ContractedOrder(BaseModel):
    """An outstanding investigation that already has an owner and a deadline.

    These do not block: the gate exists to force ownership, and ownership has
    been given. Exit Gate 1 requires exactly this -- *"blocked -> assign owners
    and dates -> discharge succeeds"*.
    """

    model_config = ConfigDict(from_attributes=True)

    order_id: uuid.UUID
    test_code: str
    test_name: str
    status: str
    contract_id: uuid.UUID
    responsible_doctor_id: uuid.UUID
    expected_by: dt.datetime


class DischargeReadiness(BaseModel):
    """Whether this encounter may be discharged, and what is outstanding."""

    encounter_id: uuid.UUID
    encounter_type: str
    gate_applies: bool = Field(
        description=(
            "False for encounter types outside ipd|emergency|daycare. ADR 0003 "
            "puts OPD out of scope for v1: those visits have no reliable "
            "closure event to hang the gate on, so the gate does not bind. "
            "blocking_orders is still reported for them, informationally."
        )
    )
    can_discharge: bool
    blocking_orders: list[BlockingOrder]
    already_contracted: list[ContractedOrder]
