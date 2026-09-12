"""Manual result intake. Phase 2.4.

    ``POST /api/orders/{id}/results`` — accepts a structured payload.
    Transitions case to ``result_received``, cancels ``result_due``, enqueues
    classification.

    **This endpoint stays forever.** Phases 6-7 just add an automatic caller
    for it. It is also the permanent fallback when extraction fails.

That last sentence shapes everything here. This is not a test fixture or a
stopgap: it is the one door every result comes through, and the PDF pipeline
in Phase 6 and the HL7 feed in Phase 9 will both end up calling this same
function. So it validates properly, it is safe to replay, and it records what
it did.

**It interprets nothing.** The payload is stored as given; whether the value
is critical is Phase 3's rule engine, and extracting it from a document is
Phase 7. RULE 1 again: the tracking guarantee must not wait on interpretation,
so intake closes the loop on *"a result arrived"* and hands the rest onward.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.results import NON_FINAL_REPORT_STATUSES
from app.db.types import uuid7
from app.schemas.results import ResultContent
from app.services.lab_flags import record_event
from app.services.timers import create_timer, supersede_result_due

CLASSIFY_QUEUE = "classify"

EVENT_RESULT_RECEIVED = "result_received"
EVENT_RESULT_SUPERSEDED_TIMER = "result_due_superseded"

# Phase 2.2: "Supersede: new result arrives -> cancel result_due, create
# classification timers." A preliminary report is the one case where a further
# deadline genuinely exists -- the final report is still owed -- so it gets a
# stale_preliminary timer. A final/amended/corrected report owes nothing more,
# and inventing a timer for it would be a deadline nobody asked for.
STALE_PRELIMINARY_AFTER = dt.timedelta(hours=72)

# Case states in which a result can still be accepted as "the" result. A
# closed case accepts one too (see below), but does not reopen itself here.
STATES_OPEN_TO_RESULT = ("awaiting_result", "result_received", "classified", "flagged")


class OrderNotFoundError(LookupError):
    """No such order. The router turns this into a 404."""


class DuplicateResultError(Exception):
    """This exact report was already taken in. Router -> 409."""

    def __init__(self, detail: str, result_id: uuid.UUID) -> None:
        self.detail = detail
        self.result_id = result_id
        super().__init__(detail)


@dataclass
class ResultIntake:
    result_id: uuid.UUID
    order_id: uuid.UUID
    case_id: uuid.UUID | None
    case_state: str | None
    superseded_timer_ids: list[uuid.UUID] = field(default_factory=list)
    classification_msg_id: int | None = None
    created: bool = True
    late: bool = False
    """True when the case had already closed before this arrived."""


async def record_result(
    session: AsyncSession,
    order_id: uuid.UUID,
    *,
    report_status: str,
    source: str = "manual",
    source_ref: str | None = None,
    reported_at: dt.datetime | None = None,
    raw_payload: dict[str, object] | None = None,
    content: ResultContent | None = None,
    actor_user_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> ResultIntake:
    """Take in one result and move its case on.

    Everything below runs in the caller's transaction. The result row, the
    case transition, the timer supersession, the events and the classification
    hand-off commit together — a result that is recorded but whose
    ``result_due`` timer survives would fire later and flag a lab for a report
    that had already arrived.

    Ordering inside the transaction is deliberate:

    1. Lock the case, so a concurrent fire handler either finishes first or
       waits. This is the same lock the worker takes, which is what makes
       "result arrives while the timer is firing" safe in both directions.
    2. Insert the result and its structured content.
    3. Supersede ``result_due``.
    4. Transition the case.
    5. Enqueue classification.

    ``content`` (Phase 3.7) is optional. A caller that sends only
    ``raw_payload`` gets exactly the Phase 2 behaviour it got before — the
    rule engine then finds nothing to read and says FOLLOW_UP, which is the
    honest answer for a report nobody has structured.
    """
    moment = now or dt.datetime.now(dt.UTC)

    order = (
        await session.execute(
            text(
                "SELECT o.id, o.encounter_id, o.test_name "
                "  FROM orders o WHERE o.id = :o AND o.deleted_at IS NULL"
            ),
            {"o": str(order_id)},
        )
    ).first()
    if order is None:
        raise OrderNotFoundError(str(order_id))

    # ── replay protection, before anything is written ─────────────
    if source_ref is not None:
        existing = (
            await session.execute(
                text(
                    "SELECT id, order_id, case_id FROM results "
                    " WHERE source = :s AND source_ref = :r AND deleted_at IS NULL"
                ),
                {"s": source, "r": source_ref},
            )
        ).first()
        if existing is not None:
            # The same report delivered twice. Returning the original rather
            # than raising would hide a genuine mis-send; the router turns
            # this into a 409 that names the result already on file.
            raise DuplicateResultError(
                f"A {source} result with reference '{source_ref}' is already recorded.",
                existing.id,
            )

    # ── lock the case, if there is one ────────────────────────────
    # A result can legitimately arrive for an order that never went through a
    # discharge: no case exists, and Phase 7.5 routes those to an orphan
    # queue. Phase 2 stores the result and stops.
    case = (
        await session.execute(
            text(
                "SELECT id, state FROM pending_cases "
                " WHERE order_id = :o AND deleted_at IS NULL FOR UPDATE"
            ),
            {"o": str(order_id)},
        )
    ).first()

    result_id = uuid7()
    await session.execute(
        text(
            "INSERT INTO results "
            "(id, order_id, case_id, report_status, reported_at, received_at, "
            " source, source_ref, raw_payload, created_by, updated_by) "
            "VALUES (:i, :o, :c, :rs, :rep, :recv, :src, :ref, "
            "        CAST(:payload AS jsonb), :a, :a)"
        ),
        {
            "i": str(result_id),
            "o": str(order_id),
            "c": str(case.id) if case else None,
            "rs": report_status,
            "rep": reported_at,
            "recv": moment,
            "src": source,
            "ref": source_ref,
            "payload": json.dumps(raw_payload or {}),
            "a": str(actor_user_id) if actor_user_id else None,
        },
    )

    # Content is written before the case moves on, so a result that reaches
    # `result_received` is always a result the rule engine can actually read.
    if content is not None:
        await _store_content(session, result_id, content, actor_user_id)

    if case is None:
        return ResultIntake(
            result_id=result_id,
            order_id=order_id,
            case_id=None,
            case_state=None,
        )

    late = case.state == "closed"

    # ── supersede the deadline this result answers ────────────────
    superseded = await supersede_result_due(session, case.id)
    if superseded:
        await record_event(
            session,
            case.id,
            EVENT_RESULT_SUPERSEDED_TIMER,
            {
                "result_id": str(result_id),
                "timer_ids": [str(t) for t in superseded],
            },
            actor_user_id=actor_user_id,
            now=moment,
        )

    # ── transition the case ───────────────────────────────────────
    # Only forward, and only from a state where the result is still news. A
    # closed case is NOT reopened here: reopening is a Phase 5.3 decision with
    # its own reason codes, and quietly doing it from an intake endpoint would
    # bypass that.
    new_state = case.state
    if case.state in STATES_OPEN_TO_RESULT:
        new_state = "result_received"
        await session.execute(
            text(
                "UPDATE pending_cases "
                "   SET state = 'result_received', "
                "       result_received_at = COALESCE(result_received_at, :t), "
                "       updated_by = :a, updated_at = now() "
                " WHERE id = :i"
            ),
            {
                "i": str(case.id),
                "t": moment,
                "a": str(actor_user_id) if actor_user_id else None,
            },
        )

    await record_event(
        session,
        case.id,
        EVENT_RESULT_RECEIVED,
        {
            "result_id": str(result_id),
            "report_status": report_status,
            "source": source,
            "source_ref": source_ref,
            "test_name": order.test_name,
            "late": late,
            "case_state": new_state,
        },
        actor_user_id=actor_user_id,
        now=moment,
    )

    # ── "create classification timers" ────────────────────────────
    if report_status in NON_FINAL_REPORT_STATUSES:
        await create_timer(
            session,
            case_id=case.id,
            timer_type="stale_preliminary",
            fire_at=moment + STALE_PRELIMINARY_AFTER,
            order_id=order_id,
            encounter_id=order.encounter_id,
            actor_user_id=actor_user_id,
            now=moment,
        )

    # ── hand off to the rule engine ───────────────────────────────
    # Phase 3 consumes this. Enqueued in the same transaction so a result that
    # is recorded is always a result that will be classified.
    msg_id = (
        await session.execute(
            text("SELECT pgmq.send(:q, CAST(:p AS jsonb))"),
            {
                "q": CLASSIFY_QUEUE,
                "p": json.dumps(
                    {
                        "result_id": str(result_id),
                        "case_id": str(case.id),
                        "order_id": str(order_id),
                        "report_status": report_status,
                    }
                ),
            },
        )
    ).scalar()

    return ResultIntake(
        result_id=result_id,
        order_id=order_id,
        case_id=case.id,
        case_state=new_state,
        superseded_timer_ids=superseded,
        classification_msg_id=int(msg_id or 0),
        late=late,
    )


async def _store_content(
    session: AsyncSession,
    result_id: uuid.UUID,
    content: ResultContent,
    actor_user_id: uuid.UUID | None,
) -> None:
    """Write the structured content of a report. Phase 3.7.

    Stored as typed rows rather than inside ``raw_payload`` because the rule
    engine reads them and because a sensitivity grid buried in JSON cannot be
    queried, corrected or audited. ``raw_payload`` still holds whatever the
    sender gave; these rows are the interpretation of it.
    """
    actor = str(actor_user_id) if actor_user_id else None

    for seq, analyte in enumerate(content.analytes, start=1):
        await session.execute(
            text(
                "INSERT INTO result_analytes "
                "(id, result_id, seq, test_name_raw, loinc_code, value_raw, "
                " value_numeric, unit_raw, unit_normalized, ref_low, ref_high, "
                " ref_text, created_by, updated_by) "
                "VALUES (:i, :r, :seq, :name, :loinc, :vraw, "
                "        CAST(:vnum AS numeric), :unit, :unit, "
                "        CAST(:lo AS numeric), CAST(:hi AS numeric), :rt, :a, :a)"
            ),
            {
                "i": str(uuid7()),
                "r": str(result_id),
                "seq": seq,
                "name": analyte.test_name,
                "loinc": analyte.loinc_code,
                "vraw": analyte.value_raw,
                "vnum": (
                    str(analyte.value_numeric)
                    if analyte.value_numeric is not None
                    else None
                ),
                "unit": analyte.unit,
                "lo": str(analyte.ref_low) if analyte.ref_low is not None else None,
                "hi": str(analyte.ref_high) if analyte.ref_high is not None else None,
                "rt": analyte.ref_text,
                "a": actor,
            },
        )

    for organism in content.organisms:
        organism_id = uuid7()
        await session.execute(
            text(
                "INSERT INTO result_organisms "
                "(id, result_id, organism_name, colony_count, specimen_type, "
                " created_by, updated_by) "
                "VALUES (:i, :r, :n, :c, :s, :a, :a)"
            ),
            {
                "i": str(organism_id),
                "r": str(result_id),
                "n": organism.organism_name,
                "c": organism.colony_count,
                "s": organism.specimen_type,
                "a": actor,
            },
        )
        for sensitivity in organism.sensitivities:
            await session.execute(
                text(
                    "INSERT INTO result_sensitivities "
                    "(id, organism_id, antibiotic_name, interpretation, mic_value, "
                    " created_by, updated_by) "
                    "VALUES (:i, :o, :ab, :v, :mic, :a, :a)"
                ),
                {
                    "i": str(uuid7()),
                    "o": str(organism_id),
                    "ab": sensitivity.antibiotic_name,
                    "v": sensitivity.interpretation,
                    "mic": sensitivity.mic_value,
                    "a": actor,
                },
            )

    for narrative in content.narratives:
        await session.execute(
            text(
                "INSERT INTO result_narratives "
                "(id, result_id, section, text, created_by, updated_by) "
                "VALUES (:i, :r, :s, :t, :a, :a)"
            ),
            {
                "i": str(uuid7()),
                "r": str(result_id),
                "s": narrative.section,
                "t": narrative.text,
                "a": actor,
            },
        )
