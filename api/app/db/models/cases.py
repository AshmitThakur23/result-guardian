"""Pending cases and their event log. Phase 1.1.

``pending_cases`` is the row the whole product revolves around: created when a
discharge completes with an investigation still outstanding, and closed only
when a human acknowledges the result. Phase 2 hangs timers off it, Phase 4
resolves an owner for it, Phase 5 closes it.

``case_events`` is its append-only history. The build plan is explicit that
append-only is **enforced with a trigger, not convention** -- the trigger lives
in migration 0003 because SQLAlchemy cannot express it.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import (
    ActorMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPkMixin,
    text_enum,
)

CASE_STATES = (
    "awaiting_result",
    "result_received",
    "classified",
    "flagged",
    "acknowledged",
    "closed",
    "reopened",
)
# NULL is valid and means "not yet classified" -- the CHECK below allows it.
CASE_SEVERITIES = ("normal", "follow_up", "critical")


class PendingCase(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    __tablename__ = "pending_cases"

    # One case per order. The database enforcing it is what stops a duplicate
    # result intake from opening a second case for the same investigation.
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    encounter_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("encounters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Nullable: the Phase 1.3 override path creates a case with no contract,
    # flagged to the unit head immediately. A case must never be blocked from
    # existing just because the gate was bypassed.
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("discharge_contracts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Resolved by Phase 4.2, which may reassign away from the contract's
    # responsible doctor when that doctor is unavailable. Kept separate from
    # the contract for exactly that reason.
    current_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="awaiting_result"
    )
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)

    opened_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    result_received_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    flagged_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Left unconstrained on purpose. The closure reasons are enumerated in
    # Phase 5.3, not in the 1.1 schema spec, and a CHECK written now would
    # block Phase 5 if that list is refined. Constrain it in Phase 5.
    closure_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    closure_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    reopened_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    __table_args__ = (
        UniqueConstraint("order_id", name="uq_pending_cases_order_id"),
        CheckConstraint(text_enum("state", CASE_STATES), name="ck_pending_cases_state"),
        # NULL means "not yet classified", which is a real state before the
        # Phase 3 rule engine has run.
        CheckConstraint(
            "severity IS NULL OR " + text_enum("severity", CASE_SEVERITIES),
            name="ck_pending_cases_severity",
        ),
        CheckConstraint(
            "reopened_count >= 0", name="ck_pending_cases_reopened_count_non_negative"
        ),
        # Phase 4 lists a doctor's open cases by owner.
        Index("ix_pending_cases_current_owner_id", "current_owner_id"),
    )


class CaseEvent(Base, UUIDPkMixin, TimestampMixin, ActorMixin):
    """Append-only. UPDATE and DELETE are rejected by a database trigger.

    No ``deleted_at``: soft-deleting a row is an UPDATE, which the trigger
    refuses. A case's history is medico-legal evidence (Phase 10.3) and must
    not be editable, including by us.
    """

    __tablename__ = "case_events"

    case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nullable for system-generated events -- a timer firing has no human actor.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Phase 5.2 renders a case's timeline: every event for one case, in order.
    __table_args__ = (
        Index("ix_case_events_case_id_occurred_at", "case_id", "occurred_at"),
    )
