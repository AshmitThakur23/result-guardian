"""Read-only lookups the Phase 1.4 gate screen needs. Phase 1.3 addendum.

Two things the gate cannot be built without: a way to pick a responsible
doctor, and the encounter's attending doctor to default that pick to. Both are
plain reads -- no new tables, no migration, no write path.

Deliberately minimal. Full user management is Phase 5.4 (admin) and
availability comes from ``duty_roster`` / ``user_absences`` in Phase 4.1;
neither is anticipated here.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class UserSummary(BaseModel):
    """Enough to populate a searchable select and print a name on a review
    screen. No password hash, no email, no phone -- the gate needs none of it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_code: str
    full_name: str
    role: str
    is_active: bool
    department_id: uuid.UUID | None = None


class PatientSummary(BaseModel):
    """Identity for the gate header only. No address, no phone, no consent
    fields -- a discharge screen has no business rendering them."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mrn: str
    name: str


class EncounterDetail(BaseModel):
    """Context for the gate header, and the attending doctor that Step 2
    defaults the responsible-doctor field to."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    encounter_no: str
    type: str
    status: str
    admitted_at: dt.datetime
    discharged_at: dt.datetime | None = None
    ward: str | None = None
    bed: str | None = None
    department_id: uuid.UUID | None = None
    patient: PatientSummary
    attending_doctor: UserSummary | None = None
