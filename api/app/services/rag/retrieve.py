"""Finding the guidance that applies. Phase 8.3 — NODE A, CPU, no network.

Hybrid retrieval: keyword and vector, fused with Reciprocal Rank Fusion.

## Why the query is built from structured JSON, not the PDF

8.3: *"Query construction **from the structured JSON, never the PDF**."* The
report's raw text is full of the patient's name, the hospital's letterhead and
the lab's phone number. Searching a guideline store with that finds documents
that happen to share a word with an address. Searching it with *"Escherichia
coli ceftriaxone resistant urine"* finds the antibiotic policy.

It also keeps patient identifiers out of the query path entirely, which matters
more once a query is ever logged.

## Why RRF rather than adding the scores

A cosine similarity and a `ts_rank_cd` are not on the same scale and never will
be — one is bounded, the other unbounded and corpus-dependent. Adding them lets
whichever happens to be larger dominate. RRF uses only the **rank**, so a chunk
that both halves rate highly wins regardless of what the two scoring functions
happen to output that day.

## Degradation, which is the point

If no embedder is installed, `embed` is `None`, the vector half returns nothing,
and RRF runs over the keyword half alone. Ranking gets worse. **Guidance does
not disappear.** THE ONE RULE, at the level of a single query.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

#: 8.3's constant.
RRF_K = 60
TOP_PER_SIDE = 20
KEEP = 5

#: 8.3's relevance floor: *"best score below threshold → return 'no verified
#: guidance found', **do not call NODE B at all**."* Calling the model with
#: irrelevant context is how a confident answer gets built on nothing.
#:
#: ⚠️ Applied to the **underlying** scores, never to the fused one. RRF is built
#: from *rank* alone, so a single perfect keyword hit and a single useless one
#: both score 1/(60+1) — the fused number says nothing about relevance and a
#: floor on it would reject everything or nothing depending only on how many
#: results came back. The first draft of this module made exactly that mistake.
KEYWORD_FLOOR = 0.01
VECTOR_FLOOR = 0.30

#: The bar the **OR fallback** must clear. Higher than `KEYWORD_FLOOR` on
#: purpose: the strict pass has already required every term to be present, so a
#: weak score there still means a real match. In the OR pass a single shared
#: word returns a row, and most shared words are furniture.
#:
#: **Measured, not guessed** — against the seeded antibiotic policy, 2026-09-15:
#:
#:   | query                                        | top ts_rank_cd |
#:   |----------------------------------------------|----------------|
#:   | "why does ceftriaxone resistance matter"     | 1.60           |
#:   | "how quickly must the clinician be contacted"| 1.20           |
#:   | "ceftriaxone"            (weakest real hit)  | 0.80           |
#:   | "...organism anywhere in any guideline"      | 0.40  ← noise  |
#:   | "organism"                                   | 0.40  ← noise  |
#:
#: 0.6 sits between the weakest genuine question and the strongest noise.
#:
#: ⚠️ `ts_rank_cd` is **not normalised**, so this number is corpus-dependent.
#: Re-measure it when the knowledge base grows beyond a handful of documents —
#: `test_the_forgiving_pass_does_not_match_everything` is what will notice.
FALLBACK_FLOOR = 0.6

#: Supplied by the caller when an embedder exists. Taken as an argument so this
#: module has no import path to a model of any kind.
Embedder = Callable[[str], Awaitable[list[float]]]


@dataclasses.dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    chunk_text: str
    document_title: str
    publisher: str
    section_path: str | None
    page_no: int | None
    score: float
    #: Which half found it. Shown in the UI so a reader can tell a keyword hit
    #: from a semantic one.
    via: str


def build_query(
    *,
    organism: str | None = None,
    resistant_to: str | None = None,
    specimen: str | None = None,
    analyte: str | None = None,
    direction: str | None = None,
) -> str:
    """Compose a search string from structured fields only.

    Every argument is a value the rule engine already parsed. Nothing here
    accepts free text from a report, so a patient's name cannot reach the query
    even by accident.
    """
    parts = [p for p in (organism, resistant_to, specimen, analyte, direction) if p]
    return " ".join(parts).strip()


#: Words that carry no retrieval signal but are how people actually phrase a
#: question. Postgres' english dictionary already drops most of them; these are
#: the ones it keeps and a clinical question always contains.
_QUESTION_WORDS = frozenset(
    {
        "why",
        "what",
        "when",
        "how",
        "which",
        "who",
        "does",
        "do",
        "did",
        "is",
        "are",
        "was",
        "were",
        "should",
        "matter",
        "matters",
        "mean",
        "means",
        "tell",
        "me",
        "about",
        "explain",
        "patient",
        "result",
        "report",
    }
)

#: Strip anything `websearch_to_tsquery` reads as an operator. A clinician's
#: apostrophe or dash must not silently turn into a phrase search or a NOT.
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")


def _or_query(query: str) -> str:
    """Rebuild a question as an OR search, dropping the interrogative scaffolding.

    ``websearch_to_tsquery`` understands the literal word ``or``, so this needs
    no hand-built tsquery syntax — and hand-building one from user text is how a
    search box becomes an injection point.
    """
    words = [w for w in _WORD.findall(query.lower()) if w not in _QUESTION_WORDS]
    # Single characters are noise, and a lone "a" ORed into the query matches
    # most of the corpus.
    words = [w for w in words if len(w) > 1]
    return " or ".join(dict.fromkeys(words))


async def _keyword_pass(
    session: AsyncSession, query: str, limit: int
) -> list[tuple[str, float]]:
    rows = (
        await session.execute(
            text(
                "SELECT c.id::text AS chunk_id, "
                "       ts_rank_cd( "
                # Title gets weight A, body weight B -- a chunk from a document
                # *about* the thing outranks one that mentions it in passing.
                "         setweight(to_tsvector('english', d.title), 'A') || "
                "         setweight(coalesce(c.tsv, ''::tsvector), 'B'), "
                "         websearch_to_tsquery('english', :q) "
                "       ) AS score "
                "  FROM kb_chunks c "
                "  JOIN kb_documents d ON d.id = c.kb_document_id "
                # ★ Only approved documents are retrievable.
                " WHERE d.deleted_at IS NULL AND d.approved_at IS NOT NULL "
                "   AND ( "
                "     setweight(to_tsvector('english', d.title), 'A') || "
                "     setweight(coalesce(c.tsv, ''::tsvector), 'B') "
                "   ) @@ websearch_to_tsquery('english', :q) "
                " ORDER BY score DESC "
                " LIMIT :lim"
            ),
            {"q": query, "lim": limit},
        )
    ).fetchall()
    return [(r.chunk_id, float(r.score)) for r in rows]


async def keyword_search(
    session: AsyncSession, query: str, *, limit: int = TOP_PER_SIDE
) -> list[tuple[str, float]]:
    """`ts_rank_cd` over approved chunks. Title weighted above body.

    ``websearch_to_tsquery`` rather than ``plainto_tsquery``: it tolerates the
    quotes and minus signs a clinician might type, instead of failing the whole
    query on one stray character.

    ## Strict first, then forgiving

    ``websearch_to_tsquery`` joins bare terms with **AND**, which is right for
    the structured query the panel builds — *"Escherichia coli Ceftriaxone
    resistant urine"* should match a chunk about all four. It is wrong for a
    question typed by a person: *"why does ceftriaxone resistance matter"*
    requires the policy to contain the words "why", "does" and "matter", and no
    policy ever does. The whole question then retrieves **nothing**, and the
    clinician is told there is no guidance when the guidance is right there.

    So: run the strict AND query, and only if it finds nothing, retry as an OR
    over the content words. Precision when precision is available, recall when
    it is not — and never a confident "no guidance" caused by the word "why".

    The relevance floor in :func:`retrieve` still applies to the second pass, so
    widening the query does not lower the bar for what counts as relevant.
    """
    if not query.strip():
        return []

    strict = await _keyword_pass(session, query, limit)
    if strict:
        return strict

    loose = _or_query(query)
    if not loose or loose == query.strip().lower():
        return []
    # ★ The fallback is held to a higher bar than the strict pass, because an
    # OR match is weaker evidence: one shared word is enough to return a row.
    # Without this, "zzzqqq no such organism anywhere in any guideline"
    # retrieved the antibiotic policy -- on the words "organism" and
    # "guideline" alone -- and NODE B was called on context that had nothing to
    # do with the question. That is precisely what 8.3's relevance floor exists
    # to prevent, and widening the query had quietly reopened it.
    return [
        (cid, score)
        for cid, score in await _keyword_pass(session, loose, limit)
        if score >= FALLBACK_FLOOR
    ]


async def vector_search(
    session: AsyncSession, embedding: list[float], *, limit: int = TOP_PER_SIDE
) -> list[tuple[str, float]]:
    """Cosine distance over the HNSW index, approved documents only."""
    rows = (
        await session.execute(
            text(
                "SELECT c.id::text AS chunk_id, "
                # `<=>` is cosine distance, so smaller is better; converted to a
                # similarity here so both halves of the fusion agree on
                # direction.
                "       1 - (c.embedding <=> CAST(:emb AS vector)) AS score "
                "  FROM kb_chunks c "
                "  JOIN kb_documents d ON d.id = c.kb_document_id "
                " WHERE d.deleted_at IS NULL AND d.approved_at IS NOT NULL "
                "   AND c.embedding IS NOT NULL "
                " ORDER BY c.embedding <=> CAST(:emb AS vector) "
                " LIMIT :lim"
            ),
            {"emb": str(embedding), "lim": limit},
        )
    ).fetchall()
    return [(r.chunk_id, float(r.score)) for r in rows]


def reciprocal_rank_fusion(
    *ranked_lists: list[tuple[str, float]], k: int = RRF_K
) -> list[tuple[str, float]]:
    """Fuse by rank, never by score. ``1 / (k + rank)``, summed.

    ``k = 60`` is the published default and it does one useful thing: it flattens
    the difference between rank 1 and rank 2 relative to the difference between
    rank 1 and rank 20, so a chunk found by *both* halves outranks one that only
    one half loved.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (chunk_id, _) in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


async def retrieve(
    session: AsyncSession,
    query: str,
    *,
    embed: Embedder | None = None,
    keep: int = KEEP,
) -> list[RetrievedChunk]:
    """Hybrid retrieval, degrading to keyword-only when no embedder exists.

    Returns ``[]`` when nothing clears the relevance floor — and the caller must
    treat that as *"no verified guidance found"* and **not call NODE B**.
    """
    # Each half filters on its own scale before fusion. `@@` has already
    # required a real lexeme match for the keyword side, so the floor there only
    # discards matches so weak they are noise.
    keyword = [
        (cid, score)
        for cid, score in await keyword_search(session, query)
        if score >= KEYWORD_FLOOR
    ]

    vector: list[tuple[str, float]] = []
    if embed is not None:
        try:
            vector = [
                (cid, score)
                for cid, score in await vector_search(session, await embed(query))
                if score >= VECTOR_FLOOR
            ]
        except Exception:
            vector = []

    fused = reciprocal_rank_fusion(keyword, vector)
    if not fused:
        return []

    top = fused[:keep]
    if not top:
        return []

    by_id = dict(top)
    found_by = {cid for cid, _ in keyword}

    rows = (
        await session.execute(
            # `expanding=True` rather than a PG array literal: asyncpg wants a
            # real sequence for an array parameter, and building "{a,b,c}" by
            # hand hands it a string it cannot adapt.
            text(
                "SELECT c.id::text AS chunk_id, c.chunk_text, c.section_path, "
                "       c.page_no, d.title, d.publisher "
                "  FROM kb_chunks c "
                "  JOIN kb_documents d ON d.id = c.kb_document_id "
                " WHERE c.id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": [uuid.UUID(cid) for cid in by_id]},
        )
    ).fetchall()

    chunks = [
        RetrievedChunk(
            chunk_id=r.chunk_id,
            chunk_text=r.chunk_text,
            document_title=r.title,
            publisher=r.publisher,
            section_path=r.section_path,
            page_no=r.page_no,
            score=by_id[r.chunk_id],
            via=(
                "both"
                if (r.chunk_id in found_by and vector)
                else ("keyword" if r.chunk_id in found_by else "vector")
            ),
        )
        for r in rows
    ]
    chunks.sort(key=lambda c: c.score, reverse=True)
    return chunks
