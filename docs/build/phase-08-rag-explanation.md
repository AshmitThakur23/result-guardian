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
| 8.1 Knowledge base ingestion | A + B | ⬜ not started |  |
| 8.2 Indexes | A + B | ⬜ not started |  |
| 8.3 Retrieval | A + B | ⬜ not started |  |
| 8.4 Generation — NODE B ★ | A + B | ⬜ not started |  |
| 8.5 Span verifier ★ | A + B | ⬜ not started |  |
| 8.6 UI | A + B | ⬜ not started |  |
| 8.7 Evaluation | A + B | 🔴 blocked | needs clinician time |
| **Exit Gate 8** | A + B | ⬜ **not started** | |

---

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

- [ ] **"Explain" button** on the case detail page — **on demand only, never auto-run**
- [ ] Response panel: action / reason / summary, then evidence cards
- [ ] Each evidence card shows source document, section, page, and the **highlighted quoted span**
- [ ] Click-through to the source document at that page
- [ ] Persistent disclaimer: *"Information only. Retrieved from hospital-approved guidelines. The treating doctor decides."*
- [ ] When NODE B is unreachable: panel shows retrieved chunks with a plain notice, **button is not hidden**
- [ ] Feedback buttons (helpful / not helpful / wrong) → **`ai_feedback`** table
- [ ] Cache responses per `(case_id, engine_version)`

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
