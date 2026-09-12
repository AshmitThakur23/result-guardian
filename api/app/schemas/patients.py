"""Patient search. Phase 1.5.

The build plan asks for search by **MRN, name, phone**. All three are
identifiers a ward clerk actually has to hand, and all three go through one
query rather than three separate modes -- a clerk should not have to tell the
system which kind of string they just typed.

Read-only. Nothing here creates or edits a patient: patient registration
belongs to the HIS, and the manual path for it is Phase 9, not 1.5.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class PatientSearchRow(BaseModel):
    """One search hit.

    Deliberately narrow. A search result list is the widest-read screen in the
    product, so it carries identity and nothing else -- no address, no consent
    state, no SMS basis. The encounter detail screen fetches what it needs.

    ``phone_primary_e164`` is included because the clerk may have searched by
    it and needs to see which of two similarly-named patients matched.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mrn: str
    name: str
    dob: dt.date | None = None
    sex: str | None = None
    phone_primary_e164: str | None = None
    active_encounter_count: int = Field(
        default=0,
        description=(
            "How many of this patient's encounters are still active. Lets the "
            "clerk go straight to the one that needs discharging."
        ),
    )


class PatientEncounterRow(BaseModel):
    """An encounter in the patient's list, enough to choose between them."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    encounter_no: str
    type: str
    status: str
    admitted_at: dt.datetime
    discharged_at: dt.datetime | None = None
    ward: str | None = None
    bed: str | None = None


class PatientWithEncounters(BaseModel):
    """A patient and their encounters, newest admission first."""

    patient: PatientSearchRow
    encounters: list[PatientEncounterRow]
