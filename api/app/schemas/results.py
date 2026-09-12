"""Result intake and lab-flag shapes. Phase 2.3 / 2.4.

Deliberately thin on the clinical side. ``raw_payload`` is accepted as an
opaque object and stored as given: what is *in* a result is Phase 3's rule
engine and Phase 7's extraction, and a Phase 2 schema that tried to describe
analytes would be a second, competing definition to migrate away from later.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ResultCreate(BaseModel):
    """One incoming report for one order.

    ``source_ref`` is the lab's own reference for the report — an accession
    number, a document id, an HL7 message control id. It is what makes replay
    detection possible, so it is strongly encouraged and still optional: a
    clerk typing a result off a paper printout may genuinely have nothing to
    put there.
    """

    model_config = ConfigDict(extra="forbid")

    report_status: Literal["preliminary", "final", "amended", "corrected"]
    source: Literal["manual", "pdf", "hl7", "fhir"] = "manual"
    source_ref: str | None = Field(
        default=None,
        max_length=200,
        description="Lab reference for this report. Replay protection keys on it.",
    )
    reported_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "When the lab issued the report. Aware by requirement, like every "
            "other clinical timestamp: a naive value read against the server's "
            "locale is the wrong hour."
        ),
    )
    raw_payload: dict[str, object] = Field(
        default_factory=dict,
        description="Stored verbatim. Phase 2 does not interpret it.",
    )
    # Authentication is Phase 5.1. Until it exists the caller names itself,
    # exactly as the Phase 1.3 override path does, and for the same reason.
    recorded_by: uuid.UUID | None = None


class ResultRecorded(BaseModel):
    """What intake did, including what it did to the case and its timers."""

    result_id: uuid.UUID
    order_id: uuid.UUID
    case_id: uuid.UUID | None = None
    case_state: str | None = None
    superseded_timer_ids: list[uuid.UUID] = Field(default_factory=list)
    classification_enqueued: bool = Field(
        description="True when the Phase 3 rule engine has been handed this result."
    )
    late: bool = Field(
        description="The case had already closed when this result arrived."
    )


class LabFlagRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    flag_type: str
    raised_at: dt.datetime
    resolved_at: dt.datetime | None = None
    resolved_by: uuid.UUID | None = None
    resolution_note: str | None = None


class LabFlagResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_by: uuid.UUID
    resolution_note: str | None = Field(default=None, max_length=2000)


class LabFlagAgeBucket(BaseModel):
    """One row of Phase 2.3's *"open lab flags by age"* metric."""

    bucket: str = Field(
        description="under_24h | 1_to_3_days | 3_to_7_days | over_7_days"
    )
    flag_count: int
    oldest_raised_at: dt.datetime | None = None


class LabFlagMetrics(BaseModel):
    """The lab-side metric the admin dashboard renders.

    Phase 2.3 asks for the measurement; Phase 5.4 builds the dashboard that
    draws it. Shipping the numbers now means the dashboard is a rendering job
    rather than a rendering *and* measurement job.
    """

    open_total: int
    by_age: list[LabFlagAgeBucket]
    by_type: dict[str, int]
