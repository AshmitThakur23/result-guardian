"""SQLAlchemy models. Phase 1.1 onwards.

Importing this package registers every model on ``Base.metadata`` so Alembic
autogenerate can see them. ``app/db/base.py`` imports it for that reason.

Phase 1.1 is complete: all eleven tables of the build plan's schema list are
modelled here, across migrations 0002 (the first six) and 0003 (the rest).
"""

from __future__ import annotations

from app.db.models.cases import CaseEvent, PendingCase
from app.db.models.discharge import (
    DischargeContract,
    DischargeContractRevision,
    DischargeMedication,
    DischargeOverride,
)
from app.db.models.encounters import Encounter
from app.db.models.infra import WorkerHealth
from app.db.models.orders import Order
from app.db.models.organisation import Department, User
from app.db.models.patients import Patient

__all__ = [
    "CaseEvent",
    "Department",
    "DischargeContract",
    "DischargeContractRevision",
    "DischargeMedication",
    "DischargeOverride",
    "Encounter",
    "Order",
    "Patient",
    "PendingCase",
    "User",
    "WorkerHealth",
]
