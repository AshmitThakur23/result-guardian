# Phase 3 — Clinical Rule Engine (manual input)

**Goal:** correct severity classification, fully deterministic, **no AI**.
**Duration:** 2–3 weeks · **Node:** A only · **Depends on NODE B?** No

> Requires a clinician's time — **book it now.**

---

## 📍 STATUS SUMMARY — Phase 3

> ⚠️ **Do not start this phase until Exit Gate 2 passes.**

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 3.1 Result schema | A | ✅ done | migration `0007_rule_engine_schema`, applied, drift 0 |
| 3.2 Configuration tables ★ | A | ✅ done | tables built; seeded with **placeholders**, see 3.8 |
| 3.3 Rule A — Numeric | A | ✅ done | 45 tests |
| 3.4 Rule B — Culture / sensitivity ★ | A | ✅ done | 55 tests |
| 3.5 Rule C — Narrative | A | ✅ done | negation, hedging, sentence scope |
| 3.6 Orchestrator | A | ✅ done | + `classify` consumer; 25 tests |
| 3.7 Manual entry UI | A | ✅ done | 3 forms + preview panel; 16 vitest, 8 Playwright |
| 3.8 Clinician validation ★ needs a clinician | A | 🔴 **blocked** | **harness built, 51 synthetic cases pass. NO CLINICIAN HAS REVIEWED ANY OF IT.** [clinical-validation.md](../clinical-validation.md) |
| **Exit Gate 3** | A | 🔴 **OPEN** | 3 of 4 clauses proven; the ≥95% clinician agreement clause cannot be closed here |

---

## 3.1 Result schema

- [x] **`results`** — id, order_id, case_id, report_status (`preliminary|final|amended|corrected`), reported_at, received_at, source (`manual|pdf|hl7|fhir`), source_ref, raw_payload JSONB, superseded_by_result_id
- [x] **`result_analytes`** — id, result_id, seq, test_name_raw, loinc_code (null till Phase 7), value_raw, value_numeric, unit_raw, unit_normalized, ref_low, ref_high, ref_text, abnormal_flag_from_lab, source_page, source_bbox JSONB
- [x] **`result_organisms`** — id, result_id, organism_name, colony_count, specimen_type
- [x] **`result_sensitivities`** — id, organism_id, antibiotic_name, antibiotic_code, interpretation (`S|I|R`), mic_value
- [x] **`result_narratives`** — id, result_id, section (`impression|findings|conclusion|microscopy`), text, source_page, source_offset_start, source_offset_end

## 3.2 Configuration tables — never hardcode ★

> Per-hospital configurability is the difference between a product and a demo. **Thresholds and delays live in tables an admin can edit, never in code.**

- [x] **`panic_thresholds`** — id, test_code, loinc_code, sex, age_min_years, age_max_years, critical_low, critical_high, follow_up_low_multiplier, follow_up_high_multiplier, unit, source (e.g. "hospital SOP v3"), effective_from, effective_to
  - **Seed from your hospital's own critical value list, not from the internet.**
- [x] **`clinical_keywords`** — id, term, category (`malignancy|infection|acute|incidental`), severity (`critical|follow_up`), requires_negation_check, active
- [x] **`negation_patterns`** — id, pattern (regex), scope_words_before, scope_words_after
  - Must handle: *"no evidence of"*, *"negative for"*, *"ruled out"*, *"cannot exclude"*, *"unlikely"*, *"r/o"*
- [x] **`antibiotic_synonyms`** — brand → generic → ATC code. **Indian brands matter:** Augmentin, Monocef, Taxim, Zifi, Mox.
- [x] **`mdro_rules`** — organism + resistance pattern → auto-critical (MRSA, ESBL, CRE)
- [x] **`rule_config`** — key/value JSONB for tunables: `slight_abnormal_factor`, `preliminary_hold_hours`, `culture_contaminant_threshold`, and compressed-clock test values

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

- [x] Censored values: `<0.01`, `>1000` — parse operator + number
- 🚫 Delta checks — **out of scope**, marked *optional v2* by the plan itself. No previous-result comparison is built.

**Output:** `{severity, rule_id, reason_code, inputs_used}`
Always emit a machine-readable `reason_code`, e.g. `NUM_ABOVE_CRITICAL_HIGH`.

- [x] Rule A implemented
- [x] Censored value parsing
- [x] reason_code emitted on every path — asserted by `test_every_path_emits_a_reason_code`

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

- [x] Rule B implemented
- [x] Contaminant handling verified (does not auto-close) — gold `B05`, unit test, Playwright
- [x] MDRO override verified — by name and by panel; checked before the contaminant branch

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

- [x] Rule C implemented
- [x] Negation scope handling verified — per-pattern word scope, and a hedge outranks a denial (audit finding A1)

## 3.6 Orchestrator

- [x] `classify_result(result_id)` → picks rules **by content, not by report type** (a radiology report can contain numbers)
- [x] Combine: overall severity = **max** of all rule outputs
- [x] Write **`classifications`** — id, result_id, case_id, severity, rule_outputs JSONB, engine_version, classified_at
  - `engine_version` is **mandatory**. You must be able to explain a 6-month-old decision.
- [x] Preliminary result → hold, set `stale_preliminary` timer (**default 48h**), do not alert unless severity is CRITICAL
- [x] Amended / corrected result → reopen a closed case, increment `reopened_count`, re-notify with **distinct wording** so the doctor knows it is an amendment, not a duplicate

## 3.7 Manual entry UI

- [x] Lab tech form: numeric panel entry (test / value / unit / range rows, add-row)
- [x] Culture form: organism + dynamic sensitivity grid (antibiotic × S/I/R)
- [x] Narrative form: section dropdown + textarea
- [x] Preview panel showing **predicted severity before save** (builds trust) — writes nothing, proven by counting nine tables before and after

## 3.8 Validation with a clinician ★

- [ ] Build a gold set: **100+ real anonymised results** spanning all three rule types
- [x] Store as `tests/fixtures/gold_set.yaml` with expected severity — **54 synthetic cases, 0 real**
- [x] Run as a pytest parametrised suite — **this becomes your regression net forever** — `api/tests/test_gold_set.py`
- [ ] Sit with a clinician, walk every disagreement, **tune thresholds, not code**
- [ ] Record final agreement rate in `docs/clinical-validation.md`

---

## Deviations from the build plan, and why

| What | Why |
|---|---|
| **`unit_conversions` table added.** 3.2's schema list does not name it; Rule A step 1 says *"unit conversion to canonical unit (conversion table)"*. | The algorithm names a table the schema list forgot. Which unit a lab reports creatinine in is a per-hospital fact, so it belongs beside the rest of the configuration rather than as a dict in code. |
| **Panic bounds are checked *before* the reference-range test**, not at step 5 as written. | A value past a panic threshold is critical whether or not the lab attached a range, and a report with a missing range is exactly when that matters most. Following the printed order would classify a 7.5 potassium with no range as "unclassifiable → FOLLOW_UP" and bury it. |
| **MDRO (step 6) is checked before contaminant (step 2).** | A methicillin-resistant staphylococcus that looks like skin flora is still an infection-control event. The printed order would dismiss it as a contaminant and never reach step 6. |
| **A hedge downgrades rather than discards.** The plan lists *"cannot exclude"* and *"unlikely"* among the negation patterns. | *"Cannot exclude malignancy"* is not a negative finding; it is one somebody should look at. Treating it as a negation would silence the most uncertain reports. They are stored in `negation_patterns` with `is_hedge = true` and reduce CRITICAL to FOLLOW_UP instead of suppressing the hit. |
| **`reopened` is a transition, not a resting state.** 3.6 says an amendment reopens a closed case; it does not say where the case rests. | `reopened` lifts the closed guard so the amendment's own severity can move the case to `flagged` or `classified`. A case left sitting in `reopened` would carry no severity and nobody would triage it. The history is carried by `reopened_count`, the `case_reopened_for_amendment` event and the distinct notification template. |
| **An unmatched discharge antibiotic is reported (`CULT_DISCHARGE_DRUG_NOT_ON_PANEL`).** The algorithm's step 3 does not say what to do when the lookup finds nothing. | Silence there is indistinguishable from "covered", and that false negative is the exact failure Rule B exists to prevent. |
| **3.8 is a harness populated with synthetic cases, not 100+ real anonymised results.** | Real anonymised results and a clinician's time are both things this repository does not have. Padding to 100 with synthetic cases would satisfy a number and nothing else. The shortfall is recorded in [clinical-validation.md](../clinical-validation.md) and asserted by a test so it cannot quietly stop being mentioned. |

---

## 🔍 Audit findings — 2026-09-13

A full adversarial audit was run before finalisation. Six defects were found,
all by probing the running code rather than reading it. Every one is fixed,
and every fix has a regression test proven to fail when the fix is reverted
(`api/tests/test_phase_3_audit_findings.py`).

| # | Sev | Defect | Why it mattered |
|---|---|---|---|
| A1 | **P1** | `is_negated` returned on the first matching pattern, and the patterns came from an unordered `SELECT`. | *"Malignancy is unlikely; ruled out on prior imaging"* puts a hedge and a denial in scope of the same term. Row order decided which won — and one of them **discarded the finding entirely**. The same report could classify differently on different days, against the architecture doc's *"same input always gives same output"*. Fixed by collecting every in-scope pattern and letting a **hedge outrank a denial**: the reading that never silences a finding. |
| A2 | **P1** | `1.5e5` parsed as **1.5**; `>10⁵` parsed as **10**. | The same class as the pre-audit `1.5 x 10^5` bug, surviving in two more notations. Both put a heavy growth under the contaminant threshold, and **the contaminant branch returns before the resistance comparison** — so a culture resistant to the patient's discharge antibiotic would have been reported as likely contamination. Ranges now take the **upper** bound for the same reason. |
| A3 | **P1** | A rule that raised took the whole classification with it. | Intake has already superseded `result_due`, so a case whose classification fails permanently has **no pending timer and no flag** — measured: `pending timers AFTER the result arrives: 0`. The message retries five times and lands in the DLQ, safe but unread, and the case goes silent. THE ONE RULE: *degrade to the previous phase, not to silence.* Each rule now runs behind `_safely()`, which degrades a failure to FOLLOW_UP; **`DBAPIError` is re-raised** so genuine infrastructure faults still retry. |
| A4 | **P2** | A non-finite value made Rule A raise. | `Decimal('NaN') >= Decimal('6.5')` raises rather than returning False. Unreachable through the API today (Pydantic refuses non-finite), so this is the second lock, for a row that arrives by import or a future HL7 feed. |
| A5 | **P2** | `culture_no_growth_patterns` and `culture_contaminant_organisms` were read from `rule_config` and **never seeded**. | The code path was data-driven; the data was not there, so the engine always fell through to constants in `culture.py`. An admin could not change which organisms count as skin flora without editing Python — exactly what §3.2 forbids. Both are now seeded, and a test proves editing the row changes the verdict. |
| A6 | **P3** | Two MDRO rules can match one organism, and which `mdro_code` was recorded depended on row order. | Severity was never in doubt; the **stored explanation** was. *"You must be able to explain a 6-month-old decision"* rules that out. `ORDER BY code`. |

Also checked and found sound: idempotency under four concurrent workers on
independent connections (1 classification, 1 clinical event); three concurrent
engine versions (3 classifications, correctly); Rule C keyword matching
against case, punctuation, newlines, double spaces and multi-word terms; no
historical migration edited; no NODE B reference in any Phase 3 code path.

**Accepted, not a defect:** a low-count urine growth that is resistant to a
discharge antibiotic is reported FOLLOW_UP (contaminant), not CRITICAL,
because the plan puts contaminant detection at step 2 and the drug comparison
at step 3. It never auto-closes, so it still reaches a human. Whether that
ordering is right for a given hospital is a **threshold question for the
clinician walkthrough in 3.8**, not a code change.

---

## ✅ EXIT GATE 3

- [ ] 🔴 Gold set passes at **≥95% agreement** with the clinician
      — **no clinician has reviewed anything. There is no agreement rate.**
      See [clinical-validation.md](../clinical-validation.md).
- [x] ✅ Culture-vs-discharge-drug case demonstrably produces **CRITICAL**
      — gold case `B01`; `test_resistant_to_the_discharge_antibiotic_is_critical`;
      Playwright *"is predicted critical before saving and recorded critical after"*
      (real browser → real API → real worker → `classifications.severity = critical`)
- [x] ✅ A negated sentence (*"no evidence of malignancy"*) does **not** produce a flag
      — gold case `C01`; `test_a_negated_finding_does_not_flag`;
      Playwright *"does not produce a flag"*, with the unnegated twin proving the test bites
- [x] ✅ A contaminant culture produces **FOLLOW_UP**, not CRITICAL, and does **not** auto-close
      — gold case `B05`; `test_a_contaminant_is_follow_up_and_never_auto_closes`;
      Playwright *"is FOLLOW_UP, not CRITICAL, and does not auto-close"*

**Gate verdict: 🔴 OPEN.** Three clauses are mechanical and are proven. The fourth is
a claim about a clinician's judgement and no amount of engineering closes it.
**Phase 4 must not start on the strength of the three that passed.**

---

**Cross-ref:** [architecture/03-rules-ownership-escalation.md](../architecture/03-rules-ownership-escalation.md) (Step 7)
