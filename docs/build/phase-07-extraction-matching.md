# Phase 7 — Extraction, Normalisation, Matching

**Goal:** document → structured JSON → **correct** pending case, with confidence.
**Duration:** 3 weeks · **Node:** A, with NODE B as a fallback for step 3 only · **Depends on NODE B?** Optional fallback only

---

## 📍 STATUS SUMMARY — Phase 7

> ⚠️ **Do not start this phase until Exit Gate 6 passes.**

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 7.1 Report classification | A (+B fallback) | ⬜ not started |  |
| 7.2 Templates before LLM ★ | A (+B fallback) | ⬜ not started |  |
| 7.3 Field extraction | A (+B fallback) | ⬜ not started |  |
| 7.4 Normalisation | A (+B fallback) | ⬜ not started |  |
| 7.5 Matching — NO AI ★ | A (+B fallback) | ⬜ not started |  |
| 7.6 Status handling | A (+B fallback) | ⬜ not started |  |
| 7.7 Evaluation | A (+B fallback) | ⬜ not started |  |
| **Exit Gate 7** | A (+B fallback) | ⬜ **not started** | |

---

## 7.1 Report classification

- [ ] **Rules first:** header keywords, lab name, section titles, presence of a sensitivity grid
- [ ] **`document_classifications`** table with confidence and method (`rule|llm`)
- [ ] LLM classifier (NODE B) **only as a fallback** for unmatched documents
- [ ] Below threshold → `needs_review`
- [ ] **If NODE B is unreachable, unmatched documents go to `needs_review`, never to a guess**

## 7.2 Extraction strategy — templates before LLM ★

**Order of attempts:**

1. **Lab-specific template** — `extraction_templates` table: lab_id, report_type, anchor regexes, column positions, row patterns, version
2. **Generic table parser** — Docling table output mapped to analyte rows
3. **LLM extraction** — Qwen3 on NODE B, strict JSON schema, **temperature 0**, only for what 1 and 2 missed
4. **Human** — review queue

> Templates are boring, fast, free and reproducible. A hospital sends reports from **5–10 labs; 10 templates cover 90% of volume. Do not skip straight to the LLM.**

- [ ] `extraction_templates` table + cascade implemented
- [ ] Template editor UI for admins (define anchors visually on a sample report)
- [ ] Every extracted field records `method` and `confidence`
- [ ] **NODE B down → attempts 1, 2 and 4 still run. Throughput drops, correctness does not.**

## 7.3 Field extraction

- [ ] Analyte rows: name, value, unit, reference range, lab's own abnormal flag
- [ ] Reference range parsing: `10-20`, `< 5`, `>= 3.5`, `Male: 13-17 / Female: 12-15`, `Negative`
- [ ] Value parsing: numbers, censored values, text results (`Positive`, `Not Detected`)
- [ ] Culture: organism block + sensitivity grid (antibiotic column, S/I/R column, MIC column)
- [ ] Narrative: section segmentation (Findings / Impression / Conclusion)
- [ ] Report metadata: patient name, MRN, order ID/accession, collection date, report date, status stamp (`PRELIMINARY`/`FINAL`/`AMENDED`)
- [ ] **Every field carries its `document_span_id`**

## 7.4 Normalisation

- [ ] Load LOINC release into **`loinc_terms`**
- [ ] **`test_synonyms`** — raw string → loinc_code, per lab (`S. Creat`, `Creat`, `SR. CREATININE` → `2160-0`)
- [ ] Matching cascade: exact synonym → normalised string (lowercase, strip punctuation, unaccent) → **trigram similarity ≥ 0.85** → LOINC search → unmapped
- [ ] Unmapped terms land in an admin queue; **mapping them once fixes them forever**
- [ ] Unit normalisation table with conversion factors (mg/dL ↔ µmol/L, g/L ↔ g/dL)
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

- [ ] Multiple candidate cases → **always human review, never guess**
- [ ] Result arrives with no pending case (never-discharged patient, OPD) → create an **orphan case** linked to the encounter
- [ ] **`match_decisions`** table logging score, method, chosen candidate, reviewer

## 7.6 Status handling

- [ ] `PRELIMINARY` → store, hold, set stale timer
- [ ] `FINAL` supersedes a preliminary on the same order — link `superseded_by_result_id`, **do not double-alert**
- [ ] `AMENDED` / `CORRECTED` → reopen a closed case, mark the flag as amendment-driven, notify with **distinct wording**

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
