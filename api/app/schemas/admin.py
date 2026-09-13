"""Admin editor payloads. Phase 5.4."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.organisation import USER_ROLES
from app.db.models.ownership import ESCALATION_TARGETS
from app.db.models.rules_config import KEYWORD_CATEGORIES, KEYWORD_SEVERITIES


class UserOut(BaseModel):
    id: uuid.UUID
    employee_code: str
    full_name: str
    email: str | None
    phone_e164: str | None
    role: str
    department_id: uuid.UUID | None
    department_name: str | None
    is_active: bool
    must_change_password: bool
    last_login_at: dt.datetime | None
    locked_until: dt.datetime | None
    failed_login_count: int


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_code: str = Field(min_length=1, max_length=64)
    full_name: str = Field(min_length=1, max_length=200)
    role: str = Field(description="One of: " + ", ".join(USER_ROLES))
    email: str | None = Field(default=None, max_length=320)
    phone_e164: str | None = Field(default=None, max_length=20)
    department_id: uuid.UUID | None = None
    # Optional: omit it and the account is created with a generated temporary
    # password, returned once and never stored in readable form.
    initial_password: str | None = Field(default=None, min_length=12, max_length=256)


class UserCreated(BaseModel):
    id: uuid.UUID
    employee_code: str
    temporary_password: str | None = Field(
        default=None,
        description="Shown once. Not recoverable — reset the password instead.",
    )


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone_e164: str | None = Field(default=None, max_length=20)
    role: str | None = None
    department_id: uuid.UUID | None = None
    is_active: bool | None = None
    # Clears a lockout without waiting out the 15 minutes.
    unlock: bool = False


class PasswordResetResult(BaseModel):
    user_id: uuid.UUID
    temporary_password: str
    must_change_password: bool = True


class PanicThresholdOut(BaseModel):
    id: uuid.UUID
    test_code: str
    loinc_code: str | None
    sex: str
    age_min_years: Decimal | None
    age_max_years: Decimal | None
    critical_low: Decimal | None
    critical_high: Decimal | None
    follow_up_low_multiplier: Decimal | None
    follow_up_high_multiplier: Decimal | None
    unit: str | None
    source: str
    effective_from: dt.datetime
    effective_to: dt.datetime | None


class PanicThresholdCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_code: str = Field(min_length=1, max_length=64)
    loinc_code: str | None = Field(default=None, max_length=32)
    sex: str = "any"
    age_min_years: Decimal | None = None
    age_max_years: Decimal | None = None
    critical_low: Decimal | None = None
    critical_high: Decimal | None = None
    follow_up_low_multiplier: Decimal | None = None
    follow_up_high_multiplier: Decimal | None = None
    unit: str | None = Field(default=None, max_length=64)
    # Mandatory, and not defaulted. *"Seed from your hospital's own critical
    # value list, not from the internet."* A threshold with no provenance is
    # a number nobody can defend at a review.
    source: str = Field(min_length=3, max_length=200)
    effective_from: dt.datetime | None = None


class KeywordOut(BaseModel):
    id: uuid.UUID
    term: str
    category: str
    severity: str
    requires_negation_check: bool
    active: bool


class KeywordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str = Field(min_length=1, max_length=200)
    category: str = Field(description="One of: " + ", ".join(KEYWORD_CATEGORIES))
    severity: str = Field(description="One of: " + ", ".join(KEYWORD_SEVERITIES))
    requires_negation_check: bool = True
    active: bool = True


class KeywordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    severity: str | None = None
    requires_negation_check: bool | None = None
    active: bool | None = None


class EscalationRungOut(BaseModel):
    id: uuid.UUID
    department_id: uuid.UUID | None
    department_name: str | None
    level: int
    target_type: str
    delay_minutes: int
    channels: list[str]
    severity: str
    active: bool


class EscalationRungUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: uuid.UUID | None = None
    level: int = Field(ge=0, le=20)
    target_type: str = Field(description="One of: " + ", ".join(ESCALATION_TARGETS))
    delay_minutes: int = Field(ge=0, le=100_000)
    channels: list[str] = Field(default_factory=lambda: ["in_app"], max_length=6)
    severity: str = "any"
    active: bool = True


class ProviderHealthOut(BaseModel):
    channel: str
    adapter: str
    sent_24h: int
    failed_24h: int
    suppressed_24h: int
    failure_rate: float
    last_failure_at: dt.datetime | None
    last_error: str | None


class NodeBStatusOut(BaseModel):
    """*"NODE B status and kill switch (``LLM_ENABLED``)."*"""

    llm_enabled: bool
    llm_enabled_source: str
    kill_switch_reason: str | None
    reachable: bool
    base_url: str
    model: str
    probe_error: str | None = None
    # Stated on the endpoint so nobody reads a red light as an outage.
    safety_note: str = (
        "NODE B is an accelerator. Every safety guarantee — tracking, timers, "
        "escalation, notification — runs on NODE A and is unaffected when this "
        "is off or unreachable."
    )


class KillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_enabled: bool
    reason: str = Field(
        min_length=5,
        max_length=500,
        description="Why. Shown to users and written to the audit log.",
    )


class OverrideReportRow(BaseModel):
    """*"Overrides report — every discharge override with reason and who
    approved."*"""

    id: uuid.UUID
    encounter_id: uuid.UUID
    order_id: uuid.UUID
    patient_name: str | None
    mrn: str | None
    reason_code: str
    reason_text: str
    overridden_by_name: str | None
    approved_by_name: str | None
    created_at: dt.datetime
    department_name: str | None
