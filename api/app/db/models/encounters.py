"""Encounters. Phase 1.1.

``type`` keeps ``opd`` on purpose. ADR 0003 puts OPD out of scope for v1 but
keeps the value in the enum, so admitting OPD later is a behaviour change (a
new trigger) rather than a migration. The discharge gate binds to
``ipd | emergency | daycare``.

``status`` carries ``lama``, ``transferred`` and ``deceased`` because the
Phase 4.6 suppression rules key off them -- notifying the family of a deceased
patient is the kind of failure that ends a pilot.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
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

ENCOUNTER_TYPES = ("ipd", "opd", "emergency", "daycare")
ENCOUNTER_STATUSES = ("active", "discharged", "lama", "transferred", "deceased")
# The gate only binds to encounter types that have a real discharge event.
GATED_ENCOUNTER_TYPES = ("ipd", "emergency", "daycare")


class Encounter(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    __tablename__ = "encounters"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    encounter_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)

    admitted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    discharged_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ward: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bed: Mapped[str | None] = mapped_column(String(64), nullable=True)

    attending_doctor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )

    __table_args__ = (
        CheckConstraint(text_enum("type", ENCOUNTER_TYPES), name="ck_encounters_type"),
        CheckConstraint(
            text_enum("status", ENCOUNTER_STATUSES), name="ck_encounters_status"
        ),
    )
