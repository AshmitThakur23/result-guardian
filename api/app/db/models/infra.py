"""Infrastructure tables. Not clinical data.

``worker_health`` is created by migration 0001_baseline. It is modelled here
purely so it exists in ``Base.metadata``: without it, the first Alembic
autogenerate run after Phase 1.1 emits ``op.drop_table('worker_health')`` --
silently deleting the worker's heartbeat and with it /api/health's ability to
report that timers have stopped firing.

It deliberately skips the Phase 0.6 mixins. This is telemetry keyed by worker
identity, not a clinical row: no UUID primary key, no soft delete, no actor
columns. Every clinical table from Phase 1.1 onward does use them.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import (
    ActorMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPkMixin,
    text_enum,
)

# How a `system_settings` row is parsed. Narrow on purpose: a value type the
# reader does not understand would be silently treated as a string, and a
# boolean kill switch read as the string "false" is truthy.
SETTING_VALUE_TYPES = ("bool", "int", "float", "string")


class WorkerHealth(Base):
    __tablename__ = "worker_health"

    worker_name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_beat_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)


class SystemSetting(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """Runtime configuration an admin can edit. Phase 5.4.

    CLAUDE.md: *"Configuration lives in tables, never in code."* The row is
    the override; the ``RG_``-prefixed environment variable is the default,
    so a fresh deployment boots with no rows at all.

    ``value`` is text with an explicit ``value_type`` rather than JSONB. A
    JSONB column would let an admin screen write an object into a boolean
    flag, and the reader would have to guess what to do with it.
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("key", name="uq_system_settings_key"),
        CheckConstraint(
            text_enum("value_type", SETTING_VALUE_TYPES),
            name="ck_system_settings_value_type",
        ),
    )
