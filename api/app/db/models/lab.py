"""Lab-side accountability. Phase 2.3.

    The lab must be flagged too, not just the doctor. **A missing result is a
    lab-side failure.**

That is the whole reason this table exists. When a ``result_due`` timer fires
and no result has arrived, the case does *not* close and does *not* quietly
escalate to the doctor alone — a flag is raised against the lab, because the
thing that failed is upstream of the doctor. Chasing only the doctor for a
report the lab never produced is how a hospital learns to ignore the product.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
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

# The build plan's three, exactly.
LAB_FLAG_TYPES = ("sample_missing", "report_delayed", "sample_rejected")

# Phase 2.3: "Re-check timer every 24h until resolved, max 7 days, then
# escalate to unit head."
LAB_RECHECK_INTERVAL = dt.timedelta(hours=24)
LAB_RECHECK_MAX = dt.timedelta(days=7)


class LabFlag(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    __tablename__ = "lab_flags"

    case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    flag_type: Mapped[str] = mapped_column(String(32), nullable=False)

    raised_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            text_enum("flag_type", LAB_FLAG_TYPES), name="ck_lab_flags_flag_type"
        ),
        # Resolution is facts that arrive together; half of them is an
        # unfinished audit trail.
        CheckConstraint(
            "(resolved_at IS NULL) = (resolved_by IS NULL)",
            name="ck_lab_flags_resolution_is_complete",
        ),
        Index("ix_lab_flags_case_id", "case_id"),
        # "Lab-side metric on the admin dashboard: open lab flags by age."
        Index(
            "ix_lab_flags_open_raised_at",
            "raised_at",
            postgresql_where=text("resolved_at IS NULL AND deleted_at IS NULL"),
        ),
        # One *open* flag of a given type per case. Without it, every 24h
        # re-check would raise another "sample missing" for the same case and
        # the lab's queue would fill with copies of one problem.
        Index(
            "uq_lab_flags_open_per_case_type",
            "case_id",
            "flag_type",
            unique=True,
            postgresql_where=text("resolved_at IS NULL AND deleted_at IS NULL"),
        ),
    )
