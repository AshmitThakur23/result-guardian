"""Investigation results.

⚠️ **This table's schema is specified in Phase 3.1, not Phase 2.** It is
created by Phase 2's migration because Phase 2.4's
``POST /api/orders/{id}/results`` has to put its payload somewhere, and the
plan is explicit that that endpoint *"stays forever"* — an intake endpoint
that persists nothing would make that false. The column list below is 3.1's,
unchanged.

Phase 3.1's *other* result tables — ``result_analytes``, ``result_organisms``,
``result_sensitivities``, ``result_narratives`` — are deliberately absent. They
exist to be read by the clinical rule engine, which is Phase 3's work. Phase 2
stores the payload and enqueues classification **without interpreting any of
it**: RULE 1 means the tracking guarantee never waits on interpretation.
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
    func,
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

REPORT_STATUSES = ("preliminary", "final", "amended", "corrected")

# A preliminary report is not an answer -- Phase 2.2's "classification timers"
# is why the distinction is load-bearing here rather than cosmetic.
NON_FINAL_REPORT_STATUSES = ("preliminary",)

RESULT_SOURCES = ("manual", "pdf", "hl7", "fhir")


class Result(Base, UUIDPkMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    __tablename__ = "results"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Nullable: a result can legitimately arrive for an order that never went
    # through a discharge and so has no tracking case. Phase 7.5 routes those
    # to an orphan queue rather than inventing a case for them.
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_cases.id", ondelete="RESTRICT"),
        nullable=True,
    )

    report_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reported_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # An amended or corrected report replaces an earlier one. The earlier row
    # is kept and pointed forward rather than edited: what the doctor was told
    # first is medico-legal evidence.
    superseded_by_result_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("results.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            text_enum("report_status", REPORT_STATUSES), name="ck_results_report_status"
        ),
        CheckConstraint(text_enum("source", RESULT_SOURCES), name="ck_results_source"),
        CheckConstraint(
            "superseded_by_result_id IS NULL OR superseded_by_result_id <> id",
            name="ck_results_not_self_superseding",
        ),
        Index("ix_results_order_id", "order_id"),
        Index("ix_results_case_id", "case_id"),
        # Replay protection for intake: the same lab report delivered twice
        # must not become two results. Partial, because source_ref is null for
        # payloads carrying no accession or document reference.
        Index(
            "uq_results_source_ref",
            "source",
            "source_ref",
            unique=True,
            postgresql_where=text("source_ref IS NOT NULL AND deleted_at IS NULL"),
        ),
    )
