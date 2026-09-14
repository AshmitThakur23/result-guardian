"""The queues Phase 7 sends uncertainty to. 7.4 and 7.5.

Two screens' worth of API, and they exist because the rest of Phase 7 is built
to **decline** rather than guess. A system that routes everything it is unsure
about to a queue has only moved the problem unless somebody can work that queue.

* **`/api/extraction/unmapped`** — test names the cascade could not resolve.
  7.4: *"Unmapped terms land in an admin queue; **mapping them once fixes them
  forever**."* The POST is what makes "forever" true.

* **`/api/extraction/match-review`** — results the matcher refused to file.
  7.5: *"Multiple candidate cases → **always human review, never guess**."*
  Confirming one here is the **only** way a result reaches a patient's file
  without the arithmetic having been certain, and it is recorded as `human`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.security import client_ip, require_role
from app.services import audit as audit_service
from app.services.auth import AuthenticatedUser
from app.services.extraction.normalise import remember_mapping

router = APIRouter(prefix="/extraction", tags=["extraction"])

#: Who may work these queues. A lab tech maps test names daily; an auditor may
#: read but `require_role` refuses them every non-GET regardless.
QUEUE_ROLES = ("lab_tech", "unit_head", "admin", "auditor")
#: Confirming a match files a result against a patient. Narrower on purpose.
MATCH_ROLES = ("lab_tech", "doctor", "unit_head", "admin")


class UnmappedTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str
    normalised_text: str
    lab_name: str | None
    #: How often it has arrived. Ordering by this puts the term that is costing
    #: the most review time at the top, rather than whichever arrived first.
    occurrences: int


class MapTermRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(min_length=1, max_length=300)
    loinc_code: str = Field(min_length=1, max_length=20)
    lab_name: str | None = Field(default=None, max_length=200)


@router.get(
    "/unmapped",
    summary="Test names the normalisation cascade could not resolve",
)
async def list_unmapped(
    limit: int = 50,
    user: AuthenticatedUser = Depends(require_role(*QUEUE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Raw analyte names with no LOINC code, commonest first.

    Read from `result_analytes` rather than from a queue table: the queue *is*
    the set of rows that never got a code, so there is nothing to keep in step
    and nothing to go stale if a mapping is added by another route.
    """
    rows = (
        await session.execute(
            text(
                "SELECT ra.test_name_raw AS raw_text, count(*) AS occurrences "
                "  FROM result_analytes ra "
                " WHERE ra.deleted_at IS NULL AND ra.loinc_code IS NULL "
                "   AND ra.test_name_raw IS NOT NULL "
                " GROUP BY ra.test_name_raw "
                " ORDER BY count(*) DESC, ra.test_name_raw "
                " LIMIT :lim"
            ),
            {"lim": max(1, min(limit, 200))},
        )
    ).fetchall()

    from app.services.extraction.normalise import normalise_test_name

    return {
        "terms": [
            UnmappedTerm(
                raw_text=r.raw_text,
                normalised_text=normalise_test_name(r.raw_text),
                lab_name=None,
                occurrences=r.occurrences,
            ).model_dump()
            for r in rows
        ],
        "count": len(rows),
    }


@router.post("/unmapped", summary="Map a test name — permanently")
async def map_term(
    payload: MapTermRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_role("lab_tech", "unit_head", "admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Write the mapping back, and **apply it to rows already stored**.

    Applying retroactively is the whole point of *"fixes them forever"*. A
    mapping that only affected future reports would leave every result already
    in the system unmapped, and somebody would have to work the same queue entry
    again tomorrow for yesterday's rows.
    """
    await remember_mapping(
        session,
        raw=payload.raw_text,
        loinc_code=payload.loinc_code,
        lab_name=payload.lab_name,
        source=f"admin:{user.employee_code}",
    )

    # RETURNING and count, rather than `.rowcount`: that attribute is on the
    # driver's cursor result and is not part of the typed Result interface, so
    # relying on it is both untyped and driver-specific.
    updated = len(
        (
            await session.execute(
                text(
                    "UPDATE result_analytes SET loinc_code = :code, "
                    "       updated_at = now() "
                    " WHERE deleted_at IS NULL AND loinc_code IS NULL "
                    "   AND test_name_raw = :raw "
                    " RETURNING id"
                ),
                {"code": payload.loinc_code, "raw": payload.raw_text},
            )
        ).fetchall()
    )

    await audit_service.append(
        session,
        action=audit_service.ACTION_CONFIG_CHANGED,
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        before={"test_name": payload.raw_text, "loinc_code": None},
        after={
            "test_name": payload.raw_text,
            "loinc_code": payload.loinc_code,
            "lab_name": payload.lab_name,
            "rows_updated": updated,
        },
    )
    await session.commit()

    return {
        "raw_text": payload.raw_text,
        "loinc_code": payload.loinc_code,
        "rows_updated": updated,
        "message": (
            f"Mapped. {updated} stored result(s) updated, and this name will "
            "resolve automatically from now on."
        ),
    }


@router.get(
    "/match-review",
    summary="Results the matcher refused to file without a person",
)
async def list_match_review(
    limit: int = 50,
    user: AuthenticatedUser = Depends(require_role(*QUEUE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Decisions that came out as `needs_review` or `unmatched`.

    **Oldest first**, for the same reason Phase 6's queue is: a backlog served
    newest-first grows a tail nobody reaches, and the tail is exactly the
    results that have been waiting longest.
    """
    rows = (
        await session.execute(
            text(
                "SELECT md.id, md.document_id, md.result_id, md.outcome, "
                "       md.method, md.score, md.candidates, md.created_at, "
                "       md.reviewed_at "
                "  FROM match_decisions md "
                " WHERE md.outcome IN ('needs_review', 'unmatched') "
                "   AND md.reviewed_at IS NULL "
                " ORDER BY md.created_at ASC "
                " LIMIT :lim"
            ),
            {"lim": max(1, min(limit, 200))},
        )
    ).fetchall()

    return {
        "decisions": [
            {
                "id": str(r.id),
                "document_id": str(r.document_id) if r.document_id else None,
                "result_id": str(r.result_id) if r.result_id else None,
                "outcome": r.outcome,
                "method": r.method,
                "score": str(r.score),
                # Every candidate that was scored, so a reviewer sees what the
                # arithmetic saw rather than being asked to trust its verdict.
                "candidates": r.candidates,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "count": len(rows),
    }


class ConfirmMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: uuid.UUID


@router.post(
    "/match-review/{decision_id}/confirm",
    summary="A person says which case this result belongs to",
)
async def confirm_match(
    decision_id: uuid.UUID,
    payload: ConfirmMatchRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_role(*MATCH_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Record a human's decision, **as a human's decision**.

    ``method`` becomes ``human`` and ``reviewed_by`` is set. That distinction
    matters more than it looks: Exit Gate 7 counts *incorrect auto-matches*, and
    a confirmation recorded as though the arithmetic had made it would quietly
    corrupt the one metric that must be zero.
    """
    updated = (
        await session.execute(
            text(
                "UPDATE match_decisions "
                "   SET chosen_case_id = :case, outcome = 'auto_matched', "
                "       method = 'human', reviewed_by = :who, "
                "       reviewed_at = now(), updated_at = now() "
                " WHERE id = :id AND reviewed_at IS NULL "
                " RETURNING id"
            ),
            {"case": str(payload.case_id), "who": str(user.id), "id": str(decision_id)},
        )
    ).first()

    if updated is None:
        await session.rollback()
        return {
            "decision_id": str(decision_id),
            "confirmed": False,
            "message": "That decision has already been reviewed by someone else.",
        }

    await audit_service.append(
        session,
        action=audit_service.ACTION_CONFIG_CHANGED,
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        before={"decision": str(decision_id), "chosen_case_id": None},
        after={
            "decision": str(decision_id),
            "chosen_case_id": str(payload.case_id),
            "method": "human",
        },
    )
    await session.commit()

    return {
        "decision_id": str(decision_id),
        "confirmed": True,
        "case_id": str(payload.case_id),
        "message": "Filed against the case you chose, and recorded as your decision.",
    }
