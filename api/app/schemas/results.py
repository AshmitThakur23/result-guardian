"""Result intake and lab-flag shapes. Phase 2.3 / 2.4, extended by 3.7.

Phase 2 was deliberately thin on the clinical side: ``raw_payload`` was
accepted as an opaque object and stored as given, because *what is in a
result* was Phase 3's problem and a Phase 2 schema describing analytes would
have been a second, competing definition to migrate away from.

Phase 3 is that definition. ``analytes``, ``organisms`` and ``narratives`` are
the structured content the rule engine reads, and they are **optional** on
intake — a Phase 2 caller that sends only ``raw_payload`` behaves exactly as
it did before, which is what keeps the earlier phase intact.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class AnalyteIn(BaseModel):
    """One row of a numeric panel. Phase 3.7's numeric form."""

    model_config = ConfigDict(extra="forbid")

    test_name: str = Field(min_length=1, max_length=300)
    # Both are optional and at least one is expected: a lab reports "<0.01" as
    # text with no number, and discarding that would throw away the most
    # extreme results on the report.
    value_numeric: Decimal | None = None
    value_raw: str | None = Field(default=None, max_length=120)
    unit: str | None = Field(default=None, max_length=64)
    ref_low: Decimal | None = None
    ref_high: Decimal | None = None
    ref_text: str | None = Field(default=None, max_length=200)
    loinc_code: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _needs_a_value(self) -> AnalyteIn:
        if self.value_numeric is None and not (self.value_raw or "").strip():
            raise ValueError("an analyte needs either value_numeric or value_raw")
        return self

    @model_validator(mode="after")
    def _range_is_ordered(self) -> AnalyteIn:
        if (
            self.ref_low is not None
            and self.ref_high is not None
            and self.ref_low > self.ref_high
        ):
            # A transposed range silently inverts every comparison Rule A
            # makes, so it is rejected at the door rather than classified.
            raise ValueError("ref_low must not be greater than ref_high")
        return self


class SensitivityIn(BaseModel):
    """One cell of the antibiotic x S/I/R grid."""

    model_config = ConfigDict(extra="forbid")

    antibiotic_name: str = Field(min_length=1, max_length=200)
    interpretation: Literal["S", "I", "R"]
    mic_value: str | None = Field(default=None, max_length=32)


class OrganismIn(BaseModel):
    """One organism and its panel. Phase 3.7's culture form."""

    model_config = ConfigDict(extra="forbid")

    organism_name: str = Field(min_length=1, max_length=200)
    colony_count: str | None = Field(
        default=None,
        max_length=64,
        description='As the lab wrote it — ">100,000 CFU/mL", "1.5 x 10^5".',
    )
    specimen_type: str | None = Field(default=None, max_length=64)
    sensitivities: list[SensitivityIn] = Field(default_factory=list)


class NarrativeIn(BaseModel):
    """One prose section. Phase 3.7's narrative form."""

    model_config = ConfigDict(extra="forbid")

    section: Literal["impression", "findings", "conclusion", "microscopy"]
    text: str = Field(min_length=1)


class ResultContent(BaseModel):
    """The structured content of a report, independent of intake.

    Shared by ``ResultCreate`` and the preview endpoint so the preview grades
    exactly what the save will store — a preview that read a different shape
    would be a prediction of a different report.
    """

    analytes: list[AnalyteIn] = Field(default_factory=list)
    organisms: list[OrganismIn] = Field(default_factory=list)
    narratives: list[NarrativeIn] = Field(default_factory=list)


class ResultCreate(ResultContent):
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


class RulePreviewRow(BaseModel):
    """What one rule said about one piece of content."""

    rule_id: str
    severity: str
    reason_code: str
    subject: str | None = Field(
        default=None,
        description="The analyte, organism or section this line is about.",
    )
    offending_drug: str | None = None
    alternatives_available: list[str] = Field(default_factory=list)


class ResultPreview(BaseModel):
    """Phase 3.7: *"preview panel showing predicted severity before save"*.

    **Predicted, and nothing more.** Nothing is written, no case moves, no
    timer is set and no notification is queued. The severity here is what the
    engine *would* decide on this content as typed; the decision of record is
    made when the result is saved and is written to ``classifications`` with
    its engine version.
    """

    severity: str
    engine_version: str
    would_auto_close: bool = Field(
        description=(
            "Every rule permits closing. A narrative section always makes this "
            "false — narrative reports never auto-close."
        )
    )
    rules: list[RulePreviewRow] = Field(default_factory=list)
    discharge_antibiotics: list[str] = Field(
        default_factory=list,
        description=(
            "The drugs this patient actually went home on, which Rule B "
            "compared the culture against. Empty means none were recorded — "
            "which is itself why a culture may come back FOLLOW_UP."
        ),
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
