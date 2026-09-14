# Phase 7 — Extraction, Normalisation, Matching

**Goal:** document → structured JSON → **correct** pending case, with confidence.
**Duration:** 3 weeks · **Node:** A, with NODE B as a fallback for step 3 only · **Depends on NODE B?** Optional fallback only

---

## 📍 STATUS SUMMARY — Phase 7

> ⚠️ **Do not start this phase until Exit Gate 6 passes.**

### 🔵 STARTED 2026-09-14 as a KNOWING EXCEPTION — Exit Gate 6 is OPEN

**Exit Gate 6 has not passed and cannot**, because two of its three clauses are
measurements over **200 real PDFs** and the corpus stands at **0**. The project
owner instructed that Phase 7 proceed. Recorded here and in
[`PROGRESS.md`](../../PROGRESS.md) so it is a decision on the record rather than
an oversight — the build plan's own ordering rule says otherwise, and that
disagreement should stay visible.

**🔴 Exit Gate 7 cannot close either, for the same reason.** It is measured on a
gold set of **100 labelled real documents**, and the same missing corpus blocks
it. Synthetic fixtures are unit-test material and **may never be counted toward
either gate** — a test asserts this.

**What this phase can honestly deliver without the corpus:** the schema, the
extraction cascade, the normalisation tables, the matching arithmetic, the
status-supersession rules, the review queues, and an evaluation harness that is
**ready to run the moment real documents exist**. What it cannot deliver is the
accuracy numbers, and those are the gate.

▶ The corpus request is §4 of [`../phase-6-outstanding.md`](../phase-6-outstanding.md).

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 7.1 Report classification | A (+B fallback) | ✅ **done** | Rules score structurally; **two types tying is `unknown`, not a coin toss**. NODE B absent is recorded as *retryable* — "could not ask" ≠ "asked and it didn't know" |
| 7.2 Templates before LLM ★ | A (+B fallback) | ✅ **code done** | Cascade: template → generic → model → human. `extract()` takes the model as an **argument**, so the module has no import path to a network client. 🔴 Template **editor UI** not built |
| 7.3 Field extraction | A (+B fallback) | ✅ **code done** | Values (censored kept censored), ranges (sex-specific unresolved without a sex), status stamps, narrative sections. 🔴 `document_span_id` **plumbing** not wired |
| 7.4 Normalisation | A (+B fallback) | ✅ **code done** | Full cascade with a 0.85 floor and write-back. 🔴 **LOINC release not loaded** — `loinc_terms` is empty |
| 7.5 Matching — NO AI ★ | A | ✅ **done** | Pure arithmetic. **Ties refused, signals never summed, missing date = disagreement.** Verified red-then-green |
| 7.6 Status handling | A | ✅ **done** | PRELIMINARY holds + timer · FINAL supersedes without double-alerting · AMENDED re-opens with distinct wording · **no stamp ≠ final** |
| 7.7 Evaluation | A | ✅ **harness done** | Per-field precision/recall; wrong auto-matches **counted, never averaged**. 🔴 **Nothing to measure — corpus is 0** |
| **Exit Gate 7** | A (+B fallback) | 🔴 **CANNOT CLOSE** | Measured on **100 labelled real documents**; corpus is **0**. Same blocker as Exit Gate 6 |

---

## 7.1 Report classification

- [x] **Rules first:** header keywords, lab name, section titles, presence of a sensitivity grid
- [x] **`document_classifications`** table with confidence and method (`rule|llm`)
- [x] LLM classifier (NODE B) **only as a fallback** for unmatched documents
- [x] Below threshold → `needs_review`
- [x] **If NODE B is unreachable, unmatched documents go to `needs_review`, never to a guess**

## 7.2 Extraction strategy — templates before LLM ★

**Order of attempts:**

1. **Lab-specific template** — `extraction_templates` table: lab_id, report_type, anchor regexes, column positions, row patterns, version
2. **Generic table parser** — Docling table output mapped to analyte rows
3. **LLM extraction** — Qwen3 on NODE B, strict JSON schema, **temperature 0**, only for what 1 and 2 missed
4. **Human** — review queue

> Templates are boring, fast, free and reproducible. A hospital sends reports from **5–10 labs; 10 templates cover 90% of volume. Do not skip straight to the LLM.**

- [x] `extraction_templates` table + cascade implemented
- [ ] Template editor UI for admins (define anchors visually on a sample report)
- [x] Every extracted field records `method` and `confidence`
- [x] **NODE B down → attempts 1, 2 and 4 still run. Throughput drops, correctness does not.**

## 7.3 Field extraction

- [x] Analyte rows: name, value, unit, reference range, lab's own abnormal flag
- [x] Reference range parsing: `10-20`, `< 5`, `>= 3.5`, `Male: 13-17 / Female: 12-15`, `Negative`
- [x] Value parsing: numbers, censored values, text results (`Positive`, `Not Detected`)
- [x] Culture: organism block + sensitivity grid (antibiotic column, S/I/R column, MIC column)
- [x] Narrative: section segmentation (Findings / Impression / Conclusion)
- [x] Report metadata: patient name, MRN, order ID/accession, collection date, report date, status stamp (`PRELIMINARY`/`FINAL`/`AMENDED`)
- [ ] **Every field carries its `document_span_id`**

## 7.4 Normalisation

- [ ] Load LOINC release into **`loinc_terms`**
- [x] **`test_synonyms`** — raw string → loinc_code, per lab (`S. Creat`, `Creat`, `SR. CREATININE` → `2160-0`)
- [x] Matching cascade: exact synonym → normalised string (lowercase, strip punctuation, unaccent) → **trigram similarity ≥ 0.85** → LOINC search → unmapped
- [ ] Unmapped terms land in an admin queue; **mapping them once fixes them forever**
- [x] Unit normalisation table with conversion factors (mg/dL ↔ µmol/L, g/L ↔ g/dL)
- [ ] Antibiotic name normalisation (reuse the Phase 3 synonym table)

## 7.5 Matching to pending cases — NO AI ★

> **AI never decides which patient a result belongs to.** This is scoring arithmetic and thresholds. **A wrong match puts a result on a stranger's file.**

**Score candidates:**

| Signal | Score |
|---|---|
| `external_order_id` / accession exact | **1.00 (accept)** |
| MRN + test_code + collection_date | 0.90 |
| MRN + LOINC + date ±1d | 0.85 |
| name trigram + DOB + test + date ±2d | 0.70 |

**Thresholds:**

- **≥ 0.90** → auto-match
- **0.70–0.90** → review queue with suggestions ranked
- **< 0.70** → unmatched queue

**Rules:**

- [x] Multiple candidate cases → **always human review, never guess**
- [x] Result arrives with no pending case (never-discharged patient, OPD) → create an **orphan case** linked to the encounter
- [x] **`match_decisions`** table logging score, method, chosen candidate, reviewer

## 7.6 Status handling

- [x] `PRELIMINARY` → store, hold, set stale timer
- [x] `FINAL` supersedes a preliminary on the same order — link `superseded_by_result_id`, **do not double-alert**
- [x] `AMENDED` / `CORRECTED` → reopen a closed case, mark the flag as amendment-driven, notify with **distinct wording**

## 7.7 Evaluation

- [ ] Labelled gold set: **100 documents** with hand-written expected JSON
- [ ] `make eval-extraction` reports per-field precision/recall and match accuracy
- [ ] **Run on every PR touching extraction** — your only defence against silent regression
- [ ] Track metrics over time in `docs/extraction-accuracy.md`

---

## ✅ EXIT GATE 7

On the gold set:

- [ ] **≥95%** analyte extraction accuracy
- [ ] **≥98%** matching accuracy on order-ID-bearing reports
- [ ] **ZERO incorrect auto-matches** — wrong-patient matching is the one failure mode that must be zero. **Prefer the review queue every time.**

---

**Cross-ref:** [architecture/02-ingestion-extraction-matching.md](../architecture/02-ingestion-extraction-matching.md) (Steps 5–6)
