"""SQLAlchemy models. Phase 1.1 onwards.

Importing this package registers every model on ``Base.metadata`` so Alembic
autogenerate can see them. ``app/db/base.py`` imports it for that reason.

Phase 1.1 is complete: all eleven tables of the build plan's schema list are
modelled here, across migrations 0002 (the first six) and 0003 (the rest).
Phase 2.1 adds the twelfth, ``sla_timers``, in migration 0005; Phase 2.3/2.4
add ``lab_flags`` and ``results`` in 0006. Phase 3.1/3.2/3.6 add the result
detail tables, the rule-engine configuration tables and ``classifications``
in 0007. Phase 4.1/4.3/4.6 add the ownership, escalation and notification
tables in 0008.
"""

from __future__ import annotations

from app.db.models.audit import AuditAnchor, AuditLog, Session
from app.db.models.cases import CaseEvent, PendingCase
from app.db.models.discharge import (
    DischargeContract,
    DischargeContractRevision,
    DischargeMedication,
    DischargeOverride,
)
from app.db.models.encounters import Encounter
from app.db.models.infra import SystemSetting, WorkerHealth
from app.db.models.lab import LabFlag
from app.db.models.notifications import Notification, PatientContact
from app.db.models.orders import Order
from app.db.models.organisation import Department, User
from app.db.models.ownership import DutyRoster, EscalationChain, UserAbsence
from app.db.models.patients import Patient
from app.db.models.result_details import (
    ResultAnalyte,
    ResultNarrative,
    ResultOrganism,
    ResultSensitivity,
)
from app.db.models.results import Result
from app.db.models.rules_config import (
    AntibioticSynonym,
    Classification,
    ClinicalKeyword,
    MdroRule,
    NegationPattern,
    PanicThreshold,
    RuleConfig,
    UnitConversion,
)
from app.db.models.timers import SlaTimer

__all__ = [
    "AntibioticSynonym",
    "AuditAnchor",
    "AuditLog",
    "CaseEvent",
    "Classification",
    "ClinicalKeyword",
    "Department",
    "DischargeContract",
    "DischargeContractRevision",
    "DischargeMedication",
    "DischargeOverride",
    "DutyRoster",
    "Encounter",
    "EscalationChain",
    "LabFlag",
    "MdroRule",
    "NegationPattern",
    "Notification",
    "Order",
    "PanicThreshold",
    "Patient",
    "PatientContact",
    "PendingCase",
    "Result",
    "ResultAnalyte",
    "ResultNarrative",
    "ResultOrganism",
    "ResultSensitivity",
    "RuleConfig",
    "Session",
    "SlaTimer",
    "SystemSetting",
    "UnitConversion",
    "User",
    "UserAbsence",
    "WorkerHealth",
]
