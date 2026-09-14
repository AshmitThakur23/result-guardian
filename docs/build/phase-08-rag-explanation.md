# Phase 8 — RAG Explanation Layer

**Goal:** on demand, explain **why** a flag is critical, with a **verifiable citation**.
**Duration:** 3 weeks · **Nodes:** A (retrieval, verification) + B (generation only) · **Depends on NODE B?** Yes — degrades cleanly

> **Non-negotiable: this feature is optional to the workflow. If NODE B is down, everything else works.**

---

## 📍 STATUS SUMMARY — Phase 8

> ⚠️ **Do not start this phase until Exit Gate 7 passes.**

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 8.1 Knowledge base ingestion | A | ✅ **done — text**  | `kb_documents` / `kb_chunks`, migration `0016`. **Approval is a column and retrieval filters on it** — an unapproved guideline is invisible, proven by E2E as well as unit test. Section-aware chunker written: 400–600 tokens, 15 % overlap, **never splits a table row**. Identifier scan at ingest. 🔴 **The only content loaded is a labelled demo fixture** (`api/scripts/seed_kb_demo.py`) — no real guidance, no antibiogram. 🔴 **8.2's PDF path is not written**: ingestion takes text, so a PDF guideline cannot be loaded yet |
| 8.2 Indexes | A | ✅ **done** | HNSW `(m=16, ef_construction=64)` + GIN on `tsv`, kept in step by a trigger so a chunk written by any route is indexed the same way |
| 8.3 Retrieval | A | ✅ **done** | Keyword + vector, RRF `k=60`. **Degrades to keyword-only with no embedder** — ranking suffers, guidance does not. Query built from structured fields, so a patient name cannot reach it |
| 8.4 Generation — NODE B ★ | B | ✅ **done** | `rsplit` on `</think>`, not a paired-tag regex. **Every failure path returns `None`** — timeout, refusal, malformed JSON, unreachable node all mean *no explanation*, flag untouched |
| 8.5 Span verifier ★ | A | ✅ **done** | **Plain code, no AI, no import path to NODE B.** Fuzzy 0.95 for typography, never paraphrase. Min 20 chars. All citations failing rejects the **whole** response. Proven by disabling it — 3 tests went red |
| 8.6 UI | A | 🔵 **core done & verified; 3 items open** | `ExplainPanel.tsx`. **The verified quote is highlighted inside its surrounding passage** using the verifier's own offsets, so a clinician checks it rather than trusts it; provenance sits **above** the prose; rejections shown **including zero**; disclaimer persistent in every state. **With NODE B off the approved guidance is still shown** — retrieval is all NODE A, so the outage costs the paraphrase and nothing else. Proven end to end against a live NODE B: the E2E asserts the highlighted text is actually present in a `kb_chunks` row, not merely that something got styled. ★ Two real defects found and fixed here — **the admin kill switch did not stop generation**, and **a dead NODE B took the retrieved guidance down with it**. 🔴 Open: source click-through (blocked on 8.2's PDF path), `ai_feedback` (table does not exist; **8.7 needs it**), and response caching |
| 8.7 Evaluation | A | 🔴 blocked | Needs clinician time — same input as Exit Gate 3 |
| **Exit Gate 8** | A + B | 🔴 **CANNOT CLOSE** | Needs a clinician's judgement on generated explanations |

---

### 🔵 STARTED 2026-09-15 as a KNOWING EXCEPTION — Exit Gate 7 is OPEN

Exit Gate 7 cannot close: its clauses are measured over **100 labelled real
documents** and the corpus is **0**, the same blocker holding Exit Gates 6 and 7.
The project owner instructed that Phase 8 proceed. Recorded here so it is a
decision on the record rather than an oversight.

**🔴 Exit Gate 8 cannot close either.** It needs a clinician's judgement on
generated explanations, which is the same missing input as Exit Gate 3.

**What this phase can honestly deliver:** the knowledge store with approval
gating, hybrid retrieval, generation on NODE B, and — the part that matters —
**the span verifier, which is plain code and needs no corpus at all**. What it
cannot deliver is a measured hallucination rate over real guidance.

### ⚠️ Vector search degrades to keyword search — a recorded deviation

8.3 specifies **bge-m3 embeddings on NODE A's CPU**. Installing torch plus the
model is ~5 GB, which is not available on this machine over the campus link.

So `kb_chunks.embedding` is **nullable**, and retrieval runs hybrid when an
embedder exists and **keyword-only when it does not**. That is THE ONE RULE
applied to Phase 8: a missing model costs *ranking quality*, never *guidance*.
Making the column NOT NULL would have turned an absent model into an absent
feature.

▶ The embedder is a drop-in: install it, run the re-embed job, and the vector
half activates with no code change.

## 8.1 Knowledge base ingestion — NODE A

- [ ] **`kb_documents`** — id, title, publisher (WHO/ICMR/hospital), doc_type, version, effective_from, effective_to, source_url_or_path, sha256, approved_by, approved_at
  - **Only approved documents are retrievable.** An unapproved guideline must not reach a doctor.
- [ ] **`kb_chunks`** — id, kb_document_id, section_path, page_no, char_start, char_end, text, token_count, embedding `VECTOR(1024)`, tsv `TSVECTOR`
- [ ] Chunking: **section-aware, 400–600 tokens, 15% overlap, never split a table row**
- [ ] **No patient data ever enters this store** — enforce with a review step at ingestion
- [ ] Content to load: WHO guidelines, ICMR AMR treatment guidelines, hospital SOPs, **hospital antibiogram (highest local value)**, antibiotic policy, NLEM reference, departmental protocols

## 8.2 Indexes — NODE A

- [ ] HNSW on embedding: `USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)`
- [ ] GIN on `tsv`
- [ ] Re-embed job when a document version changes; **old version retired, not deleted**

## 8.3 Retrieval — NODE A, CPU, no network

- [ ] Query construction **from the structured JSON, never the PDF**: organism + resistant drug + specimen, or analyte + direction + magnitude
- [ ] Vector search **top-20** (bge-m3, query prefix per model card)
- [ ] Keyword search **top-20** (`ts_rank_cd`, weighted title > body)
- [ ] Fusion: **Reciprocal Rank Fusion (k=60)**
- [ ] Rerank **top-30** with bge-reranker-v2-m3 → **keep top-5**
- [ ] **Relevance floor:** best reranker score below threshold → return *"no verified guidance found"*, **do not call NODE B at all**

## 8.4 Generation — NODE B ★

> **Ollama runs on NODE B, not in the NODE A compose file. It is a deployment target, not a service.**

### 🔴 Measured before starting: `qwen3:4b` leaks its reasoning, and the usual fix does not work

**Measured 2026-09-14 against the live NODE B** (`172.25.54.48:11434`), once the
link was up. Recorded here rather than discovered halfway through 8.4.

`qwen3` is a **reasoning model**, and it emits its internal monologue into the
ordinary content field. Three things that look like they should stop it:

| Attempt | Result |
|---|---|
| `think: false` on `/api/generate` | ❌ still reasons |
| `think: false` on `/api/chat` | ❌ still reasons |
| `/no_think` prefix in the prompt | ❌ **returns empty content** — worse than leaking |

**And the trap: the model closes the block without opening it.** A real response
ends `…matches what they asked for.\n</think>\n\nOK` — there is a `</think>` and
**no `<think>`**. So the strip every tutorial reaches for:

```python
re.sub(r"<think>.*?</think>", "", content, flags=re.S)   # ❌ matches nothing
```

silently does nothing, and the model's entire monologue lands in the UI. Measured:

```
naive regex : 'Hmm, the user just asked me to reply with only the word "OK". That's s…'
rsplit      : 'OK'
```

**What works — take everything after the LAST closing tag:**

```python
def visible(content: str) -> str:
    if "</think>" in content:
        return content.rsplit("</think>", 1)[1].strip()
    return content.strip()
```

Consequences for 8.4, all of which affect the task list below:

- [ ] **Budget tokens for reasoning, not just for the answer.** A one-word reply
      cost **107 eval tokens** and 4.1 s. A `num_predict` sized for the answer
      alone truncates mid-thought and returns **nothing usable** — that is what
      the 16- and 64-token runs produced.
- [ ] **Strip with `rsplit`, never with a paired-tag regex**, and unit-test it
      against a response that has a closing tag and no opening one.
- [ ] **Treat a response containing no `</think>` as suspicious**, not as clean —
      it may equally mean the budget ran out mid-monologue.
- [ ] **Re-benchmark `format: json` with reasoning on.** The JSON-schema
      requirement below and a model that thinks in prose are in tension, and it
      is untested which wins.

⚠️ **And a reminder of why the span verifier (8.5) is not optional.** Asked *"what
is a critical potassium level?"* the model answered **"6.0 mEq/L or higher"** —
fluent, plausible, and **not from any source this system holds**. Exactly the
kind of unverified clinical claim [`CLAUDE.md`](../../CLAUDE.md) forbids reaching
a clinician. The model is a phrasing engine over retrieved text, never an
authority.

### 🔴 The 30-second budget has only 27 % headroom — measured, not estimated

A **realistic** request was timed end to end from NODE A on 2026-09-14: one
retrieved policy chunk (~60 words) and *"write two sentences for a doctor, do
not add facts."*

| | |
|---|---|
| **Elapsed** | **21.9 s** — against the 30 s budget below |
| Tokens generated | 569 |
| Throughput (from NODE A) | **26.0 tok/s** |
| Reasoning output | **2,361 characters** |
| Answer output | **222 characters** |

> **The model spends roughly nine tenths of its output thinking, and every
> token of it is discarded.** That is what the budget is actually paying for.

The answer itself was faithful — it added no facts and stayed inside the source
— so the approach is sound. The *cost* is the problem, and 8.1 s of headroom on
a short chunk is not a margin, it is a coincidence.

**Hardware context** — measured on NODE B the same day: RTX 3050, **2,939 MiB of
4,096 MiB VRAM in use**, and `ollama ps` reports a **29 % CPU / 71 % GPU** split
because the model is 4.2 GB and the card holds 4.0 GB. Locally NODE B sees
31.1 tok/s; NODE A sees 26.0 across the LAN. **`qwen3:4b` is the right choice for
this card** — 8B does not fit at all — but the partial CPU offload is why the
throughput is what it is, and it will not improve without different hardware.

**Before writing 8.4, decide which of these gives:**

- [ ] **Cap the reasoning, not just the answer.** Needs a measured floor: too low
      truncates mid-thought and returns *nothing* (proven above at 16 and 64).
- [ ] **Raise the budget above 30 s**, and say what the UI does while waiting.
- [ ] **Use a non-reasoning model for this task.** The generation step is
      phrasing retrieved text, not reasoning — the thinking may be pure waste
      here. Benchmark a non-reasoning model of similar size before committing.
- [ ] **Accept it and generate asynchronously**, so no clinician ever waits on
      NODE B. Fits RULE 2 best: the explanation arrives when it arrives, and its
      absence changes nothing.

⚠️ **Whatever is chosen, a timeout must degrade to "no explanation shown", never
to a blank panel or a spinner that never resolves.** An explanation is a
convenience; the flag beneath it is the product.

- [ ] Model pulled at provisioning, `OLLAMA_KEEP_ALIVE=-1` so it stays in VRAM
- [ ] GPU optional — 8B runs on CPU slowly. **Benchmark before promising anything.**
- [ ] Strict JSON schema output, **temperature 0**, `format: json`, max tokens capped:

```json
{
  "actionable": "string",
  "clinical_reason": "string",
  "clinical_summary": "string",
  "evidence": [
    {"claim": "string", "chunk_id": "uuid", "quoted_span": "string"}
  ]
}
```

**System prompt rules:**

- use only provided context
- every claim needs a `chunk_id`
- `quoted_span` must be **verbatim** from that chunk
- if context is insufficient, say so
- **never state a diagnosis**
- **never recommend a specific dose for a specific patient**

**Network handling — mandatory, not optional:**

```python
async def explain(case_id):
    chunks = await retrieve(case_id)          # NODE A. always works.
    if not chunks:
        return ExplainResult(status="no_verified_guidance")

    cached = await get_cached(case_id, ENGINE_VERSION)
    if cached:
        return cached

    if not settings.LLM_ENABLED:              # admin kill switch
        return ExplainResult(status="chunks_only", chunks=chunks)

    try:
        raw = await ollama_generate(chunks, timeout=settings.LLM_TIMEOUT_S)
    except (httpx.ConnectError, httpx.ReadTimeout):
        await log_degradation("llm_unreachable", case_id)
        return ExplainResult(status="chunks_only", chunks=chunks)

    verified = verify_spans(raw, chunks)
    if not verified.evidence:
        return ExplainResult(status="chunks_only", chunks=chunks)

    await cache(case_id, ENGINE_VERSION, verified)
    return verified
```

- [ ] **30s timeout** → degrade to *"explanation unavailable, showing retrieved guidelines"*
- [ ] Connection errors are **logged to a degradation metric, not swallowed**

## 8.5 Span verifier — NODE A, plain code, no AI ★

```
for each evidence item:
    chunk = fetch(chunk_id) → missing? reject
    normalise whitespace on both sides
    quoted_span in chunk.text? → no? try fuzzy ratio >= 0.95
    still no? → reject this evidence item

if zero evidence items survive → reject the whole response
```

- [ ] One retry with a stricter prompt, then fall back to showing the raw retrieved chunks with **no generated text**
- [ ] Log every rejection to **`ai_rejections`** — this is your **hallucination rate metric**
- [ ] **Target: zero unverified citations ever displayed**

## 8.6 UI

- [x] **"Explain" button** on the case detail page — **on demand only, never auto-run**. ✅ Enforced by the hook, not only by the button: `useExplain` is a **mutation**, so there is no mount-time fetch, no refetch-on-focus and no retry, each of which would spend ~14 s of NODE B's GPU on an answer nobody asked for
- [x] Response panel: explanation, then evidence cards. ✅ Provenance is rendered **above** the prose, and the prose is styled as a **quotation** — it is a paraphrase of a source, not a statement by this system. ⚠️ *Deviation:* the "action / reason" line is **not** repeated here; it is the rule engine's output and already sits above this panel in `ResultBlock`. Restating it inside an AI panel would attach the flag's authority to generated text
- [x] Each evidence card shows source document, section, page, and the **highlighted quoted span** — ✅ highlighted **inside its surrounding passage**, using the verifier's own offsets, so the quote is checkable rather than merely displayed. E2E asserts the highlighted text is genuinely present in a `kb_chunks` row
- [ ] 🔴 **Click-through to the source document at that page** — not built. There is no viewer route for a KB document, and `kb_documents` stores no file: 8.1 ingests **text**, so there is no page image to open. Blocked behind 8.2's PDF path
- [x] Persistent disclaimer: *"Information only. Retrieved from hospital-approved guidelines. The treating doctor decides."* — ✅ verbatim, and **outside every conditional branch**, so it is present in all states. A disclaimer that appeared only alongside a successful explanation would be missing in exactly the states a reader is most likely to misread
- [x] ★ When NODE B is unreachable: panel shows retrieved chunks with a plain notice, **button is not hidden** — ✅ **and this was a real gap**: the response model documented the fallback but no field carried it, so a dead NODE B took the guidance down with it. `retrieved` is a **separate type** from `evidence` (no `quoted_text`, no `match_ratio`) because those fields mean "a model said this and the verifier confirmed it". Proven red-then-green, and asserted in the E2E
- [ ] 🔴 **Feedback buttons (helpful / not helpful / wrong) → `ai_feedback`** — not built; the table does not exist in any migration. Needed for 8.7's usefulness rating, so it is a prerequisite for the gate rather than a nicety
- [ ] 🔴 **Cache responses per `(case_id, engine_version)`** — not built. Every click is a fresh ~14 s call. Acceptable while one person demonstrates it; **not** acceptable on a ward, where re-opening a case re-bills the GPU

## 8.7 Evaluation

- [ ] **100-question eval set** with known-correct guideline answers, written **with a clinician**
- [ ] Measure: retrieval recall@5, citation validity rate, clinician-rated usefulness (1–5)
- [ ] **Adversarial set:** questions with no answer in the KB → **correct behaviour is refusal**
- [ ] Record results in `docs/ai-evaluation.md` — the ethics/quality committee will ask for this

---

## ✅ EXIT GATE 8

- [ ] **Zero unverified citations** reach the UI across the full eval set
- [ ] Clinician rates **≥4/5** usefulness on **80%+** of critical-flag explanations
- [ ] **Powering off NODE B degrades only the Explain button** — every other test in the suite still passes

---

**Cross-ref:** [architecture/04-rag-explain-and-closure.md](../architecture/04-rag-explain-and-closure.md) (Step 10)
