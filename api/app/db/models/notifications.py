"""Notifications and patient contact. Phase 4.3 / 4.6.

Phase 2.3 has been enqueueing notification *intents* onto the pgmq
``notifications`` queue since lab flags shipped, with nothing consuming them —
deliberately, because a stub consumer would have deleted them. This is the
table those intents finally land in, and the record of what was actually sent.

**A notification row is not a message.** It is the record that the system took
responsibility for telling someone, and what became of that. ``status`` carries
the whole lifecycle including the two outcomes that are not failures:

* ``suppressed`` — a deliberate decision not to send. Quiet hours, a rate cap,
  a deceased patient, an unverified phone. The plan requires every one of those
  and each must be visible afterwards, because "we chose not to send" and "we
  failed to send" are different facts and only one of them is a bug.
* ``queued`` — accepted, not yet attempted.

**The safety state never depends on a row here.** Phase 4.4's ladder is driven
by ``sla_timers``, which is PostgreSQL truth; a provider outage marks rows
``failed`` and the ladder keeps climbing. That is the degradation the plan asks
for: *"SMS provider returns 500 → retried → failure surfaced, ladder
continues."*
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.ownership import NOTIFICATION_CHANNELS
from app.db.types import (
    ActorMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPkMixin,
    text_enum,
)

NOTIFICATION_STATUSES = ("queued", "sent", "delivered", "failed", "suppressed")

# Locales the template store carries. Same three as `patients.preferred_language`
# -- a locale a patient can hold but no template exists for would render blank.
LOCALES = ("en", "hi", "pa")

# Phase 4.3: "Provider failure -> retry 3x with backoff -> mark failed".
MAX_SEND_ATTEMPTS = 3

# Why a notification was not sent. Recorded rather than inferred, because
# "suppressed" on its own tells an auditor nothing.
SUPPRESSION_REASONS = (
    "quiet_hours",
    "rate_capped",
    "deduplicated",
    "patient_deceased",
    "phone_unverified",
    "no_recipient",
    "no_consent",
    "encounter_lama",
)


class Notification(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """One thing the system owed one recipient. Phase 4.3."""

    __tablename__ = "notifications"

    # Nullable, because two Phase 4.5 messages are addressed to a *person*
    # rather than to a case: the morning digest spans every open follow-up a
    # doctor owns, and ADR 0004's weekly roster reminder belongs to no case at
    # all. Forcing a case_id on those would mean either inventing one or not
    # recording that the message was sent.
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_cases.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Exactly one of these is set for a real recipient; both null is possible
    # for a department-wide intent (the lab queue), which the plan also emits.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=True,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    template_key: Mapped[str] = mapped_column(String(80), nullable=False)
    locale: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'en'")
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'queued'")
    )
    suppression_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_msg_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    sent_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Escalation rung this came from, when it came from one. Null for
    # Phase 2.3's lab-side intents, which are not part of the ladder.
    escalation_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What makes a redelivered queue message produce one notification rather
    # than two. Unique, and built from the facts that identify the obligation.
    dedupe_key: Mapped[str] = mapped_column(String(250), nullable=False)

    __table_args__ = (
        CheckConstraint(
            text_enum("channel", NOTIFICATION_CHANNELS), name="ck_notifications_channel"
        ),
        CheckConstraint(
            text_enum("status", NOTIFICATION_STATUSES), name="ck_notifications_status"
        ),
        CheckConstraint(text_enum("locale", LOCALES), name="ck_notifications_locale"),
        CheckConstraint("attempts >= 0", name="ck_notifications_attempts_non_negative"),
        # sent_at and status must agree, the same biconditional Phase 2.1 uses
        # on sla_timers.fired_at. A row claiming to be sent with no timestamp
        # is unauditable.
        CheckConstraint(
            "(status IN ('sent', 'delivered')) = (sent_at IS NOT NULL)",
            name="ck_notifications_sent_at_matches_status",
        ),
        # A suppression must say why; anything else must not pretend to.
        CheckConstraint(
            "(status = 'suppressed') = (suppression_reason IS NOT NULL)",
            name="ck_notifications_suppression_reason_matches_status",
        ),
        UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
        Index("ix_notifications_case_id", "case_id"),
        Index(
            "ix_notifications_user_channel_sent",
            "user_id",
            "channel",
            "sent_at",
        ),
        Index(
            "ix_notifications_queued",
            "status",
            postgresql_where=text("status = 'queued' AND deleted_at IS NULL"),
        ),
        Index("ix_notifications_provider_msg_id", "provider_msg_id"),
    )


class PatientContact(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """*"Inbound: `patient_contacts` table so front desk can mark 'patient
    called back'."* Phase 4.6.

    The other half of a patient SMS. Without it the ladder has no way to know
    the message worked, and the only evidence a patient responded lives in
    somebody's memory of a phone call.
    """

    __tablename__ = "patient_contacts"

    case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    contacted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            text_enum("direction", ("inbound", "outbound")),
            name="ck_patient_contacts_direction",
        ),
        Index("ix_patient_contacts_case_id", "case_id"),
        Index("ix_patient_contacts_patient_id", "patient_id"),
    )
