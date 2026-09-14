"""``/api/cases/{id}/explain`` and the knowledge base behind it. Phase 8.6.

The whole of Phase 8 at one URL, in the only order that is safe:

    retrieve (8.3) → generate (8.4) → **verify (8.5)** → return what survived

## Four ways this returns "no explanation", and none of them is an error

1. **Nothing retrieved** — no approved guidance matches. NODE B is *not called*.
2. **NODE B unreachable or slow** — the kill switch, a dead node, a timeout.
3. **Malformed output** — the model did not return usable JSON.
4. **Every citation failed verification** — the model wrote something the
   sources do not contain.

All four return `200` with `explanation: null` and a sentence saying which
happened. **None of them touches the flag.** The explanation is a convenience;
the flag underneath is the product, and a clinician who came to read a critical
result must never be shown an error instead of it.

## Why the endpoint is a POST

It is not idempotent in the sense that matters: it calls NODE B, costs ~20 s of
GPU, and writes rejection rows. A GET that did that would be cached, prefetched
and retried by things that assume GETs are free.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_session
from app.security import client_ip, require_role
from app.services import audit as audit_service
from app.services import settings_store
from app.services.auth import AuthenticatedUser
from app.services.rag import generate as generate_svc
from app.services.rag import ingest as ingest_svc
from app.services.rag import retrieve as retrieve_svc
from app.services.rag import verify as verify_svc

router = APIRouter(tags=["explain"])

EXPLAIN_ROLES = ("doctor", "unit_head", "lab_tech", "admin", "auditor")
KB_ADMIN_ROLES = ("admin",)

#: Shown when retrieval found nothing. 8.3's own wording.
NO_GUIDANCE = (
    "No approved guidance in the knowledge base covers this result, so there is "
    "nothing to explain from. The flag itself is unaffected."
)
NODE_B_DOWN = (
    "The AI assist is offline, so no explanation was generated. Tracking, "
    "timers and escalation are unaffected."
)
ALL_REJECTED = (
    "An explanation was generated but none of its quotations could be found in "
    "the sources, so it was discarded rather than shown. This is recorded."
)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: uuid.UUID
    quoted_text: str
    document_title: str
    section_path: str | None
    page_no: int | None
    #: Offsets into the chunk, so the UI highlights rather than making the
    #: reader hunt for the sentence.
    char_start: int
    char_end: int
    #: 1.0 for an exact match. Shown, because a reader is entitled to know the
    #: quote was re-typed rather than copied.
    match_ratio: float
    chunk_text: str


class RetrievedPassage(BaseModel):
    """Approved guidance that was found, with **no generated text attached.**

    Deliberately a different type from :class:`Evidence`, and deliberately
    without ``quoted_text`` or ``match_ratio``. Those fields mean "a model said
    this and the verifier confirmed it"; these passages have been through no
    model at all. Reusing ``Evidence`` here would put unverified and verified
    material in one list under one name, which is the exact confusion the span
    verifier exists to prevent.
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: uuid.UUID
    document_title: str
    section_path: str | None
    page_no: int | None
    chunk_text: str


class ExplainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: uuid.UUID
    #: ``None`` whenever nothing survived. Never a partial explanation.
    explanation: str | None
    evidence: list[Evidence]
    #: Always populated, including on success — it says how the answer was
    #: reached, not only why it failed.
    note: str
    #: ★ 8.5's fallback: *"show the raw retrieved chunks with no generated
    #: text."* Populated whenever guidance was found but no explanation is being
    #: shown — NODE B down, the kill switch, malformed output, or every citation
    #: rejected. The hospital's own approved text is useful on its own, and a
    #: clinician who asked "why does this matter?" should not be sent away with
    #: an apology when the answer is sitting in the knowledge base. Empty on
    #: success, because `evidence` already carries the passages in context.
    retrieved: list[RetrievedPassage] = Field(default_factory=list)
    sources_considered: int
    rejected_count: int


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Built from structured fields by the caller. 8.3 forbids the raw report
    #: text reaching the query, so this is a short phrase, not a document.
    query: str = Field(min_length=3, max_length=300)


def _passages(chunks: list[retrieve_svc.RetrievedChunk]) -> list[RetrievedPassage]:
    """The retrieved guidance, as itself. No model has touched any of this."""
    return [
        RetrievedPassage(
            chunk_id=uuid.UUID(c.chunk_id),
            document_title=c.document_title,
            section_path=c.section_path,
            page_no=c.page_no,
            chunk_text=c.chunk_text,
        )
        for c in chunks
    ]


@router.post(
    "/cases/{case_id}/explain",
    response_model=ExplainResponse,
    summary="Explain why this result matters, with verifiable citations",
)
async def explain_case(
    case_id: uuid.UUID,
    payload: ExplainRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_role(*EXPLAIN_ROLES)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ExplainResponse:
    """Retrieve, generate, verify. Return only what survived verification."""
    # ── 8.3 ── no embedder installed on this deployment, so keyword-only.
    chunks = await retrieve_svc.retrieve(session, payload.query, embed=None)

    if not chunks:
        # 8.3: below the floor -> do not call NODE B at all.
        return ExplainResponse(
            case_id=case_id,
            explanation=None,
            evidence=[],
            note=NO_GUIDANCE,
            sources_considered=0,
            rejected_count=0,
        )

    # ── ★ the kill switch, checked before NODE B is dialled ──────────
    #
    # Phase 5 gives an admin a way to turn inference off in one click "at 3am",
    # and until 2026-09-15 this endpoint did not consult it: the switch dimmed
    # the health badge while the Explain button went on calling NODE B. A lever
    # that does not disconnect the thing it names is worse than no lever, and
    # nothing would have shown it — the answers kept arriving.
    #
    # Resolved from the table so the switch takes effect on the next request
    # rather than the next restart, and reported as NODE_B_DOWN because from
    # the clinician's side it is the same fact: no assist, flag unaffected.
    if not await settings_store.llm_enabled(session, env_default=settings.llm_enabled):
        return ExplainResponse(
            case_id=case_id,
            explanation=None,
            evidence=[],
            note=NODE_B_DOWN,
            retrieved=_passages(chunks),
            sources_considered=len(chunks),
            rejected_count=0,
        )

    # ── 8.4 ──
    generated = await generate_svc.generate(
        base_url=settings.llm_base_url,
        model=settings.llm_generation_model,
        question=(
            f"Why does this result need a doctor's attention? {payload.query}. "
            "Answer in two sentences."
        ),
        sources=[
            generate_svc.SourceChunk(
                chunk_id=c.chunk_id, text=c.chunk_text, title=c.document_title
            )
            for c in chunks
        ],
    )

    if generated is None:
        return ExplainResponse(
            case_id=case_id,
            explanation=None,
            evidence=[],
            note=NODE_B_DOWN,
            retrieved=_passages(chunks),
            sources_considered=len(chunks),
            rejected_count=0,
        )

    # ── 8.5 ★ nothing reaches the caller without passing this ──
    result = await verify_svc.verify_evidence(
        session,
        [
            verify_svc.EvidenceItem(chunk_id=uuid.UUID(cid), quoted_text=quote)
            for cid, quote in generated.evidence
        ],
        case_id=case_id,
    )
    await session.commit()

    if not result.accepted:
        # The explanation is discarded whole. Showing it with the failed
        # citations removed would leave prose that looks sourced and is not.
        return ExplainResponse(
            case_id=case_id,
            explanation=None,
            evidence=[],
            note=ALL_REJECTED,
            # The prose is discarded; the hospital's own approved text is not.
            retrieved=_passages(chunks),
            sources_considered=len(chunks),
            rejected_count=len(result.rejections),
        )

    by_id = {c.chunk_id: c for c in chunks}
    await audit_service.append(
        session,
        action=audit_service.ACTION_EXPLANATION_SHOWN,
        # `pending_case`, not `case` -- the CHECK on audit_log.entity_type
        # names the table, and the shorter word is refused.
        entity_type="pending_case",
        entity_id=case_id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        after={
            "explained": True,
            "evidence": len(result.verified),
            "rejected": len(result.rejections),
            "elapsed_s": generated.elapsed_s,
        },
    )
    await session.commit()

    return ExplainResponse(
        case_id=case_id,
        explanation=generated.explanation,
        evidence=[
            Evidence(
                chunk_id=v.chunk_id,
                quoted_text=v.quoted_text,
                document_title=v.document_title,
                section_path=v.section_path,
                page_no=v.page_no,
                char_start=v.char_start,
                char_end=v.char_end,
                match_ratio=round(v.ratio, 3),
                chunk_text=verify_svc.normalise(
                    by_id[str(v.chunk_id)].chunk_text
                    if str(v.chunk_id) in by_id
                    else v.quoted_text
                ),
            )
            for v in result.verified
        ],
        note=(
            f"Every quotation below was checked against its source. "
            f"{len(result.rejections)} were rejected."
            if result.rejections
            else "Every quotation below was checked against its source."
        ),
        sources_considered=len(chunks),
        rejected_count=len(result.rejections),
    )


# ── the knowledge base itself ─────────────────────────────────────────


#: Mirrors `ck_kb_documents_publisher` and `ck_kb_documents_doc_type`.
#:
#: These are deliberately **not** config tables. The project convention is
#: "configuration lives in tables", and it applies to things an admin tunes —
#: thresholds, delays, keywords. These two are a closed vocabulary that the
#: database enforces with a `CHECK`, and duplicating them here is what turns a
#: typo into a 422 that names the five valid values instead of a 500 with a
#: constraint name in the log. If the `CHECK` ever gains a value, this tuple is
#: the other half of that migration.
Publisher = Literal["who", "icmr", "hospital", "nlem", "other"]
DocType = Literal[
    "guideline", "sop", "antibiogram", "antibiotic_policy", "formulary", "protocol"
]


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=300)
    #: Validated against the same vocabulary the database enforces, so an
    #: unknown value is rejected at the edge with a message that says what is
    #: allowed. Before this, `publisher: str` let any string through to the
    #: `CHECK`, which raised an unhandled `IntegrityError` — a 500 for what is
    #: plainly a bad request, found by the Phase 8 E2E spec on 2026-09-15.
    publisher: Publisher
    doc_type: DocType
    document_text: str = Field(min_length=50)
    version: str = Field(default="1", max_length=64)
    source_ref: str | None = Field(default=None, max_length=500)


@router.post("/kb/documents", summary="Add a guideline — unapproved until approved")
async def ingest_kb_document(
    payload: IngestRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_role(*KB_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Store and chunk. **Stored unapproved**, so retrieval cannot see it yet.

    Any patient-identifier warnings are returned rather than blocking: a
    guideline may legitimately contain the word "Patient:" in a worked example,
    so the judgement belongs to the person approving it — with the offending
    lines in front of them.
    """
    doc_id, chunk_count, warnings = await ingest_svc.ingest_document(
        session,
        title=payload.title,
        publisher=payload.publisher,
        doc_type=payload.doc_type,
        document_text=payload.document_text,
        version=payload.version,
        source_ref=payload.source_ref,
        approved_by=None,
    )
    await session.commit()

    return {
        "document_id": str(doc_id),
        "chunks": chunk_count,
        "approved": False,
        "identifier_warnings": [
            {"kind": w.kind, "excerpt": w.excerpt} for w in warnings
        ],
        "message": (
            f"Stored as {chunk_count} chunks. **It is not retrievable until "
            "approved.**"
            + (
                f" ⚠️ {len(warnings)} possible patient identifier(s) found — "
                "review before approving."
                if warnings
                else ""
            )
        ),
    }


@router.post("/kb/documents/{document_id}/approve", summary="Make it retrievable")
async def approve_kb_document(
    document_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_role(*KB_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """★ The moment a document becomes something a doctor can be shown.

    Recorded with who and when, because *"an unapproved guideline must not reach
    a doctor"* is only enforceable if approval is an act with a name on it.
    """
    row = (
        await session.execute(
            text(
                "UPDATE kb_documents "
                "   SET approved_by = :who, approved_at = now(), updated_at = now() "
                " WHERE id = :id AND deleted_at IS NULL AND approved_at IS NULL "
                " RETURNING title"
            ),
            {"who": str(user.id), "id": str(document_id)},
        )
    ).first()

    if row is None:
        await session.rollback()
        return {"approved": False, "message": "Already approved, or no such document."}

    await audit_service.append(
        session,
        action=audit_service.ACTION_CONFIG_CHANGED,
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
        actor_ip=client_ip(request),
        before={"document": str(document_id), "approved": False},
        after={"document": str(document_id), "approved": True, "title": row.title},
    )
    await session.commit()

    return {
        "approved": True,
        "document_id": str(document_id),
        "message": f"'{row.title}' is now retrievable, approved by you.",
    }


@router.get("/kb/documents", summary="What the knowledge base holds")
async def list_kb_documents(
    user: AuthenticatedUser = Depends(require_role(*EXPLAIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = (
        await session.execute(
            text(
                "SELECT d.id, d.title, d.publisher, d.doc_type, d.version, "
                "       d.approved_at IS NOT NULL AS approved, "
                "       count(c.id) AS chunks "
                "  FROM kb_documents d "
                "  LEFT JOIN kb_chunks c ON c.kb_document_id = d.id "
                " WHERE d.deleted_at IS NULL "
                " GROUP BY d.id "
                " ORDER BY d.created_at DESC"
            )
        )
    ).fetchall()
    return {
        "documents": [
            {
                "id": str(r.id),
                "title": r.title,
                "publisher": r.publisher,
                "doc_type": r.doc_type,
                "version": r.version,
                "approved": r.approved,
                "chunks": r.chunks,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/kb/rejections", summary="The hallucination rate, made countable")
async def list_rejections(
    limit: int = 50,
    user: AuthenticatedUser = Depends(require_role("admin", "auditor")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """8.5's metric. Printed even when zero, so a reader can see it was checked."""
    rows = (
        await session.execute(
            text(
                "SELECT reason, count(*) AS n FROM ai_rejections "
                " GROUP BY reason ORDER BY n DESC"
            )
        )
    ).fetchall()
    recent = (
        await session.execute(
            text(
                "SELECT reason, quoted_text, best_ratio, created_at "
                "  FROM ai_rejections ORDER BY created_at DESC LIMIT :lim"
            ),
            {"lim": max(1, min(limit, 200))},
        )
    ).fetchall()
    return {
        "by_reason": {r.reason: r.n for r in rows},
        "total": sum(r.n for r in rows),
        "recent": [
            {
                "reason": r.reason,
                "quoted_text": r.quoted_text,
                "best_ratio": float(r.best_ratio) if r.best_ratio is not None else None,
                "created_at": r.created_at.isoformat(),
            }
            for r in recent
        ],
    }
