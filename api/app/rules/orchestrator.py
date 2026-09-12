"""The classification orchestrator. Phase 3.6.

    ``classify_result(result_id)`` → picks rules **by content, not by report
    type** (a radiology report can contain numbers)

That parenthesis is the whole design. Dispatching on ``orders.category`` would
mean a potassium value mentioned in a radiology report never reaches Rule A.
So the orchestrator asks what the result *contains* — analytes, organisms,
narrative — and runs whichever rules have something to read.

    Combine: overall severity = **max** of all rule outputs

Max, never average and never "the last rule wins": if any rule says critical,
the result is critical.

**Idempotency.** ``classifications`` is unique on ``(result_id,
engine_version)``. A duplicate queue delivery recomputes the same answer and
the insert does nothing; a genuine re-run under a *new* engine version records
a second, separate decision alongside the first. Neither produces a duplicate
clinical action, because the case transition and the events are written only
by the caller that actually inserted the row.

**Auto-close is permitted only when every rule agrees it is safe.** Rule B may
say a culture is covered; Rule C can never say a narrative is closeable. One
narrative section on the report is enough to keep the case open — the plan's
critical safety rule, enforced by taking the AND across rules rather than by
remembering to check.

No AI, no NODE B, no network. Every decision is a table lookup and a
comparison, and every one carries a reason code.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from decimal import Decimal

import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.types import uuid7
from app.rules import (
    ENGINE_VERSION,
    SEVERITY_CRITICAL,
    SEVERITY_FOLLOW_UP,
    SEVERITY_NORMAL,
    max_severity,
)
from app.rules.culture import RULE_ID as CULTURE_RULE_ID
from app.rules.culture import (
    DischargeDrug,
    classify_organism,
    classify_result_culture,
)
from app.rules.narrative import RULE_ID as NARRATIVE_RULE_ID
from app.rules.narrative import classify_narrative_text, classify_result_narratives
from app.rules.numeric import RuleOutput, classify_analyte, classify_result_analytes
from app.services.lab_flags import record_event

log = structlog.get_logger(__name__)

# A rule raised instead of answering. The result is still classified, as
# FOLLOW_UP, so the case reaches a human rather than going quiet.
REASON_RULE_FAILED = "ORCH_RULE_FAILED"

EVENT_CLASSIFIED = "result_classified"
EVENT_AUTO_CLOSED = "case_auto_closed_by_rule_engine"
EVENT_AMENDMENT_REOPENED = "case_reopened_for_amendment"

# Phase 3.6: "Preliminary result -> hold, set stale_preliminary timer
# (default 48h), do not alert unless severity is CRITICAL."
DEFAULT_PRELIMINARY_HOLD_HOURS = 48

# An amended or corrected report replaces an earlier one and may reopen a
# closed case.
AMENDING_STATUSES = ("amended", "corrected")


class ResultNotFoundError(LookupError):
    """No such result. The router turns this into a 404."""


@dataclass
class Classification:
    result_id: uuid.UUID
    case_id: uuid.UUID | None
    severity: str
    engine_version: str
    rule_outputs: list[RuleOutput] = field(default_factory=list)
    classification_id: uuid.UUID | None = None
    created: bool = True
    """False when this result was already classified by this engine version."""
    auto_closed: bool = False
    held_as_preliminary: bool = False
    reopened: bool = False


async def _preliminary_hold_hours(session: AsyncSession) -> int:
    row = (
        await session.execute(
            text(
                "SELECT value FROM rule_config "
                " WHERE key = 'preliminary_hold_hours' AND deleted_at IS NULL"
            )
        )
    ).first()
    if row is None:
        return DEFAULT_PRELIMINARY_HOLD_HOURS
    raw = row.value
    candidate = raw.get("hours") if isinstance(raw, dict) else raw
    try:
        return int(str(candidate))
    except (TypeError, ValueError):
        return DEFAULT_PRELIMINARY_HOLD_HOURS


def _age_years(dob: dt.date | None, at: dt.datetime) -> Decimal | None:
    if dob is None:
        return None
    days = (at.date() - dob).days
    if days < 0:
        return None
    return Decimal(days) / Decimal(365)


async def classify_result(
    session: AsyncSession,
    result_id: uuid.UUID,
    *,
    engine_version: str = ENGINE_VERSION,
    at: dt.datetime | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> Classification:
    """Classify one result. Idempotent per ``(result_id, engine_version)``.

    Runs in the caller's transaction so the classification row, the case
    transition, the events and any timer all commit together — a severity
    recorded without the case moving would be a decision nobody acts on.
    """
    moment = at or dt.datetime.now(dt.UTC)

    context = (
        await session.execute(
            text(
                "SELECT r.id, r.case_id, r.report_status, o.category, "
                "       p.sex, p.dob "
                "  FROM results r "
                "  JOIN orders o ON o.id = r.order_id "
                "  JOIN patients p ON p.id = o.patient_id "
                " WHERE r.id = :r AND r.deleted_at IS NULL"
            ),
            {"r": str(result_id)},
        )
    ).first()
    if context is None:
        raise ResultNotFoundError(str(result_id))

    # ── run whichever rules have something to read ────────────────
    # Each rule is run behind _safely(), which turns a rule that raises into a
    # FOLLOW_UP rather than letting the exception escape. That matters more
    # than it looks: intake has already superseded the case's `result_due`
    # timer, so a case whose classification fails permanently has **no pending
    # timer and no flag** — nothing left to wake anybody. The message would
    # retry five times and land in the DLQ, where it is safe but unread, and
    # the patient's case would sit silent. THE ONE RULE: degrade to the
    # previous phase, not to silence.
    outputs: list[RuleOutput] = []
    outputs += await _safely(
        "A_numeric",
        classify_result_analytes(
            session,
            result_id,
            sex=context.sex,
            age_years=_age_years(context.dob, moment),
            at=moment,
        ),
    )
    outputs += await _safely("B_culture", classify_result_culture(session, result_id))
    outputs += await _safely(
        "C_narrative",
        classify_result_narratives(session, result_id, order_category=context.category),
    )

    if not outputs:
        # A result with no structured content at all. Not classifiable, and
        # "not classifiable" is never NORMAL.
        outputs = [
            RuleOutput(
                severity=SEVERITY_FOLLOW_UP,
                rule_id="orchestrator",
                reason_code="ORCH_NO_CLASSIFIABLE_CONTENT",
                inputs_used={"result_id": str(result_id)},
            )
        ]

    severity = max_severity(*(o.severity for o in outputs))

    # ── write the decision, idempotently ──────────────────────────
    classification_id = uuid7()
    inserted = (
        await session.execute(
            text(
                "INSERT INTO classifications "
                "(id, result_id, case_id, severity, rule_outputs, "
                " engine_version, classified_at, created_by, updated_by) "
                "VALUES (:i, :r, :c, :s, CAST(:ro AS jsonb), :v, :t, :a, :a) "
                "ON CONFLICT (result_id, engine_version) DO NOTHING "
                "RETURNING id"
            ),
            {
                "i": str(classification_id),
                "r": str(result_id),
                "c": str(context.case_id) if context.case_id else None,
                "s": severity,
                "ro": json.dumps([o.as_dict() for o in outputs]),
                "v": engine_version,
                "t": moment,
                "a": str(actor_user_id) if actor_user_id else None,
            },
        )
    ).first()

    if inserted is None:
        # Already classified by this engine version. A duplicate delivery, a
        # retry, or a second worker -- every one of them must stop here rather
        # than repeat the case transition below.
        existing = (
            await session.execute(
                text(
                    "SELECT id, severity FROM classifications "
                    " WHERE result_id = :r AND engine_version = :v"
                ),
                {"r": str(result_id), "v": engine_version},
            )
        ).one()
        return Classification(
            result_id=result_id,
            case_id=context.case_id,
            severity=existing.severity,
            engine_version=engine_version,
            rule_outputs=outputs,
            classification_id=existing.id,
            created=False,
        )

    result = Classification(
        result_id=result_id,
        case_id=context.case_id,
        severity=severity,
        engine_version=engine_version,
        rule_outputs=outputs,
        classification_id=classification_id,
        created=True,
    )

    if context.case_id is None:
        # No tracking case (a result for an order that never went through a
        # discharge). The decision is recorded; there is nothing to move.
        return result

    case_id = uuid.UUID(str(context.case_id))

    await record_event(
        session,
        case_id,
        EVENT_CLASSIFIED,
        {
            "result_id": str(result_id),
            "severity": severity,
            "engine_version": engine_version,
            "reason_codes": [o.reason_code for o in outputs],
            "rule_ids": sorted({o.rule_id for o in outputs}),
        },
        actor_user_id=actor_user_id,
        now=moment,
    )

    # ── amended / corrected reports reopen a closed case ──────────
    if context.report_status in AMENDING_STATUSES:
        result.reopened = await _reopen_if_closed(
            session, case_id, result_id, severity, moment, actor_user_id
        )

    # ── preliminary results are held ──────────────────────────────
    if context.report_status == "preliminary":
        result.held_as_preliminary = True
        await _hold_preliminary(
            session, case_id, result_id, severity, moment, actor_user_id
        )
        # "do not alert unless severity is CRITICAL" -- the severity is
        # recorded either way; Phase 4 reads `classifications` and this event
        # to decide whether to dispatch.
        await _set_case_severity(session, case_id, severity, moment)
        return result

    await _set_case_severity(session, case_id, severity, moment)

    # ── auto-close, only when every rule permits it ───────────────
    if severity == SEVERITY_NORMAL and _all_rules_permit_auto_close(outputs):
        from app.services.cases import close_case

        closed = await close_case(
            session,
            case_id,
            reason="auto_closed_normal",
            note=(
                "Rule engine "
                f"{engine_version}: "
                + ", ".join(sorted({o.reason_code for o in outputs}))
            ),
            actor_user_id=actor_user_id,
            now=moment,
        )
        if not closed.already_closed:
            result.auto_closed = True
            await record_event(
                session,
                case_id,
                EVENT_AUTO_CLOSED,
                {
                    "result_id": str(result_id),
                    "engine_version": engine_version,
                    "reason_codes": [o.reason_code for o in outputs],
                },
                actor_user_id=actor_user_id,
                now=moment,
            )

    return result


async def _safely(
    rule_id: str, coroutine: Coroutine[object, object, list[RuleOutput]]
) -> list[RuleOutput]:
    """Run one rule; a failure becomes FOLLOW_UP instead of an exception.

    A rule that raises is a rule that could not read the result — a malformed
    configuration row, an unparseable value, a regex an admin typed. None of
    those is a reason to leave the case unclassified, because an unclassified
    case is a silent one: intake has already cancelled its ``result_due``
    timer, so nothing will fire for it again.

    **Database failures are re-raised**, deliberately. A lost connection is
    not a classification outcome; the message should go back on the queue and
    be retried, which is exactly what the consumer does with an exception.
    """
    try:
        return await coroutine
    except DBAPIError:
        # Infrastructure, not content. Let the queue retry it.
        raise
    except Exception as exc:
        log.exception("rule_failed", rule_id=rule_id, error=str(exc))
        return [
            RuleOutput(
                severity=SEVERITY_FOLLOW_UP,
                rule_id=rule_id,
                reason_code=REASON_RULE_FAILED,
                inputs_used={"error_type": type(exc).__name__},
                detail={
                    "error": str(exc)[:500],
                    # Never auto-close on a rule that did not run.
                    "auto_close": False,
                },
            )
        ]


def _all_rules_permit_auto_close(outputs: list[RuleOutput]) -> bool:
    """AND across rules, with a default of *no*.

    A rule that does not say ``auto_close: True`` is treated as refusing. Rule
    C never sets it, so any narrative on the report keeps the case open — the
    plan's critical safety rule, enforced by the shape of this function rather
    than by remembering to special-case it.
    """
    return bool(outputs) and all(
        bool(o.detail.get("auto_close", False)) for o in outputs
    )


async def _set_case_severity(
    session: AsyncSession, case_id: uuid.UUID, severity: str, moment: dt.datetime
) -> None:
    """Move the case to ``classified`` (or ``flagged``) and record severity.

    ``flagged`` for anything a human must look at, with ``flagged_at`` set so
    Phase 4's escalation clock has a start. ``classified`` for normal.
    """
    state = "classified" if severity == SEVERITY_NORMAL else "flagged"
    await session.execute(
        text(
            "UPDATE pending_cases "
            # CAST on both uses of :state, and the same target type in each.
            # A bare parameter used once as a varchar column value and once
            # against a text literal makes asyncpg deduce two different types
            # for one placeholder and refuse the statement outright.
            "   SET severity = :sev, state = CAST(:state AS text), "
            "       flagged_at = CASE WHEN CAST(:state AS text) = 'flagged' "
            "                    THEN COALESCE(flagged_at, :t) "
            "                    ELSE flagged_at END, "
            "       updated_at = now() "
            " WHERE id = :i AND state NOT IN ('closed', 'acknowledged')"
        ),
        {"i": str(case_id), "sev": severity, "state": state, "t": moment},
    )


async def _hold_preliminary(
    session: AsyncSession,
    case_id: uuid.UUID,
    result_id: uuid.UUID,
    severity: str,
    moment: dt.datetime,
    actor_user_id: uuid.UUID | None,
) -> None:
    """Phase 3.6: hold, and set a ``stale_preliminary`` timer (default 48h).

    Phase 2.4 already creates one at 72h on intake; this replaces the default
    with the configured hold. ``create_timer`` is idempotent, so if the two
    land on the same instant only one timer exists.
    """
    from app.services.timers import create_timer

    hours = await _preliminary_hold_hours(session)
    created = await create_timer(
        session,
        case_id=case_id,
        timer_type="stale_preliminary",
        fire_at=moment + dt.timedelta(hours=hours),
        actor_user_id=actor_user_id,
        now=moment,
    )
    await record_event(
        session,
        case_id,
        "preliminary_result_held",
        {
            "result_id": str(result_id),
            "severity": severity,
            "hold_hours": hours,
            "timer_id": str(created.timer_id),
            "alert_suppressed": severity != SEVERITY_CRITICAL,
        },
        actor_user_id=actor_user_id,
        now=moment,
    )


async def _reopen_if_closed(
    session: AsyncSession,
    case_id: uuid.UUID,
    result_id: uuid.UUID,
    severity: str,
    moment: dt.datetime,
    actor_user_id: uuid.UUID | None,
) -> bool:
    """Phase 3.6: *"Amended / corrected result → reopen a closed case,
    increment ``reopened_count``, re-notify with **distinct wording** so the
    doctor knows it is an amendment, not a duplicate."*

    The distinct wording is carried by the template key, which Phase 4's
    dispatcher reads. Phase 3 records the obligation; it does not send.

    ``reopened`` is the transition out of ``closed``, not the resting state:
    it lifts the closed guard so ``_set_case_severity`` can move the case to
    ``flagged`` or ``classified`` on the amendment's own severity. A case left
    sitting in ``reopened`` would carry no severity and nobody would triage it.
    What records the history is ``reopened_count``, the ``case_reopened_for_
    amendment`` event and the distinct notification template — all three of
    which survive the later transition.
    """
    row = (
        await session.execute(
            text(
                "UPDATE pending_cases "
                "   SET state = 'reopened', reopened_count = reopened_count + 1, "
                "       closed_at = NULL, updated_at = now() "
                " WHERE id = :i AND state = 'closed' AND deleted_at IS NULL "
                " RETURNING reopened_count"
            ),
            {"i": str(case_id)},
        )
    ).first()
    if row is None:
        return False

    await record_event(
        session,
        case_id,
        EVENT_AMENDMENT_REOPENED,
        {
            "result_id": str(result_id),
            "severity": severity,
            "reopened_count": row.reopened_count,
        },
        actor_user_id=actor_user_id,
        now=moment,
    )

    from app.services.lab_flags import enqueue_notification

    await enqueue_notification(
        session,
        case_id,
        # Distinct from the first-notification template on purpose: a doctor
        # who sees the same wording twice reads it as a duplicate and ignores
        # it, which is exactly the failure an amendment must not have.
        "result_amended_case_reopened",
        {
            "audience": "responsible_doctor",
            "result_id": str(result_id),
            "severity": severity,
            "is_amendment": True,
        },
    )
    return True


class OrderNotFoundError(LookupError):
    """No such order. The preview router turns this into a 404."""


@dataclass
class Preview:
    """What the engine *would* decide. Phase 3.7."""

    severity: str
    engine_version: str
    would_auto_close: bool
    rule_outputs: list[RuleOutput] = field(default_factory=list)
    discharge_antibiotics: list[str] = field(default_factory=list)


async def preview_classification(
    session: AsyncSession,
    order_id: uuid.UUID,
    *,
    analytes: list[dict[str, object]],
    organisms: list[dict[str, object]],
    narratives: list[dict[str, object]],
    at: dt.datetime | None = None,
) -> Preview:
    """Grade content that has not been saved. Phase 3.7's preview panel.

    **Writes nothing.** No result row, no classification, no case transition,
    no timer, no notification — a lab tech looking at what a value would mean
    must not move a case by looking.

    It calls the same three rule functions the real classification calls, with
    the same configuration from the same tables, so the prediction and the
    decision cannot drift apart on the rules themselves. What it cannot share
    is the reading of rows that do not exist yet, so a test asserts that
    previewing a payload and then saving it produce the same severity.
    """
    moment = at or dt.datetime.now(dt.UTC)

    context = (
        await session.execute(
            text(
                "SELECT o.id, o.category, o.encounter_id, p.sex, p.dob "
                "  FROM orders o JOIN patients p ON p.id = o.patient_id "
                " WHERE o.id = :o AND o.deleted_at IS NULL"
            ),
            {"o": str(order_id)},
        )
    ).first()
    if context is None:
        raise OrderNotFoundError(str(order_id))

    drug_rows = (
        await session.execute(
            text(
                "SELECT drug_name FROM discharge_medications "
                " WHERE encounter_id = :e AND is_antibiotic AND deleted_at IS NULL "
                " ORDER BY drug_name"
            ),
            {"e": str(context.encounter_id)},
        )
    ).all()
    discharge_drugs = [DischargeDrug(drug_name=row.drug_name) for row in drug_rows]

    age = _age_years(context.dob, moment)
    outputs: list[RuleOutput] = []

    for analyte in analytes:
        outputs.append(
            await classify_analyte(
                session,
                test_code=str(analyte.get("test_name") or ""),
                value_numeric=_as_decimal(analyte.get("value_numeric")),
                value_raw=_as_str(analyte.get("value_raw")),
                unit_normalized=_as_str(analyte.get("unit")),
                ref_low=_as_decimal(analyte.get("ref_low")),
                ref_high=_as_decimal(analyte.get("ref_high")),
                ref_text=_as_str(analyte.get("ref_text")),
                sex=context.sex,
                age_years=age,
                at=moment,
            )
        )

    for organism in organisms:
        raw_sensitivities = organism.get("sensitivities")
        pairs = [
            (str(s["antibiotic_name"]), str(s["interpretation"]))
            for s in (raw_sensitivities if isinstance(raw_sensitivities, list) else [])
            if isinstance(s, dict)
        ]
        finding = await classify_organism(
            session,
            organism_name=str(organism.get("organism_name") or ""),
            colony_count=_as_str(organism.get("colony_count")),
            specimen_type=_as_str(organism.get("specimen_type")),
            sensitivities=pairs,
            discharge_drugs=discharge_drugs,
        )
        outputs.append(
            RuleOutput(
                severity=finding.severity,
                rule_id=CULTURE_RULE_ID,
                reason_code=finding.reason_code,
                inputs_used={
                    "organism": finding.organism,
                    "discharge_antibiotics": [d.drug_name for d in discharge_drugs],
                },
                detail={
                    "offending_drug": finding.offending_drug,
                    "alternatives_available": finding.alternatives_available,
                    "auto_close": finding.auto_close,
                    **finding.detail,
                },
            )
        )

    for narrative in narratives:
        verdict = await classify_narrative_text(
            session,
            str(narrative.get("text") or ""),
            category=context.category,
        )
        outputs.append(
            RuleOutput(
                severity=verdict.severity,
                rule_id=NARRATIVE_RULE_ID,
                reason_code=verdict.reason_code,
                inputs_used={"section": narrative.get("section")},
                detail={
                    "matched_terms": verdict.matched_terms,
                    # Same literal as the real path, and for the same reason:
                    # narrative reports never auto-close.
                    "auto_close": False,
                },
            )
        )

    if not outputs:
        outputs = [
            RuleOutput(
                severity=SEVERITY_FOLLOW_UP,
                rule_id="orchestrator",
                reason_code="ORCH_NO_CLASSIFIABLE_CONTENT",
                inputs_used={"order_id": str(order_id)},
            )
        ]

    return Preview(
        severity=max_severity(*(o.severity for o in outputs)),
        engine_version=ENGINE_VERSION,
        would_auto_close=(
            max_severity(*(o.severity for o in outputs)) == SEVERITY_NORMAL
            and _all_rules_permit_auto_close(outputs)
        ),
        rule_outputs=outputs,
        discharge_antibiotics=[d.drug_name for d in discharge_drugs],
    )


def _as_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _as_str(value: object) -> str | None:
    return None if value is None else str(value)


# Re-exported so callers need not import from three modules.
__all__ = [
    "SEVERITY_CRITICAL",
    "SEVERITY_FOLLOW_UP",
    "SEVERITY_NORMAL",
    "Classification",
    "ResultNotFoundError",
    "classify_result",
]
