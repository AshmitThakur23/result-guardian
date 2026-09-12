"""Discharge gate response schemas. Phase 1.3.

Read-only shapes. Nothing here accepts a client-supplied readiness value --
the gate is derived from the database on every call, never asserted by the
caller.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


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


# Build plan 1.3: "expected_by <= 30 days". The boundary is inclusive.
MAX_CONTRACT_HORIZON_DAYS = 30


class DischargeContractRequest(BaseModel):
    """One contract to create: an order, an owner, and a deadline.

    ``expected_by`` is an aware datetime by requirement. A naive value would be
    interpreted against whatever the server's locale happens to be, and this
    field becomes the Phase 2 SLA deadline -- a silent timezone shift there is
    a deadline that fires at the wrong hour.
    """

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    responsible_doctor_id: uuid.UUID
    expected_by: AwareDatetime
    note: str | None = Field(default=None, max_length=2000)


class DischargeContractsCreate(BaseModel):
    """A bulk request. All of it is created, or none of it is."""

    model_config = ConfigDict(extra="forbid")

    contracts: list[DischargeContractRequest] = Field(min_length=1)


class CreatedContract(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contract_id: uuid.UUID
    order_id: uuid.UUID
    encounter_id: uuid.UUID
    responsible_doctor_id: uuid.UUID
    expected_by: dt.datetime
    note: str | None = None


class DischargeContractsCreated(BaseModel):
    encounter_id: uuid.UUID
    created: list[CreatedContract]


class OpenedCase(BaseModel):
    """A post-discharge tracking case, opened by the discharge action."""

    model_config = ConfigDict(from_attributes=True)

    case_id: uuid.UUID
    order_id: uuid.UUID
    contract_id: uuid.UUID
    current_owner_id: uuid.UUID
    expected_by: dt.datetime
    timer_msg_id: int = Field(
        description=(
            "pgmq message id of the result_due wake-up, enqueued in the same "
            "transaction as the case. Phase 2 adds the sla_timers table that "
            "carries the truth; this is only the wake-up."
        )
    )


class DischargeResult(BaseModel):
    encounter_id: uuid.UUID
    status: str
    discharged_at: dt.datetime
    opened_cases: list[OpenedCase]


# Build plan 1.3, the emergency path. Every value is a reason a discharge may
# legitimately bypass the gate; anything else is a bug.
OVERRIDE_REASON_CODES = (
    "patient_lama",
    "transfer_out",
    "deceased",
    "system_outage",
    "clinical_urgency",
)
MIN_OVERRIDE_REASON_CHARS = 20


class DischargeOverrideRequest(BaseModel):
    """Bypass the gate, on the record.

    ``overridden_by`` is supplied by the caller because authentication lands
    in Phase 5.1. Once it exists this comes from the session instead -- until
    then an override is only as trustworthy as the client, which is one more
    reason the row is immutable and audited.
    """

    model_config = ConfigDict(extra="forbid")

    reason_code: Literal[
        "patient_lama", "transfer_out", "deceased", "system_outage", "clinical_urgency"
    ]
    reason_text: str = Field(min_length=MIN_OVERRIDE_REASON_CHARS, max_length=4000)
    overridden_by: uuid.UUID
    # No approval workflow: the plan does not require one, and the column is
    # nullable. Recorded when a counter-signature happens to exist.
    approved_by: uuid.UUID | None = None

    @field_validator("reason_text")
    @classmethod
    def _meaningful_after_trimming(cls, value: str) -> str:
        # Mirrors the database CHECK, which trims first. Twenty spaces is not
        # an audit trail, and min_length alone would let it through.
        if len(value.strip()) < MIN_OVERRIDE_REASON_CHARS:
            raise ValueError(
                f"reason_text must be at least {MIN_OVERRIDE_REASON_CHARS} "
                "characters after trimming whitespace"
            )
        return value


class CreatedOverride(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    override_id: uuid.UUID
    order_id: uuid.UUID
    case_id: uuid.UUID
    flagged_owner_id: uuid.UUID = Field(
        description="The unit head the case was flagged to."
    )


class DischargeOverrideResult(BaseModel):
    encounter_id: uuid.UUID
    status: str
    discharged_at: dt.datetime
    reason_code: str
    unit_head_id: uuid.UUID
    overridden: list[CreatedOverride]
    opened_cases: list[OpenedCase] = Field(
        default_factory=list,
        description=(
            "Contracted investigations on the same encounter. They keep their "
            "contracted owner and SLA timer -- an override covers only the "
            "orders that had no contract."
        ),
    )
