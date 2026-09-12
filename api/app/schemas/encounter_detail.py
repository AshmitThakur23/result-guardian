"""The encounter detail screen's payload. Phase 1.5.

*"Encounter detail: orders list with status, discharge medications,
contracts."* All three in one read, because the screen shows all three at once
and three round trips would let them disagree with each other -- an order
could gain a contract between call two and call three, and the page would
render a state that never existed.

Composed from the Phase 1.4 pieces rather than redefining them: the patient
and doctor shapes are the same ``PatientSummary`` / ``UserSummary`` the gate
already uses.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, Field

from app.schemas.directory import PatientSummary, UserSummary
from app.schemas.medications import DischargeMedicationRow
from app.schemas.orders import OrderRow


class EncounterFullDetail(BaseModel):
    """Everything the detail screen renders, derived in one transaction."""

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

    orders: list[OrderRow] = Field(
        default_factory=list, description="Newest order first."
    )
    medications: list[DischargeMedicationRow] = Field(default_factory=list)

    # The gate's answer, carried here so the detail screen can say whether
    # this encounter is dischargeable without running the rule itself.
    gate_applies: bool
    can_discharge: bool
    blocking_order_count: int
    can_add_orders: bool = Field(
        description=(
            "False once the encounter leaves 'active'. The server enforces "
            "this under a row lock regardless; this only stops the UI from "
            "offering a button that would be refused."
        )
    )
