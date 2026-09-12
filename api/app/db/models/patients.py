"""Patients. Phase 1.1.

Two columns here are load-bearing far downstream and are easy to mistake for
decoration:

* ``phone_verified_at`` -- the Phase 4 T+24h patient rung is *skipped* when it
  is null, and escalates to the unit head instead. Without it that rung fails
  silently, which is the exact failure mode this product exists to prevent.
* ``sms_consent_at`` / ``sms_consent_basis`` -- captured at admission, not at
  notification time, and required by the Phase 10.2 DPDP record.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import CheckConstraint, Date, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import (
    ActorMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPkMixin,
    text_enum,
)

PREFERRED_LANGUAGES = ("en", "hi", "pa")


class Patient(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    __tablename__ = "patients"

    mrn: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    dob: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    # Deliberately unconstrained: the build plan specifies value sets for every
    # other enum-like column but not for sex, and the hospital's own coding
    # (M/F/O vs male/female/other) is a data-standards decision, not ours.
    # Add a CHECK once the hospital confirms its value set.
    sex: Mapped[str | None] = mapped_column(String(32), nullable=True)

    phone_primary_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone_alt_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    preferred_language: Mapped[str] = mapped_column(
        String(2), nullable=False, server_default="en"
    )

    address_line: Mapped[str | None] = mapped_column(String(500), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(12), nullable=True)

    phone_verified_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sms_consent_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sms_consent_basis: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        CheckConstraint(
            text_enum("preferred_language", PREFERRED_LANGUAGES),
            name="ck_patients_preferred_language",
        ),
        # Phase 1.2. Fuzzy name search for the Phase 1.5 patient lookup, and
        # the trigram similarity Phase 7.4 scores candidate matches with.
        # Needs pg_trgm, installed by the Phase 0 init scripts.
        Index(
            "ix_patients_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )
