"""SQLAlchemy models. Phase 1.1 onwards.

Importing this package registers every model on ``Base.metadata`` so Alembic
autogenerate can see them. ``app/db/base.py`` imports it for that reason.

Phase 1.1 covers the first six tables of the build plan's schema list. The
remaining five -- ``discharge_contract_revisions``, ``pending_cases``,
``case_events``, ``discharge_medications`` and ``discharge_overrides`` -- are
still outstanding and are tracked in the phase doc.
"""

from __future__ import annotations

from app.db.models.discharge import DischargeContract
from app.db.models.encounters import Encounter
from app.db.models.infra import WorkerHealth
from app.db.models.orders import Order
from app.db.models.organisation import Department, User
from app.db.models.patients import Patient

__all__ = [
    "Department",
    "DischargeContract",
    "Encounter",
    "Order",
    "Patient",
    "User",
    "WorkerHealth",
]
