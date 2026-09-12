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

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import ActorMixin, SoftDeleteMixin, TimestampMixin, UUIDPkMixin


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
