"""Phase 8 — retrieval, generation and the span verifier. Real database.

The starred tests are the ones that matter, and they all assert a **refusal**:

* a quote the source does not contain is rejected, **even when it is plausible**
* every citation failing rejects the **whole** response, not just that citation
* an unapproved document is invisible, at retrieval *and* at verification
* nothing clearing the relevance floor means **NODE B is not called at all**

The measured example this is built around: asked *"what is a critical potassium
level?"* the live `qwen3:4b` answered **"6.0 mEq/L or higher"** — fluent,
confident, and from no source this system holds.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag import retrieve as retrieve_svc
from app.services.rag import verify as verify_svc
from app.services.rag.generate import parse_response, visible_text

POLICY = (
    "Escherichia coli isolated from urine at greater than 100,000 CFU/mL with "
    "resistance to ceftriaxone requires review of the discharge prescription. "
    "Alternative agents should be selected on the reported sensitivity panel."
)


async def _seed_document(
    session: AsyncSession, *, approved: bool, chunk_text: str = POLICY
) -> tuple[uuid.UUID, uuid.UUID]:
    """One document and one chunk. ``approved`` decides visibility."""
    tag = uuid.uuid4().hex[:12]
    doc_id = (
        await session.execute(
            text(
                "INSERT INTO kb_documents "
                "  (id, title, publisher, doc_type, version, sha256, "
                "   approved_by, approved_at, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :title, 'hospital', "
                "        'antibiotic_policy', '1', :sha, "
                "        CASE WHEN :ok THEN (SELECT id FROM users LIMIT 1) END, "
                "        CASE WHEN :ok THEN now() END, now(), now()) "
                "RETURNING id"
            ),
            {
                "title": f"Antibiotic Policy {tag}",
                "sha": tag.ljust(64, "0"),
                "ok": approved,
            },
        )
    ).scalar_one()

    chunk_id = (
        await session.execute(
            text(
                "INSERT INTO kb_chunks "
                "  (id, kb_document_id, section_path, page_no, char_start, "
                "   char_end, chunk_text, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :doc, '4.2 > Urinary tract', 12, "
                "        0, :end, :txt, now(), now()) "
                "RETURNING id"
            ),
            {"doc": doc_id, "end": len(chunk_text), "txt": chunk_text},
        )
    ).scalar_one()
    await session.flush()
    return doc_id, chunk_id


# ── 8.5 ★ the verifier ────────────────────────────────────────────────


async def test_a_verbatim_quote_is_accepted(session: AsyncSession) -> None:
    _, chunk_id = await _seed_document(session, approved=True)
    result = await verify_svc.verify_evidence(
        session,
        [
            verify_svc.EvidenceItem(
                chunk_id=chunk_id,
                quoted_text="resistance to ceftriaxone requires review of the "
                "discharge prescription",
            )
        ],
    )
    assert result.accepted
    assert len(result.verified) == 1
    assert result.verified[0].ratio == 1.0
    # The offsets let the UI highlight rather than asking the reader to search.
    assert result.verified[0].char_end > result.verified[0].char_start
    assert result.verified[0].section_path == "4.2 > Urinary tract"


async def test_a_fabricated_quote_is_refused(session: AsyncSession) -> None:
    """★ The whole reason this module exists.

    A plausible clinical sentence that is not in the source. This is exactly the
    shape of the real failure measured on 2026-09-14 — "6.0 mEq/L or higher",
    fluent and invented.
    """
    _, chunk_id = await _seed_document(session, approved=True)
    result = await verify_svc.verify_evidence(
        session,
        [
            verify_svc.EvidenceItem(
                chunk_id=chunk_id,
                quoted_text="A critical potassium level is 6.0 mEq/L or higher, "
                "which can cause life-threatening arrhythmias",
            )
        ],
    )
    assert not result.accepted
    assert result.verified == []
    assert result.rejections[0].reason == verify_svc.REASON_FUZZY_BELOW


async def test_every_citation_failing_rejects_the_whole_response(
    session: AsyncSession,
) -> None:
    """★ Not "show it with fewer citations".

    An explanation whose every citation failed is a paragraph of confident prose
    with nothing behind it.
    """
    _, chunk_id = await _seed_document(session, approved=True)
    result = await verify_svc.verify_evidence(
        session,
        [
            verify_svc.EvidenceItem(chunk_id=chunk_id, quoted_text="invented one " * 3),
            verify_svc.EvidenceItem(chunk_id=chunk_id, quoted_text="invented two " * 3),
        ],
    )
    assert not result.accepted
    assert len(result.rejections) == 2


async def test_no_evidence_at_all_is_rejected_and_logged(
    session: AsyncSession,
) -> None:
    """An explanation citing nothing is not an explanation."""
    result = await verify_svc.verify_evidence(session, [])
    assert not result.accepted
    assert result.rejections[0].reason == verify_svc.REASON_NO_EVIDENCE


async def test_a_quote_from_an_unapproved_document_is_refused(
    session: AsyncSession,
) -> None:
    """★ 8.1: *only approved documents are retrievable* — enforced at
    verification too, not just at retrieval.

    A document whose approval is revoked between retrieval and verification must
    not be shown, and the chunk simply ceases to exist as far as this is
    concerned.
    """
    _, chunk_id = await _seed_document(session, approved=False)
    result = await verify_svc.verify_evidence(
        session,
        [
            verify_svc.EvidenceItem(
                chunk_id=chunk_id,
                quoted_text="resistance to ceftriaxone requires review of the "
                "discharge prescription",
            )
        ],
    )
    assert not result.accepted
    assert result.rejections[0].reason == verify_svc.REASON_CHUNK_MISSING


async def test_whitespace_and_dash_drift_is_tolerated(session: AsyncSession) -> None:
    """A model re-typing a quote with different punctuation is still citing it.

    This is what the fuzzy threshold is *for* — as distinct from paraphrase,
    which the test above proves it refuses.
    """
    _, chunk_id = await _seed_document(session, approved=True)
    result = await verify_svc.verify_evidence(
        session,
        [
            verify_svc.EvidenceItem(
                chunk_id=chunk_id,
                quoted_text="resistance   to ceftriaxone\nrequires review of the "
                "discharge prescription",
            )
        ],
    )
    assert result.accepted


async def test_a_two_word_quote_verifies_against_nothing(
    session: AsyncSession,
) -> None:
    """★ "the" appears in every document ever written.

    Without a minimum length a citation could be satisfied by a common word,
    which would make the verifier decorative.
    """
    _, chunk_id = await _seed_document(session, approved=True)
    result = await verify_svc.verify_evidence(
        session, [verify_svc.EvidenceItem(chunk_id=chunk_id, quoted_text="the urine")]
    )
    assert not result.accepted


async def test_every_rejection_is_counted(session: AsyncSession) -> None:
    """★ `ai_rejections` is the hallucination rate. A verifier that silently
    discards is indistinguishable from a model that never hallucinates."""
    before = (
        await session.execute(text("SELECT count(*) FROM ai_rejections"))
    ).scalar_one()

    _, chunk_id = await _seed_document(session, approved=True)
    await verify_svc.verify_evidence(
        session,
        [verify_svc.EvidenceItem(chunk_id=chunk_id, quoted_text="not in here " * 4)],
    )
    await session.flush()

    after = (
        await session.execute(text("SELECT count(*) FROM ai_rejections"))
    ).scalar_one()
    assert after == before + 1

    row = (
        await session.execute(
            text(
                "SELECT reason, quoted_text, best_ratio FROM ai_rejections "
                " ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).first()
    assert row is not None
    # The invented text is kept verbatim: "the model quoted something not in the
    # source" is only checkable later if the something is stored.
    assert row.quoted_text is not None
    assert row.best_ratio is not None


# ── 8.3 retrieval ─────────────────────────────────────────────────────


async def test_keyword_search_finds_an_approved_chunk(session: AsyncSession) -> None:
    await _seed_document(session, approved=True)
    hits = await retrieve_svc.keyword_search(session, "ceftriaxone resistance urine")
    assert hits


async def test_an_unapproved_document_is_never_retrieved(
    session: AsyncSession,
) -> None:
    """★ *An unapproved guideline must not reach a doctor.*"""
    _, chunk_id = await _seed_document(session, approved=False)
    hits = await retrieve_svc.keyword_search(session, "ceftriaxone resistance urine")
    assert str(chunk_id) not in {h[0] for h in hits}


async def test_retrieval_works_with_no_embedder(session: AsyncSession) -> None:
    """★ THE ONE RULE for one query: a missing model costs ranking, not guidance."""
    await _seed_document(session, approved=True)
    chunks = await retrieve_svc.retrieve(
        session, "ceftriaxone resistant Escherichia coli urine", embed=None
    )
    assert chunks
    assert chunks[0].via == "keyword"


async def test_nothing_relevant_returns_empty_so_node_b_is_never_called(
    session: AsyncSession,
) -> None:
    """★ 8.3's relevance floor. Calling the model with irrelevant context is how
    a confident answer gets built on nothing."""
    await _seed_document(session, approved=True)
    chunks = await retrieve_svc.retrieve(
        session, "zzzqqq nonexistent terminology xyzzy", embed=None
    )
    assert chunks == []


def test_rrf_rewards_agreement_between_the_two_halves() -> None:
    """A chunk both halves found outranks one only a single half loved."""
    keyword = [("a", 9.0), ("b", 8.0)]
    vector = [("b", 0.9), ("c", 0.8)]
    fused = dict(retrieve_svc.reciprocal_rank_fusion(keyword, vector))
    assert fused["b"] > fused["a"]
    assert fused["b"] > fused["c"]


def test_the_query_is_built_from_structured_fields_only() -> None:
    """8.3: *from the structured JSON, never the PDF* — so a patient's name
    cannot reach the query even by accident."""
    query = retrieve_svc.build_query(
        organism="Escherichia coli", resistant_to="ceftriaxone", specimen="urine"
    )
    assert query == "Escherichia coli ceftriaxone urine"


# ── 8.4 generation ────────────────────────────────────────────────────


def test_reasoning_is_stripped_even_with_no_opening_tag() -> None:
    """★ The measured `qwen3` shape: a closing tag and no opening one.

    A `<think>.*?</think>` regex matches nothing here and passes the whole
    monologue through to a clinician.
    """
    raw = "Hmm, the user wants me to...\nLet me consider.\n</think>\n\nThe answer."
    assert visible_text(raw) == "The answer."
    assert "Hmm" not in visible_text(raw)


def test_text_with_no_think_tag_is_returned_whole() -> None:
    assert visible_text("  Just an answer.  ") == "Just an answer."


def test_json_is_found_even_when_wrapped_in_prose() -> None:
    raw = (
        "Sure! Here is the JSON:\n"
        '{"explanation": "Two sentences here.", '
        '"evidence": [{"source": 1, "quote": "a verbatim quote from a source"}]}\n'
        "Hope that helps."
    )
    parsed = parse_response(raw)
    assert parsed is not None
    assert parsed.explanation == "Two sentences here."
    assert parsed.evidence == [("1", "a verbatim quote from a source")]


def test_malformed_output_is_none_not_an_exception() -> None:
    """★ Every failure path is silence. A malformed reply means no explanation,
    never a crash and never a partial one shown as whole."""
    assert parse_response("I'm sorry, I can't help with that.") is None
    assert parse_response("{not json at all}") is None
    assert parse_response('{"evidence": []}') is None
