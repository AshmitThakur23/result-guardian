# Phase 3 — Clinical Rule Engine (manual input)

**Goal:** correct severity classification, fully deterministic, **no AI**.
**Duration:** 2–3 weeks · **Node:** A only · **Depends on NODE B?** No

> Requires a clinician's time — **book it now.**

---

## 3.1 Result schema

- [ ] **`results`** — id, order_id, case_id, report_status (`preliminary|final|amended|corrected`), reported_at, received_at, source (`manual|pdf|hl7|fhir`), source_ref, raw_payload JSONB, superseded_by_result_id
- [ ] **`result_analytes`** — id, result_id, seq, test_name_raw, loinc_code (null till Phase 7), value_raw, value_numeric, unit_raw, unit_normalized, ref_low, ref_high, ref_text, abnormal_flag_from_lab, source_page, source_bbox JSONB
- [ ] **`result_organisms`** — id, result_id, organism_name, colony_count, specimen_type
- [ ] **`result_sensitivities`** — id, organism_id, antibiotic_name, antibiotic_code, interpretation (`S|I|R`), mic_value
- [ ] **`result_narratives`** — id, result_id, section (`impression|findings|conclusion|microscopy`), text, source_page, source_offset_start, source_offset_end

## 3.2 Configuration tables — never hardcode ★

> Per-hospital configurability is the difference between a product and a demo. **Thresholds and delays live in tables an admin can edit, never in code.**

- [ ] **`panic_thresholds`** — id, test_code, loinc_code, sex, age_min_years, age_max_years, critical_low, critical_high, follow_up_low_multiplier, follow_up_high_multiplier, unit, source (e.g. "hospital SOP v3"), effective_from, effective_to
  - **Seed from your hospital's own critical value list, not from the internet.**
- [ ] **`clinical_keywords`** — id, term, category (`malignancy|infection|acute|incidental`), severity (`critical|follow_up`), requires_negation_check, active
- [ ] **`negation_patterns`** — id, pattern (regex), scope_words_before, scope_words_after
  - Must handle: *"no evidence of"*, *"negative for"*, *"ruled out"*, *"cannot exclude"*, *"unlikely"*, *"r/o"*
- [ ] **`antibiotic_synonyms`** — brand → generic → ATC code. **Indian brands matter:** Augmentin, Monocef, Taxim, Zifi, Mox.
- [ ] **`mdro_rules`** — organism + resistance pattern → auto-critical (MRSA, ESBL, CRE)
- [ ] **`rule_config`** — key/value JSONB for tunables: `slight_abnormal_factor`, `preliminary_hold_hours`, `culture_contaminant_threshold`, and compressed-clock test values

## 3.3 Rule A — Numeric

> *Correction noted in the source plan: this algorithm was printed under 3.4 Rule B in v1. It belongs here.*

**Input:** value_numeric, unit_normalized, ref_low, ref_high, patient age/sex, test_code

**Steps:**

1. Unit conversion to canonical unit (conversion table)
2. If no ref range → look up default range by test+age+sex → if still none → **FOLLOW_UP** (unclassifiable)
3. Compare: `value < ref_low` = LOW, `> ref_high` = HIGH, else NORMAL
4. If NORMAL → severity **NORMAL**
5. Look up `panic_thresholds` for test+age+sex
6. If `value ≤ critical_low` OR `value ≥ critical_high` → **CRITICAL**
7. Else if beyond range by > `slight_abnormal_factor` (**default 1.5×**) → **CRITICAL**
8. Else → **FOLLOW_UP**

**Also handle:**

- [ ] Censored values: `<0.01`, `>1000` — parse operator + number
- [ ] Delta checks (optional v2): large swing vs previous result on same patient

**Output:** `{severity, rule_id, reason_code, inputs_used}`
Always emit a machine-readable `reason_code`, e.g. `NUM_ABOVE_CRITICAL_HIGH`.

- [ ] Rule A implemented
- [ ] Censored value parsing
- [ ] reason_code emitted on every path

## 3.4 Rule B — Culture / sensitivity ★ highest clinical value

> *Correction noted in the source plan: this algorithm was printed under 3.5 Rule C in v1. It belongs here.*

**Input:** organisms[], sensitivities[], `encounter.discharge_medications[]`

**Steps:**

1. No growth / sterile → **NORMAL**, auto-close
2. Contaminant pattern (mixed flora, low colony count, common skin flora) → **FOLLOW_UP**, **never auto-close**. Skipping this floods doctors with noise and kills adoption.
3. For each discharge antibiotic:
   - map drug name → antibiotic_code (synonym table)
   - find sensitivity row for this organism
   - **R → CRITICAL** — the patient is at home on an ineffective drug
   - **I → FOLLOW_UP**
   - **S → covered**
4. No discharge antibiotic at all and organism is significant → **FOLLOW_UP**
5. All discharge antibiotics S → **NORMAL**, auto-close with logged reason
6. Multi-drug-resistant organism (MRSA, ESBL, CRE) → **CRITICAL regardless**

**Output:** `{severity, offending_drug, organism, alternatives_available[]}`

- [ ] Rule B implemented
- [ ] Contaminant handling verified (does not auto-close)
- [ ] MDRO override verified

## 3.5 Rule C — Narrative text

**Input:** narrative sections

**Steps:**

1. Sentence split (spaCy sentencizer or regex — **no ML model needed**)
2. Match `clinical_keywords` per sentence
3. For each hit, run negation check within the sentence scope
4. Negated → discard hit
5. Surviving hits → take max severity
6. No hits → **FOLLOW_UP** if radiology/pathology

**Output:** `{severity, matched_terms[], matched_sentences[]}`

> **Critical safety rule: narrative reports never auto-close. Worst case is FOLLOW_UP.**

- [ ] Rule C implemented
- [ ] Negation scope handling verified

## 3.6 Orchestrator

- [ ] `classify_result(result_id)` → picks rules **by content, not by report type** (a radiology report can contain numbers)
- [ ] Combine: overall severity = **max** of all rule outputs
- [ ] Write **`classifications`** — id, result_id, case_id, severity, rule_outputs JSONB, engine_version, classified_at
  - `engine_version` is **mandatory**. You must be able to explain a 6-month-old decision.
- [ ] Preliminary result → hold, set `stale_preliminary` timer (**default 48h**), do not alert unless severity is CRITICAL
- [ ] Amended / corrected result → reopen a closed case, increment `reopened_count`, re-notify with **distinct wording** so the doctor knows it is an amendment, not a duplicate

## 3.7 Manual entry UI

- [ ] Lab tech form: numeric panel entry (test / value / unit / range rows, add-row)
- [ ] Culture form: organism + dynamic sensitivity grid (antibiotic × S/I/R)
- [ ] Narrative form: section dropdown + textarea
- [ ] Preview panel showing **predicted severity before save** (builds trust)

## 3.8 Validation with a clinician ★

- [ ] Build a gold set: **100+ real anonymised results** spanning all three rule types
- [ ] Store as `tests/fixtures/gold_set.yaml` with expected severity
- [ ] Run as a pytest parametrised suite — **this becomes your regression net forever**
- [ ] Sit with a clinician, walk every disagreement, **tune thresholds, not code**
- [ ] Record final agreement rate in `docs/clinical-validation.md`

---

## ✅ EXIT GATE 3

- [ ] Gold set passes at **≥95% agreement** with the clinician
- [ ] Culture-vs-discharge-drug case demonstrably produces **CRITICAL**
- [ ] A negated sentence (*"no evidence of malignancy"*) does **not** produce a flag
- [ ] A contaminant culture produces **FOLLOW_UP**, not CRITICAL, and does **not** auto-close

---

**Cross-ref:** [architecture/03-rules-ownership-escalation.md](../architecture/03-rules-ownership-escalation.md) (Step 7)
