"""Ownership, escalation and notification shapes. Phase 4.

The reassignment schema carries the phase's sharpest rule in its validation:
a reason is **mandatory**, and the plan says why —

    **Reassignment does not reset the escalation clock**, otherwise it becomes
    a dodge.

An unexplained handover is the thing that endpoint exists to make visible, so
an empty or whitespace-only reason is refused at the door rather than stored.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models.ownership import (
    ABSENCE_TYPES,
    NOTIFICATION_CHANNELS,
    ROLES_ON_DUTY,
)

RoleOnDuty = Literal["primary", "backup", "consultant"]
AbsenceType = Literal["leave", "resigned", "suspended", "training"]
Channel = Literal["in_app", "email", "sms", "whatsapp"]

_ = (ROLES_ON_DUTY, ABSENCE_TYPES, NOTIFICATION_CHANNELS)


class ReassignRequest(BaseModel):
    """``POST /api/cases/{id}/reassign`` — with a **mandatory reason**."""

    model_config = ConfigDict(extra="forbid")

    new_owner_id: uuid.UUID
    reason: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "Why this case is moving. Mandatory: reassignment does not reset "
            "the escalation clock, and an unexplained handover is exactly what "
            "this field exists to surface."
        ),
    )
    # Authentication is Phase 5.1. Until then the caller names itself, as the
    # Phase 1.3 override path and Phase 2.4 intake both already do.
    reassigned_by: uuid.UUID | None = None

    @field_validator("reason")
    @classmethod
    def _reason_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a reassignment reason cannot be only whitespace")
        return value.strip()


class ReassignResult(BaseModel):
    case_id: uuid.UUID
    previous_owner_id: uuid.UUID | None = None
    new_owner_id: uuid.UUID
    reason: str
    escalation_clock_reset: bool = Field(
        default=False,
        description="Always false. Reassignment is not progress.",
    )


class AcknowledgeRequest(BaseModel):
    """Acknowledging stops the ladder. Exit Gate 4's second clause."""

    model_config = ConfigDict(extra="forbid")

    acknowledged_by: uuid.UUID
    note: str | None = Field(default=None, max_length=2000)


class AcknowledgeResult(BaseModel):
    case_id: uuid.UUID
    cancelled_timer_ids: list[uuid.UUID] = Field(default_factory=list)
    already_acknowledged: bool = False


class OwnerResolutionOut(BaseModel):
    """Who the engine would route this case to, and why."""

    user_id: uuid.UUID | None = None
    step: str
    level: int = Field(description="1-6, the plan's own numbering.")
    reason: str
    unresolved: bool = False
    roster_fallthrough: bool = Field(
        description="Fell through to the unit head or admin — ADR 0004's "
        "stale-roster signal."
    )


class DutyRosterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    department_id: uuid.UUID
    shift_start: dt.datetime
    shift_end: dt.datetime
    role_on_duty: RoleOnDuty = "primary"
    created_by: uuid.UUID | None = None

    @model_validator(mode="after")
    def _shift_is_ordered(self) -> DutyRosterCreate:
        if self.shift_end <= self.shift_start:
            # The database refuses this too. Catching it here gives the unit
            # head a readable message instead of an IntegrityError.
            raise ValueError("shift_end must be after shift_start")
        return self


class DutyRosterRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    department_id: uuid.UUID
    shift_start: dt.datetime
    shift_end: dt.datetime
    role_on_duty: str


class AbsenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    absence_type: AbsenceType
    starts_at: dt.datetime
    ends_at: dt.datetime | None = Field(
        default=None,
        description="Null means indefinite — which is what 'resigned' needs.",
    )
    delegate_user_id: uuid.UUID | None = None
    created_by: uuid.UUID | None = None

    @model_validator(mode="after")
    def _window_is_ordered(self) -> AbsenceCreate:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at, or null for indefinite")
        if self.delegate_user_id is not None and self.delegate_user_id == self.user_id:
            raise ValueError("a user cannot be their own delegate")
        return self


class AbsenceRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    absence_type: str
    starts_at: dt.datetime
    ends_at: dt.datetime | None = None
    delegate_user_id: uuid.UUID | None = None


class StaleRosterDepartment(BaseModel):
    """ADR 0004 guard 3 — the roster-stale badge's data."""

    department_id: str
    code: str
    name: str
    unit_head_user_id: str | None = None
    unit_head_name: str | None = None


class FallthroughMetric(BaseModel):
    """ADR 0004 guard 4 — the stale-roster signal, per department."""

    department_id: str
    resolutions: int
    fell_through_to_unit_head_or_admin: int


class FlagRateMetric(BaseModel):
    """Phase 4.5's *"flag rate per 100 discharges"*, with the plan's own
    retune threshold carried alongside so a dashboard cannot render the number
    without the line it must not cross."""

    since: str
    discharges: int
    flagged_cases: int
    critical_cases: int
    flags_per_100_discharges: float
    retune_threshold_per_100: float
    exceeds_retune_threshold: bool


class DeliveryReceipt(BaseModel):
    """*"Delivery receipt webhook endpoint for SMS provider."*"""

    model_config = ConfigDict(extra="allow")

    provider_msg_id: str = Field(min_length=1, max_length=200)
    delivered: bool
    error: str | None = Field(default=None, max_length=500)


class PatientContactCreate(BaseModel):
    """*"Front desk can mark 'patient called back'."*"""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    direction: Literal["inbound", "outbound"] = "inbound"
    note: str | None = Field(default=None, max_length=2000)
    recorded_by_user_id: uuid.UUID | None = None


class PatientContactRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    patient_id: uuid.UUID
    direction: str
    contacted_at: dt.datetime
    note: str | None = None


class NotificationRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    user_id: uuid.UUID | None = None
    patient_id: uuid.UUID | None = None
    channel: str
    template_key: str
    locale: str
    status: str
    suppression_reason: str | None = None
    escalation_level: int | None = None
    attempts: int
    sent_at: dt.datetime | None = None
    error: str | None = None
