"""Departments and users. Phase 1.1.

The build plan insists these exist now, long before auth lands in Phase 5:
*"Create the table and the role column now even though auth lands in Phase 5.
Retrofitting ownership onto rows that have no user is painful."* Every other
table's ``created_by`` / ``updated_by`` points here.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    text,
    true,
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

USER_ROLES = ("doctor", "unit_head", "lab_tech", "admin", "auditor")


class Department(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    __tablename__ = "departments"

    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Rung 2 of the Phase 4 escalation ladder, and the owner of duty_roster
    # upkeep per ADR 0004. Nullable: a department may exist before its head is
    # appointed, and the FK is added after `users` exists (circular reference).
    unit_head_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class User(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    __tablename__ = "users"

    employee_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # E.164 so the Phase 4 SMS rung has something dialable without reformatting.
    phone_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Argon2id hash, populated in Phase 5.1. Nullable until then so users can
    # be seeded now without inventing credentials.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    last_login_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    # Phase 5.1: "Account lockout after 5 failures for 15 min."
    # Counted on the user rather than by IP: the thing being protected is the
    # account, and an attacker who rotates IPs would walk straight past an
    # IP-keyed counter.
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(text_enum("role", USER_ROLES), name="ck_users_role"),
        CheckConstraint(
            "failed_login_count >= 0", name="ck_users_failed_login_non_negative"
        ),
    )
