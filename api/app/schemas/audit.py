"""Audit trail payloads. Phase 5.5."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel


class AuditRowOut(BaseModel):
    seq: int
    occurred_at: dt.datetime
    actor_user_id: uuid.UUID | None
    actor_name: str | None
    actor_employee_code: str | None
    actor_ip: str | None
    action: str
    entity_type: str
    entity_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    # Returned so a caller can verify the chain independently. The row is
    # public to anyone allowed to read the log at all, and withholding it
    # would make the export unverifiable without adding any secrecy.
    prev_hash: str
    row_hash: str
    break_glass_reason: str | None


class AuditPage(BaseModel):
    rows: list[AuditRowOut]
    next_after_seq: int | None = None


class ChainVerificationOut(BaseModel):
    intact: bool
    rows_checked: int
    first_break_seq: int | None = None
    first_break_reason: str | None = None
    head_seq: int | None = None
    head_hash: str | None = None
    window_anchored: bool = True
    """False when a ``from``/``to`` window was given: the first row's
    ``prev_hash`` could not be checked against the row before it."""
    verified_at: dt.datetime


class AuditAnchorOut(BaseModel):
    anchored_at: dt.datetime
    head_seq: int
    head_hash: str
    rows_verified: int
    chain_intact: bool
    first_break_seq: int | None = None
