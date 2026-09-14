"""Document in, matched result out. Phase 7, wired together.

The whole of Phase 7 in the order a document actually meets it:

    classify (7.1) → extract (7.2, 7.3) → normalise (7.4) → match (7.5)
                                                              ↓
                                                    status handling (7.6)

## Everything here fails towards a human

Each step can decline, and a decline is never an error. A document that cannot
be classified is not extracted with the wrong parser; analytes that cannot be
mapped are stored raw and queued; a result that cannot be matched confidently is
never filed against a patient. The queue is the designed destination for
uncertainty, and every ``needs_review`` below is the system working.

## One rule this module exists to enforce structurally

``match`` is given **candidates**, not a session to go looking with, and it is
imported from ``app.services.matching`` — a module with no extraction imports at
all. **AI never decides which patient a result belongs to**, and the shortest
path to breaking that rule would be a matcher that could reach the model tier.
It cannot, because it cannot reach anything.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import decimal
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.extraction import classify as classify_svc
from app.services.extraction import normalise as normalise_svc
from app.services.extraction.cascade import ExtractionResult, LlmExtractor, extract
from app.services.extraction.parse import parse_report_status
from app.services.matching import (
    OUTCOME_UNMATCHED,
    CandidateCase,
    IncomingResult,
    MatchOutcome,
    candidates_as_json,
    match_result,
)


@dataclasses.dataclass(frozen=True)
class MappedAnalyte:
    test_name_raw: str
    loinc_code: str | None
    mapping_method: str
    mapping_confidence: float
    value_raw: str | None
    value_numeric: decimal.Decimal | None
    unit_raw: str | None
    extraction_method: str
    extraction_confidence: float

    @property
    def needs_mapping_review(self) -> bool:
        return self.loinc_code is None


@dataclasses.dataclass(frozen=True)
class PipelineOutcome:
    classification: classify_svc.Classification
    extraction: ExtractionResult
    analytes: list[MappedAnalyte]
    match: MatchOutcome
    report_status: str | None
    #: True when *anything* in the chain wants a person. The document lands in
    #: the review queue and the reasons are collected below.
    needs_review: bool
    reasons: list[str]


async def load_candidates(
    session: AsyncSession, *, mrn: str | None, patient_name: str | None
) -> list[CandidateCase]:
    """Open cases that could plausibly own this result.

    **Deliberately over-fetches.** Narrowing here would silently remove
    candidates the scorer never gets to reject, and a candidate that is never
    scored cannot appear in ``match_decisions.candidates`` — so a later
    investigation into a wrong match would show nothing was in contention when
    something was.

    Name similarity is computed in SQL, where the trigram index lives, and
    returned as ``None`` when there is no name to compare — which the scorer
    treats as "not computed", not as zero.
    """
    if not mrn and not patient_name:
        return []

    rows = (
        await session.execute(
            text(
                "SELECT pc.id AS case_id, o.id AS order_id, "
                "       o.external_order_id, p.mrn, p.name AS patient_name, "
                "       p.dob, o.test_code, "
                # sample_collected_at is when the specimen was taken, which is
                # what a lab prints; ordered_at is when someone typed the
                # request. Comparing a report's collection date against the
                # order date would fail every time the two differ by a day.
                "       COALESCE(o.sample_collected_at, o.ordered_at) AS collected, "
                "       CASE WHEN CAST(:name AS text) IS NULL THEN NULL "
                "            ELSE similarity(p.name, :name) END AS name_sim "
                "  FROM pending_cases pc "
                "  JOIN orders o ON o.id = pc.order_id "
                # Joined through orders.patient_id rather than hopping via
                # encounters: fewer joins, and it is the same patient by
                # construction.
                "  JOIN patients p ON p.id = o.patient_id "
                " WHERE pc.deleted_at IS NULL AND pc.state <> 'closed' "
                "   AND p.deleted_at IS NULL AND o.deleted_at IS NULL "
                "   AND (CAST(:mrn AS text) IS NULL OR p.mrn = :mrn "
                "        OR (CAST(:name AS text) IS NOT NULL "
                "            AND similarity(p.name, :name) >= 0.5)) "
                " LIMIT 50"
            ),
            {"mrn": mrn, "name": patient_name},
        )
    ).fetchall()

    return [
        CandidateCase(
            case_id=row.case_id,
            order_id=row.order_id,
            external_order_id=row.external_order_id,
            mrn=row.mrn,
            patient_name=row.patient_name,
            date_of_birth=row.dob,
            test_code=row.test_code,
            expected_collected_at=row.collected.date() if row.collected else None,
            name_similarity=float(row.name_sim) if row.name_sim is not None else None,
        )
        for row in rows
    ]


async def run(
    session: AsyncSession,
    *,
    text_layer: str,
    lab_name: str | None = None,
    mrn: str | None = None,
    patient_name: str | None = None,
    external_order_id: str | None = None,
    collected_at: dt.date | None = None,
    test_code: str | None = None,
    llm: LlmExtractor | None = None,
) -> PipelineOutcome:
    """Run the whole of Phase 7 over one document's text.

    ``llm=None`` is NODE B being unreachable and is **not an error**: 7.1 falls
    back to `unknown` and 7.2 drops a tier. Both route to a person rather than
    to a guess, which is the shape RULE 2 takes at document level.
    """
    reasons: list[str] = []

    # ── 7.1 ──
    classification = classify_svc.classify_text(text_layer)
    if classification.needs_review:
        if llm is None:
            classification = classify_svc.unknown_because_node_b_is_down()
        reasons.append(
            "The kind of report could not be determined confidently, so it has "
            "not been parsed automatically."
        )

    # ── 7.2 + 7.3 ──
    extraction = await extract(
        session,
        text_layer=text_layer,
        report_type=classification.report_type,
        lab_name=lab_name,
        llm=llm,
    )
    if extraction.needs_human and extraction.reason:
        reasons.append(extraction.reason)

    # ── 7.4 ──
    mapped: list[MappedAnalyte] = []
    for analyte in extraction.analytes:
        mapping = await normalise_svc.map_test_name(
            session, analyte.test_name_raw, lab_name=lab_name
        )
        mapped.append(
            MappedAnalyte(
                test_name_raw=analyte.test_name_raw,
                loinc_code=mapping.loinc_code,
                mapping_method=mapping.method,
                mapping_confidence=mapping.confidence,
                value_raw=analyte.value.raw if analyte.value else None,
                value_numeric=analyte.value.number if analyte.value else None,
                unit_raw=analyte.unit_raw,
                extraction_method=analyte.method,
                extraction_confidence=analyte.confidence,
            )
        )
    unmapped = [m for m in mapped if m.needs_mapping_review]
    if unmapped:
        # Named, not counted. An admin fixing these needs to know which.
        names = ", ".join(sorted({m.test_name_raw for m in unmapped})[:5])
        reasons.append(
            f"{len(unmapped)} test name(s) are not yet mapped to a standard "
            f"code and need an admin: {names}."
        )

    # ── 7.6 ──
    report_status = parse_report_status(text_layer)

    # ── 7.5 ── no AI, and no session handed to the matcher
    candidates = await load_candidates(session, mrn=mrn, patient_name=patient_name)
    match = match_result(
        IncomingResult(
            external_order_id=external_order_id,
            mrn=mrn,
            patient_name=patient_name,
            test_code=test_code,
            collected_at=collected_at,
        ),
        candidates,
    )
    if match.outcome != "auto_matched":
        reasons.append(match.reason)

    return PipelineOutcome(
        classification=classification,
        extraction=extraction,
        analytes=mapped,
        match=match,
        report_status=report_status,
        needs_review=bool(reasons),
        reasons=reasons,
    )


async def record_match_decision(
    session: AsyncSession,
    outcome: MatchOutcome,
    *,
    document_id: uuid.UUID | None = None,
    result_id: uuid.UUID | None = None,
) -> None:
    """Write the decision, **including the ones that matched cleanly**.

    Recording only the ambiguous cases would leave the auto-matches — the ones
    that can actually put a result on the wrong file — with no audit trail at
    all. The near-misses stored alongside are what make a wrong match
    investigable six months later.
    """
    await session.execute(
        text(
            "INSERT INTO match_decisions "
            "  (id, document_id, result_id, chosen_case_id, outcome, method, "
            "   score, candidates, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :doc, :res, :case, :out, :method, "
            "        :score, CAST(:cands AS jsonb), now(), now())"
        ),
        {
            "doc": str(document_id) if document_id else None,
            "res": str(result_id) if result_id else None,
            "case": str(outcome.chosen_case_id) if outcome.chosen_case_id else None,
            "out": (
                outcome.outcome
                if outcome.outcome != OUTCOME_UNMATCHED
                else OUTCOME_UNMATCHED
            ),
            "method": outcome.method,
            "score": str(outcome.score),
            "cands": json.dumps(candidates_as_json(outcome.candidates)),
        },
    )
