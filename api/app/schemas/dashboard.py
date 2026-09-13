"""Worklist, case detail and closure payloads. Phase 5.2 / 5.3."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.cases import CLINICAL_CLOSURE_REASONS


class WorklistRowOut(BaseModel):
    case_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    mrn: str
    test_name: str
    severity: str | None
    state: str
    opened_at: dt.datetime
    flagged_at: dt.datetime | None
    age_seconds: int
    escalation_level: int | None
    next_escalation_at: dt.datetime | None
    # Negative means the rung is overdue — the countdown has run out and the
    # worker has not fired it yet. The UI shows that as "overdue", which is
    # information the doctor needs and a clamp to zero would hide.
    seconds_to_next_escalation: int | None
    owner_id: uuid.UUID | None
    owner_name: str | None
    department_id: uuid.UUID | None
    department_name: str | None
    summary: str
    reopened_count: int


class WorklistOut(BaseModel):
    rows: list[WorklistRowOut]
    next_cursor: str | None = None
    total_open: int | None = None


class AnalyteOut(BaseModel):
    seq: int
    test_name: str
    value: str | None
    unit: str | None
    ref_low: str | None
    ref_high: str | None
    ref_text: str | None
    abnormal: bool
    abnormal_direction: str | None
    lab_flag: str | None


class SensitivityOut(BaseModel):
    organism: str
    colony_count: str | None
    specimen_type: str | None
    sensitivities: list[dict[str, str | None]]


class ExplanationOut(BaseModel):
    severity: str
    rule_id: str
    reason_code: str
    headline: str
    detail: str | None = None


class TimelineEventOut(BaseModel):
    occurred_at: dt.datetime
    event_type: str
    actor_name: str | None
    payload: dict[str, Any]


class CaseDetailOut(BaseModel):
    case_id: uuid.UUID
    state: str
    severity: str | None
    opened_at: dt.datetime
    flagged_at: dt.datetime | None
    acknowledged_at: dt.datetime | None
    closed_at: dt.datetime | None
    closure_reason: str | None
    closure_note: str | None
    reopened_count: int
    patient: dict[str, Any]
    encounter: dict[str, Any]
    order: dict[str, Any]
    owner: dict[str, Any] | None
    result: dict[str, Any] | None
    analytes: list[AnalyteOut]
    organisms: list[SensitivityOut]
    narratives: list[dict[str, Any]]
    explanations: list[ExplanationOut]
    timeline: list[TimelineEventOut]
    escalation_level: int | None
    next_escalation_at: dt.datetime | None
    can_acknowledge: bool


class CloseCaseRequest(BaseModel):
    """*"requires ``closure_reason`` from enum"* — Phase 5.3.

    ``acknowledged_by`` is deliberately **absent**: the closer is the
    authenticated caller. A client-supplied actor would let anyone sign a
    closure in a colleague's name, which is precisely what the audit log
    exists to prevent.
    """

    model_config = ConfigDict(extra="forbid")

    closure_reason: str = Field(
        description="One of: " + ", ".join(CLINICAL_CLOSURE_REASONS)
    )
    # Required in practice for all five clinical reasons; the service enforces
    # the minimum length so the rule lives in one place.
    closure_note: str | None = Field(default=None, max_length=4000)
    duplicate_of_case_id: uuid.UUID | None = None


class CloseCaseResult(BaseModel):
    case_id: uuid.UUID
    closure_reason: str
    already_closed: bool = False
    cancelled_timer_ids: list[uuid.UUID] = Field(default_factory=list)
    audit_seq: int | None = None


class BulkCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Capped: an unbounded list is both a request-size problem and a way to
    # close a ward in one click.
    case_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    closure_reason: str
    closure_note: str | None = Field(default=None, max_length=4000)


class BulkCloseResult(BaseModel):
    closed: list[CloseCaseResult]


class ReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=10, max_length=2000)
    amended_result_id: uuid.UUID | None = None


class ReopenResult(BaseModel):
    case_id: uuid.UUID
    reopened_count: int
    reason: str


class AddNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=4000)
