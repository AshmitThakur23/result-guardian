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

import re
import uuid
from typing import get_args

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers import explain as explain_router
from app.services import settings_store
from app.services.rag import retrieve as retrieve_svc
from app.services.rag import verify as verify_svc
from app.services.rag.generate import parse_response, visible_text

from ._phase5 import bearer, build_world

POLICY = (
    "Escherichia coli isolated from urine at greater than 100,000 CFU/mL with "
    "resistance to ceftriaxone requires review of the discharge prescription. "
    "Alternative agents should be selected on the reported sensitivity panel."
)


async def _seed_document(
    session: AsyncSession, *, approved: bool, chunk_text: str = POLICY
) -> tuple[uuid.UUID, uuid.UUID]:
    """One document and one chunk. ``approved`` decides visibility.

    Creates its own approver rather than borrowing ``(SELECT id FROM users
    LIMIT 1)``. That shortcut passed locally and failed on CI, where the users
    table is empty: it left `approved_by` NULL beside a non-NULL `approved_at`,
    and `ck_kb_documents_approval_complete` correctly refused a half-written
    approval. The constraint was right; the fixture was borrowing state it did
    not own.
    """
    tag = uuid.uuid4().hex[:12]
    approver_id = (
        await session.execute(
            text(
                "INSERT INTO users "
                "  (id, employee_code, full_name, role, is_active, "
                "   created_at, updated_at) "
                "VALUES (gen_random_uuid(), :code, 'KB Approver', 'admin', true, "
                "        now(), now()) "
                "RETURNING id"
            ),
            {"code": f"KB-{tag}"},
        )
    ).scalar_one()
    doc_id = (
        await session.execute(
            text(
                "INSERT INTO kb_documents "
                "  (id, title, publisher, doc_type, version, sha256, "
                "   approved_by, approved_at, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :title, 'hospital', "
                "        'antibiotic_policy', '1', :sha, "
                "        CASE WHEN :ok THEN CAST(:approver AS uuid) END, "
                "        CASE WHEN :ok THEN now() END, now(), now()) "
                "RETURNING id"
            ),
            {
                "title": f"Antibiotic Policy {tag}",
                "sha": tag.ljust(64, "0"),
                "ok": approved,
                "approver": str(approver_id),
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


# ── the ingest endpoint's vocabulary ──────────────────────────────────


def _check_vocabulary(definition: str) -> set[str]:
    """Pull the allowed values out of a `= ANY (ARRAY[...])` CHECK definition."""
    return set(re.findall(r"'([a-z_]+)'::character varying", definition))


@pytest.mark.asyncio
async def test_ingest_vocabulary_matches_the_database(session: AsyncSession) -> None:
    """★ The API's `Literal`s and the table's `CHECK`s say the same thing.

    Read from `pg_constraint` rather than written out again here, so this fails
    if either side drifts. A value the API accepts and the database refuses is
    a 500; a value the database allows and the API refuses is a document nobody
    can upload — and both are silent until somebody tries.
    """
    rows = (
        await session.execute(
            text(
                # `AS definition`, not `AS def` -- the row is read as an
                # attribute and `r.def` is a syntax error in Python.
                "SELECT conname, pg_get_constraintdef(oid) AS definition "
                "  FROM pg_constraint "
                " WHERE conrelid = 'kb_documents'::regclass AND contype = 'c'"
            )
        )
    ).fetchall()
    defs = {r.conname: r.definition for r in rows}

    # Read off the **request model's own fields**, not the standalone aliases.
    # Checking `explain_router.Publisher` would pass even if `IngestRequest`
    # went back to a bare `str` -- which is exactly what happened when this
    # test was first written, and it passed against the unfixed code.
    fields = explain_router.IngestRequest.model_fields
    assert _check_vocabulary(defs["ck_kb_documents_publisher"]) == set(
        get_args(fields["publisher"].annotation)
    )
    assert _check_vocabulary(defs["ck_kb_documents_doc_type"]) == set(
        get_args(fields["doc_type"].annotation)
    )


@pytest.mark.asyncio
async def test_an_unknown_publisher_is_a_422_not_a_500(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """★ A bad value is a bad request, not a crash.

    `publisher` was an unvalidated `str`, so anything the `CHECK` refused came
    back as an unhandled `IntegrityError` — a 500, with the reason visible only
    in the container log. An admin who typed "NICE" instead of "nice" was told
    the server was broken.

    **Signed in as a real admin**, because authorisation runs before validation:
    an anonymous call is refused at the door and never reaches the field this
    test is about. The first draft allowed a 401 here and passed against the
    unfixed code, proving nothing.
    """
    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")

    response = await client.post(
        "/api/kb/documents",
        headers=headers,
        json={
            "title": "A document with a publisher nobody has heard of",
            "publisher": "NICE",
            "doc_type": "guideline",
            "document_text": "x" * 60,
        },
    )
    assert response.status_code == 422, response.text

    # The message has to name what is allowed, or the admin is left guessing.
    assert "hospital" in response.text


@pytest.mark.asyncio
async def test_the_kill_switch_stops_generation(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ Turning the AI off actually disconnects NODE B.

    Until 2026-09-15 `/explain` never read the kill switch. An admin could
    switch inference off, watch the health badge go grey, and the Explain
    button would carry on calling NODE B — because the only thing reading the
    switch was the liveness probe. Found by the Phase 8 E2E spec, which set the
    switch and then waited for a message that never came.

    `generate` is replaced with something that raises, so if the switch is ever
    bypassed again this fails loudly instead of quietly returning a real answer
    that happens to look fine.
    """

    async def _must_not_be_called(**kwargs: object) -> object:
        raise AssertionError("NODE B was called with the kill switch engaged")

    monkeypatch.setattr(explain_router.generate_svc, "generate", _must_not_be_called)

    ids = await build_world(session)
    headers = await bearer(client, ids, "admin")
    await _seed_document(session, approved=True)
    await settings_store.set_value(session, "llm_enabled", False)
    await session.commit()

    # A real case: the endpoint now refuses an unknown one with a 404 before
    # doing any work, so a random UUID here would test the guard rather than
    # the kill switch.
    response = await client.post(
        f"/api/cases/{ids['case']}/explain",
        headers=headers,
        json={"query": "Escherichia coli ceftriaxone urine"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["explanation"] is None
    assert body["note"] == explain_router.NODE_B_DOWN
    # ★ Retrieval still ran, and says so. The switch removes the *generation*,
    # not the system's ability to find the guidance.
    assert body["sources_considered"] > 0


@pytest.mark.asyncio
async def test_the_guidance_is_still_returned_when_generation_fails(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 8.5's fallback: no explanation, but the guidance is still shown.

    NODE B being off removes the paraphrase. It does not remove the hospital's
    own approved text, which was found on NODE A by plain keyword search and is
    the part a clinician actually needs. Returning an apology while the answer
    sits in the knowledge base would be the wrong way to fail, and the response
    model documented this fallback for a while before any field carried it.

    The passages come back as `retrieved`, never as `evidence`: `evidence`
    means "a model said this and the verifier confirmed it", and these have been
    near no model at all.
    """

    async def _node_b_is_down(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(explain_router.generate_svc, "generate", _node_b_is_down)

    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    await _seed_document(session, approved=True)
    await session.commit()

    response = await client.post(
        f"/api/cases/{ids['case']}/explain",
        headers=headers,
        json={"query": "Escherichia coli ceftriaxone urine"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["explanation"] is None
    assert body["note"] == explain_router.NODE_B_DOWN

    # ★ The guidance survived the failure.
    assert body["retrieved"], "NODE B went down and took the guidance with it"
    assert "ceftriaxone" in body["retrieved"][0]["chunk_text"].lower()
    assert body["retrieved"][0]["document_title"]

    # ...and is not being passed off as a verified citation.
    assert body["evidence"] == []
    assert "quoted_text" not in body["retrieved"][0]
    assert "match_ratio" not in body["retrieved"][0]


# ── 8.3 a question typed by a person ──────────────────────────────────


@pytest.mark.asyncio
async def test_a_natural_language_question_still_finds_the_guidance(
    session: AsyncSession,
) -> None:
    """★ A question phrased as a question must not retrieve nothing.

    `websearch_to_tsquery` joins bare terms with AND. That is right for the
    structured query the panel builds, and wrong for a clinician typing "why
    does ceftriaxone resistance matter" — which requires the policy to contain
    the words "why", "does" and "matter". No policy does, so the whole question
    retrieved **nothing** and the panel said "no approved guidance" while the
    guidance sat one row away.

    Found by the Phase 8 Guidance E2E on 2026-09-15, immediately after the
    free-text question box was added and before anybody had typed a real
    sentence into it.
    """
    await _seed_document(session, approved=True)

    strict = await retrieve_svc.keyword_search(
        session, "Escherichia coli ceftriaxone urine"
    )
    assert strict, "the structured query should still match on AND"

    asked = await retrieve_svc.keyword_search(
        session, "why does ceftriaxone resistance matter"
    )
    assert asked, "a question phrased as a question retrieved nothing"


@pytest.mark.asyncio
async def test_the_forgiving_pass_does_not_match_everything(
    session: AsyncSession,
) -> None:
    """★ Widening the query must not turn retrieval into "always something".

    The OR fallback exists so a question is not defeated by the word "why". If
    it also matched documents sharing no clinical term, the relevance floor
    would be the only thing between a clinician and a confident answer built on
    an unrelated policy — and "no guidance" would stop being sayable at all.
    """
    await _seed_document(session, approved=True)

    assert (
        await retrieve_svc.keyword_search(
            session, "what should I tell the patient about the report"
        )
        == []
    ), "a question made only of scaffolding words matched a document"

    assert (
        await retrieve_svc.keyword_search(session, "why does the roof leak")
    ) == [], "an unrelated question matched clinical guidance"

    # ★ The one that actually regressed. Every word here is furniture except
    # "organism" and "guideline", which a clinical corpus is full of. The first
    # OR fallback returned the antibiotic policy for this and NODE B was duly
    # called on it -- caught by the Phase 8 E2E, not by this file.
    assert (
        await retrieve_svc.keyword_search(
            session, "zzzqqq no such organism anywhere in any guideline"
        )
        == []
    ), "a nonsense query matched on generic words and would have called NODE B"


@pytest.mark.asyncio
async def test_explaining_a_case_that_does_not_exist_is_a_404(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ An unknown case is refused before any work is done.

    This endpoint used to accept any UUID. It ran retrieval, spent ~14 s of
    NODE B's GPU, and then returned **500** from inside the verifier, because
    `ai_rejections.case_id` references `pending_cases` and a rejected citation
    cannot be recorded against a case that is not there.

    It surfaced as a *flaky* E2E — one run in three — because it only fires
    when the model happens to write a citation that fails verification, which
    varies run to run. A missing existence check plus a non-deterministic model
    is how a real defect hides as a flake, and CLAUDE.md is explicit that a
    flake gets found rather than papered over.

    `generate` is replaced with something that raises, so this also proves the
    refusal happens **before** NODE B is dialled rather than after.
    """

    async def _must_not_be_called(**kwargs: object) -> object:
        raise AssertionError("NODE B was called for a case that does not exist")

    monkeypatch.setattr(explain_router.generate_svc, "generate", _must_not_be_called)

    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    await _seed_document(session, approved=True)
    await session.commit()

    response = await client.post(
        f"/api/cases/{uuid.uuid4()}/explain",
        headers=headers,
        json={"query": "Escherichia coli ceftriaxone urine"},
    )

    assert response.status_code == 404, response.text
    # Not a 500, and not a cheerful 200 either -- a caller asking about a case
    # that is not there has made a mistake and is entitled to be told.
    assert response.status_code != 500
