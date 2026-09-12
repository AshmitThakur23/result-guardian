"""Discharge contracts. Phase 1.1.

The contract is the product's core artefact: a doctor cannot complete a
discharge while an investigation has no owner and no expected-by date, and
this row is what they are forced to create.

``UNIQUE (order_id)`` is not a tidiness constraint -- it is the database
enforcing "one contract per pending order", which is what stops two concurrent
discharge attempts from both succeeding. The build plan is explicit that the
safety property is *"database constraints and a state machine"*, not
application logic.

The row is **immutable**: changes belong in ``discharge_contract_revisions``,
which arrives with the rest of Phase 1.1. The immutability trigger is
deliberately NOT added here -- it would have nowhere to record the change yet.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
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

# From the build plan's 1.3 override spec for this table. Every value is a
# reason a discharge may legitimately bypass the gate; anything else is a bug.
OVERRIDE_REASON_CODES = (
    "patient_lama",
    "transfer_out",
    "deceased",
    "system_outage",
    "clinical_urgency",
)


class DischargeContract(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    __tablename__ = "discharge_contracts"

    encounter_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("encounters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Who is accountable. Phase 4.2 resolves a live owner from here, falling
    # through the roster only when this doctor is unavailable.
    responsible_doctor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # The deadline the Phase 2 result_due timer is created from.
    expected_by: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("order_id", name="uq_discharge_contracts_order_id"),
    )


class DischargeContractRevision(Base, UUIDPkMixin, TimestampMixin, ActorMixin):
    """Immutable record of a change to a discharge contract.

    The contract itself is immutable, so a change to the responsible doctor or
    the expected-by date is written here rather than overwriting history. One
    row per field changed.

    No ``deleted_at``: a revision that can be removed is not an audit trail.
    The Phase 5.5 hash-chained audit log covers the whole system; this is the
    contract-specific record the build plan asks for by name.
    """

    __tablename__ = "discharge_contract_revisions"

    contract_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("discharge_contracts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Which column changed, then its before and after values as text. Text
    # rather than typed columns because the fields that can change are of
    # different types (a UUID doctor, a timestamp deadline, a note).
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_discharge_contract_revisions_contract_id", "contract_id"),
    )


class DischargeMedication(
    Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin
):
    """What the patient went home on.

    Captured now because Phase 3's Rule B compares a culture's sensitivity
    grid against these drugs: an organism resistant to a drug the patient is
    actually taking is the CRITICAL case the product exists to catch. The
    build plan is blunt -- *"Capture it now or Rule B has nothing to compare
    against."*
    """

    __tablename__ = "discharge_medications"

    encounter_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("encounters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    drug_name: Mapped[str] = mapped_column(String(300), nullable=False)
    drug_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    atc_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    dose: Mapped[str | None] = mapped_column(String(64), nullable=True)
    route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # NUMERIC, never float -- the convention applies to every value column.
    duration_days: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    # Rule B only needs to consider antibiotics; flagging them here keeps that
    # lookup from having to re-derive the answer from the drug name.
    is_antibiotic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )

    __table_args__ = (
        CheckConstraint(
            "duration_days IS NULL OR duration_days > 0",
            name="ck_discharge_medications_duration_positive",
        ),
        # Rule B looks these up by encounter.
        Index("ix_discharge_medications_encounter_id", "encounter_id"),
    )


class DischargeOverride(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """A discharge completed without a contract -- the emergency escape hatch.

    Every override is a gap in the safety property, so the row exists to make
    that gap auditable: who did it, why, and whether anyone approved. Phase 1.3
    still creates a pending case for the order and flags it to the unit head
    immediately, and Phase 5.4 reports on these.

    The reason_code list and the 20-character minimum come from the build
    plan's 1.3 override spec for this table. They are data constraints, not
    workflow, so they are enforced here -- RULE 1: the safety property is
    database constraints, not application code.
    """

    __tablename__ = "discharge_overrides"

    encounter_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("encounters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    overridden_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Nullable: an override may be recorded before anyone counter-signs it.
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            text_enum("reason_code", OVERRIDE_REASON_CODES),
            name="ck_discharge_overrides_reason_code",
        ),
        # A one-word excuse is not an audit trail. 20 chars is the build
        # plan's own floor.
        CheckConstraint(
            "char_length(trim(reason_text)) >= 20",
            name="ck_discharge_overrides_reason_text_min_length",
        ),
        # Phase 5.4's overrides audit report groups by encounter.
        Index("ix_discharge_overrides_encounter_id", "encounter_id"),
    )
