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

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkerHealth(Base):
    __tablename__ = "worker_health"

    worker_name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_beat_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
