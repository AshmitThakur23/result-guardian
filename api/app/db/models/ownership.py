"""Ownership and availability. Phase 4.1.

Three tables that answer one question between them: **when a flag needs a
human, which human?**

``duty_roster`` is who is on shift. ``user_absences`` is who is away and who
covers for them. ``escalation_chain`` is what happens, per department, when
nobody acknowledges.

The build plan is blunt about the risk in the first of these:

    Decide who maintains this. **A roster nobody updates is worse than no
    roster**, because the system will confidently notify someone who left.

[ADR 0004](../../../../docs/adr/0004-duty-roster-ownership.md) settles it: the
**unit head maintains their department's roster, weekly**, because the unit
head is rung 2 of the escalation ladder. A stale roster escalates to the person
who let it go stale. Every other option separates the cost from the control.

That ADR also requires the staleness guards to ship in the same sprint, and
they shape this schema: ``resolve_owner`` falls through to the department's
unit head (step 5) rather than to silence, and the fallthrough is counted so a
roster going stale has a measurable signature rather than a quiet one.
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
    Integer,
    String,
    UniqueConstraint,
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

# The plan's vocabulary, exactly.
ROLES_ON_DUTY = ("primary", "backup", "consultant")
ABSENCE_TYPES = ("leave", "resigned", "suspended", "training")
ESCALATION_TARGETS = ("owner", "roster_on_duty", "unit_head", "admin", "patient")

# Channels a rung may use. ``in_app`` is always available; the rest depend on a
# configured provider and degrade to in_app when one is missing.
NOTIFICATION_CHANNELS = ("in_app", "email", "sms", "whatsapp")

# Phase 4.4's ladder is severity-differentiated -- the plan gives a "default
# delay" and a separate "critical delay" per rung. A chain row therefore
# applies to one severity, and 'any' is the fallback when a hospital has not
# split them.
CHAIN_SEVERITIES = ("any", "follow_up", "critical")


class DutyRoster(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """Who is on shift, per department. Phase 4.1.

    Maintained weekly by the department's unit head (ADR 0004). Rows overlap
    freely — a department can have a primary and a backup on the same shift,
    and ``resolve_owner`` reads ``role_on_duty`` to order them.
    """

    __tablename__ = "duty_roster"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
    )
    shift_start: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    shift_end: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    role_on_duty: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        CheckConstraint(
            text_enum("role_on_duty", ROLES_ON_DUTY), name="ck_duty_roster_role"
        ),
        # A shift that ends before it starts would silently never match
        # ``now() BETWEEN shift_start AND shift_end`` and the roster would look
        # populated while resolving nobody -- the exact failure ADR 0004 warns
        # about, wearing a different hat.
        CheckConstraint("shift_end > shift_start", name="ck_duty_roster_shift_ordered"),
        # The lookup: department + covering now(), ordered by role.
        Index(
            "ix_duty_roster_department_shift",
            "department_id",
            "shift_start",
            "shift_end",
        ),
        Index("ix_duty_roster_user", "user_id"),
    )


class UserAbsence(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """Who is away, and who covers. Phase 4.1.

    ``ends_at`` is nullable and null means **indefinite** — the plan says so
    explicitly, and it is the shape "resigned" needs. A resigned doctor with a
    fixed end date would come back on duty by arithmetic.
    """

    __tablename__ = "user_absences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    absence_type: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ends_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delegate_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            text_enum("absence_type", ABSENCE_TYPES), name="ck_user_absences_type"
        ),
        CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_user_absences_window_ordered",
        ),
        # A delegate who is the absent person is a loop that resolves to
        # nobody. Cheaper to refuse than to detect at resolution time.
        CheckConstraint(
            "delegate_user_id IS NULL OR delegate_user_id <> user_id",
            name="ck_user_absences_delegate_not_self",
        ),
        Index("ix_user_absences_user_window", "user_id", "starts_at", "ends_at"),
    )


class EscalationChain(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """One rung of the ladder, per department. Phase 4.1 / 4.4.

        One row per rung, per department. **This is what makes escalation
        configurable per hospital.**

    ``department_id`` is nullable, and null is the **global default** used by a
    department that has not defined its own. Without that, a new department
    would have no ladder at all and its flags would sit at rung 0 forever —
    silence, which is the one outcome this phase exists to prevent.

    ``delay_minutes`` is measured from when the case was flagged, not from the
    previous rung, so a late-firing rung cannot push the whole ladder out.
    """

    __tablename__ = "escalation_chain"

    department_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=True,
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    channels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[\"in_app\"]'::jsonb")
    )
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'any'")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (
        CheckConstraint(
            text_enum("target_type", ESCALATION_TARGETS),
            name="ck_escalation_chain_target",
        ),
        CheckConstraint(
            text_enum("severity", CHAIN_SEVERITIES), name="ck_escalation_chain_severity"
        ),
        CheckConstraint("level >= 0", name="ck_escalation_chain_level_non_negative"),
        CheckConstraint(
            "delay_minutes >= 0", name="ck_escalation_chain_delay_non_negative"
        ),
        # One row per (department, severity, level). NULLS NOT DISTINCT so the
        # global default rows -- where department_id is null, which is the
        # common case -- actually collide instead of silently duplicating.
        # Phase 3 was bitten by exactly this on unit_conversions.
        UniqueConstraint(
            "department_id",
            "severity",
            "level",
            name="uq_escalation_chain_rung",
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_escalation_chain_lookup",
            "department_id",
            "severity",
            "level",
            postgresql_where=text("active AND deleted_at IS NULL"),
        ),
    )
