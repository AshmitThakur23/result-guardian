"""Investigation orders. Phase 1.1.

``status`` is what the discharge gate reads: an order blocks discharge unless
it is ``final``, ``cancelled`` or ``rejected``. ``external_order_id`` is the
lab's own accession number and is indexed because Phase 7.5 matches on it
first -- an exact accession match is the only signal strong enough to
auto-attach a result to a patient without human review.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String
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

ORDER_CATEGORIES = ("lab", "radiology", "pathology", "micro")
ORDER_STATUSES = (
    "ordered",
    "collected",
    "in_lab",
    "preliminary",
    "final",
    "cancelled",
    "rejected",
)
# Anything NOT in this set blocks discharge. Phase 1.3 reads it; it lives here
# so the gate and the schema cannot drift apart.
ORDER_STATUSES_NOT_BLOCKING = ("final", "cancelled", "rejected")


class Order(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    __tablename__ = "orders"

    encounter_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("encounters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Denormalised from the encounter on purpose: Phase 7.5 scores candidate
    # matches on MRN + test + date, and joining through encounters for every
    # candidate would make that scan needlessly expensive.
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_order_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )

    test_code: Mapped[str] = mapped_column(String(64), nullable=False)
    test_name: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)

    ordered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    ordered_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sample_collected_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # NUMERIC, never float -- the gate derives the suggested expected_by from
    # ordered_at + this, and a binary rounding error there is a wrong deadline.
    expected_tat_hours: Mapped[float | None] = mapped_column(
        Numeric(6, 2), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="ordered"
    )

    __table_args__ = (
        CheckConstraint(
            text_enum("category", ORDER_CATEGORIES), name="ck_orders_category"
        ),
        CheckConstraint(text_enum("status", ORDER_STATUSES), name="ck_orders_status"),
        CheckConstraint(
            "expected_tat_hours IS NULL OR expected_tat_hours > 0",
            name="ck_orders_expected_tat_hours_positive",
        ),
    )
