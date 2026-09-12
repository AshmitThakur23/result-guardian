# Clinical validation protocol — how Exit Gate 3 actually gets closed

**Status: ⬜ NOT STARTED. This is the runbook, not a record of anything done.**
**Owner: human. Nothing here can be completed by writing code.**
**Companion to [`clinical-validation.md`](clinical-validation.md), which is the *status* record. This is the *procedure*.**

---

## What this document is for

Exit Gate 3 has one clause no engineering work can close:

> Gold set passes at **≥95% agreement** with the clinician

The gate has been open since the phase started. This document exists so that
when clinician time is finally booked, **nobody has to design the process on
the day** — the schema, the fields, the identity rules and the arithmetic are
all settled in advance, in writing, before anyone is under time pressure in a
room with a consultant.

It deliberately contains **no clinical policy**. It does not say what a
critical potassium is, which organisms are contaminants, or what severity any
report deserves. Every one of those is the clinician's to state, and this
document only says *where their answer gets written down*.

---

## ⚠️ The one design flaw this preparation found

The current fixture has a single field per case:

```yaml
expected_severity: critical
```

For the synthetic set that is fine — it is the engineering team's expectation
and nothing pretends otherwise. **For a real validation it is not usable**,
and the reason matters:

> If the clinician's judgement and the pass/fail expectation are the same
> field, then "fixing" a failing case by editing that field makes the
> agreement rate **100% by construction**. The number would be arithmetically
> correct and completely meaningless.

So a real validation run needs the clinician's answer and the engine's answer
recorded as **two separate facts**, with the disagreement between them
preserved rather than edited away. The schema below does that. **Implementing
it is a code change that has deliberately NOT been made** — it belongs to the
execution of 3.8, alongside the clinician, not to this preparation.

---

## 1 · Import schema for real anonymised results

One YAML entry per real result, in `api/tests/fixtures/gold_set.yaml` (or a
sibling file, see §10). Fields marked **NEW** do not exist yet.

```yaml
- id: R001                        # see §2 — never an MRN or accession number
  provenance: real_anonymised     # replaces `synthetic`; §10
  corpus_id: C-2026-0412          # NEW · link to the de-identified source, §2
  rule: B                         # A | B | C | mixed
  description: >-
    Free text, clinically meaningful, patient-identifier free.

  order:
    category: micro
    test_code: URC
    test_name: Urine Culture

  encounter:
    discharge_antibiotics: [Monocef]   # generic or brand; drives Rule B

  content:                        # exactly the shape the API accepts today
    organisms:
      - organism_name: Escherichia coli
        colony_count: ">100,000 CFU/mL"
        specimen_type: urine
        sensitivities:
          - { antibiotic_name: Ceftriaxone, interpretation: R }

  # ── the clinician's independent judgement ──────────────── NEW
  clinician:
    severity: critical            # normal | follow_up | critical
    reviewed_by: "Dr A. Menon, MD (Internal Medicine), MCI 12345"
    reviewed_on: 2026-10-02
    rationale: >-
      Free text, in the clinician's own words. Required when it disagrees
      with the engine; optional otherwise.
    blinded: true                 # §4 — was the engine's answer hidden?

  # ── what the engine said, recorded not asserted ────────── NEW
  engine:
    severity: critical            # filled in by the harness, never by hand
    reason_codes: [CULT_RESISTANT_TO_DISCHARGE_DRUG]
    engine_version: "3.0.0"
    classified_at: 2026-10-02T09:14:33Z

  # ── the outcome ────────────────────────────────────────── NEW
  agreement: true                 # computed: clinician.severity == engine.severity
  disagreement:                   # present only when agreement is false; §5
    resolution: threshold_tuned   # threshold_tuned | clinician_revised | accepted_gap | rule_defect
    changed: "panic_thresholds POTASSIUM critical_high 6.5 -> 6.0"
    note: >-
      Why, in the clinician's words.
```

`content` is **unchanged** from what the system accepts today — the same
`analytes` / `organisms` / `narratives` shape the manual-entry API and the
harness already use. That is deliberate: a real case must be gradeable by the
exact code path a live result takes, or the validation measures something the
product does not do.

**Coverage target** — the plan says *"spanning all three rule types"*:

| Rule | Minimum | Why |
|---|---|---|
| A — numeric | 30 | the widest value space |
| B — culture | 40 | ★ highest clinical value; over-sample it |
| C — narrative | 25 | negation is where a rule engine is weakest |
| mixed | 5 | real reports are often more than one rule |
| **Total** | **100+** | the plan's own floor |

---

## 2 · Case identity without patient identifiers

**This repo already has a de-identification standard. Reuse it, do not invent
a second one.** From [`test-corpus-manifest.md`](test-corpus-manifest.md):

> De-identification must remove, at minimum: patient name, MRN, phone,
> address, date of birth, relatives' names, treating doctor's name, and any
> barcode or QR code that encodes an identifier. Dates may be shifted but must
> stay internally consistent.

Applied to the gold set:

| Rule | Detail |
|---|---|
| **`id` is a sequence number** | `R001`, `R002`… It carries no meaning and cannot be reversed. |
| **Never an MRN, accession or encounter number** | Not even hashed — a hashed MRN is still a stable identifier and a small hospital's MRN space is brute-forceable. |
| **`corpus_id` links to the source** | The de-identified source document lives **outside git** under the Phase 1.6 corpus, keyed by `corpus_id`. Only the manifest is tracked. |
| **The re-identification key never enters git** | If a mapping from `corpus_id` back to the real patient is kept at all, it lives with the hospital's records team under their access control, not in this repository. |
| **Ages, not dates of birth** | Rule A needs age; it never needs a DOB. Record `age_years` in the case if a threshold is age-banded. |
| **No free-text leakage** | `description` and `rationale` are prose fields and are the easiest place to accidentally write a name. They are reviewed before commit. |

`.gitignore` already blocks `*.pdf`, `corpus/`, `uploads/` and `data/`.
**Do not add exceptions.**

---

## 3 · How the system's classification is recorded

Already built. No change needed.

Every classification writes a row to **`classifications`**:

| Column | What it gives the validation |
|---|---|
| `severity` | the engine's answer |
| `rule_outputs` (JSONB) | every rule that ran, its `reason_code` and `inputs_used` — the *why* |
| `engine_version` | **mandatory**; which engine produced it |
| `classified_at` | when |
| `result_id` | which result |

Unique on `(result_id, engine_version)`, so a re-run under the same version
cannot produce a second decision, and a new version records a genuinely new
one alongside the old. A six-month-old decision stays explicable, which is
exactly what a validation audit needs.

The harness copies `severity`, `reason_codes`, `engine_version` and
`classified_at` into the case's `engine:` block. **Written by the harness,
never typed by hand** — a hand-typed engine answer is not evidence.

---

## 4 · How the clinician's classification is recorded

In the `clinician:` block above. Three requirements:

1. **Independent.** The clinician's severity is recorded **before** they see
   the engine's. `blinded: true` records that this happened. An unblinded
   review is still useful, but it is a different and weaker claim, and the
   field makes which one happened visible rather than assumed.
2. **In their vocabulary, mapped to ours.** The clinician says what they would
   do; it is written as `normal` / `follow_up` / `critical`. If a case does
   not map cleanly onto three buckets, that is itself a finding — record it in
   `rationale` rather than forcing it.
3. **Attributed.** See §6.

---

## 5 · Disagreements and clinician reasoning

**A disagreement is data, not a failure to be cleared.** The plan is explicit
about what to do with one:

> Sit with a clinician, walk every disagreement, **tune thresholds, not code.**

Every disagreement gets a `disagreement:` block with a `resolution` from a
closed vocabulary:

| `resolution` | Meaning | What changes |
|---|---|---|
| `threshold_tuned` | The clinician is right and the configuration was wrong. | A row in `panic_thresholds` / `clinical_keywords` / `mdro_rules` / `rule_config`. **Never rule code.** |
| `clinician_revised` | On discussion the clinician changed their own answer. | `clinician.severity`, with the reason recorded. Honest, and it must be visible rather than silently overwritten. |
| `accepted_gap` | Both answers are defensible; the engine's is acceptable. | Nothing. Counts as a **disagreement** in the arithmetic. |
| `rule_defect` | The engine is wrong in a way no threshold fixes. | A code change, its own regression test, and a **re-run of the whole set**. |

⚠️ **`rule_defect` invalidates the run.** Changing rule code changes answers
to cases already scored, so the agreement rate must be recomputed from a full
re-run — not patched. Record the engine version before and after.

---

## 6 · Reviewer identity requirements

Minimum recorded per reviewer:

| Field | Why |
|---|---|
| **Full name** | An agreement rate attributed to nobody is not attributable. |
| **Qualification and specialty** | A microbiologist validating Rule B and a radiologist validating Rule C are different claims. |
| **Registration number** (MCI/NMC or state council) | Makes the reviewer a real, checkable person. |
| **Affiliation** | Which hospital's practice these thresholds now reflect. |

Recorded in two places: `meta.reviewed_by` for the run, and
`clinician.reviewed_by` per case — because **more than one clinician may
review different rules**, and a single global name would misattribute.

If the person also enters threshold rows, they should have a `users` row
(`role`: `doctor` or `unit_head`) so `panic_thresholds.created_by` points at
them. See §9.

---

## 7 · Review date requirements

| Field | Requirement |
|---|---|
| `clinician.reviewed_on` | Date that case was judged. Per case, because a 100-case review runs over several sittings. |
| `meta.reviewed_on` | Date the run was completed — the date quoted alongside the agreement rate. |
| `meta.engine_version` | The engine the run scored. A later version is a different engine and does not inherit the result. |
| `panic_thresholds.effective_from` | Already enforced NOT NULL. When each value came into force. |

**A validation is valid for the engine version and configuration it was run
against.** Both are recorded so that "95% agreement" is never quoted without
saying *of what*.

---

## 8 · How the ≥95% agreement is calculated

**No code computes this today, and that is deliberate** — the guard test
`test_no_agreement_rate_is_reported_for_synthetic_cases` parses the harness's
own AST and fails if anything produces a proportion while the set is
synthetic. That guard stays until real data replaces the synthetic set.

When it is genuinely earned, the arithmetic is:

```
agreement = cases where clinician.severity == engine.severity
rate      = agreement / total cases scored
```

with these rules fixed **now**, before anyone knows what number they produce:

| Rule | Why it is fixed in advance |
|---|---|
| **Denominator is every case scored**, including ones the clinician found hard. | Dropping hard cases is how a rate gets inflated. |
| **`accepted_gap` counts as a disagreement.** | It *is* one. It is merely a tolerable one. |
| **Exact severity match.** No partial credit for "critical vs follow_up is close". | It is not close: one pages a doctor and one does not. |
| **`clinician_revised` cases stay in**, scored on the clinician's final answer, with the revision visible. | Removing them hides the influence of discussion. |
| **Cases resolved by `threshold_tuned` are re-scored on a full re-run**, not marked agreed in place. | Otherwise the tuning is being graded against the cases that motivated it. |
| **One rate overall, plus a rate per rule.** | 95% overall can hide 70% on Rule C. The per-rule figure is the one that finds a weak rule. |

The gate asks for **≥95%**. That threshold is the build plan's and is not
negotiable downward by anyone in this repository.

**Unblocking the calculation requires, in order:** real cases replace
synthetic → `meta.clinician_validated: true` with a named reviewer and date →
the guard test's skip condition takes effect → an agreement function may then
be written, with its own tests.

---

## 9 · Panic-threshold source and provenance

**Already built.** `panic_thresholds` carries every column needed:

| Column | Use |
|---|---|
| `source` | **NOT NULL.** e.g. `"AIIMS Rishikesh Lab SOP v3, §4.2, approved 2026-09"`. Never a URL to a textbook. |
| `effective_from` / `effective_to` | When the value was in force. Rule A scopes its lookup by these, so an old decision is explicable against the threshold that actually applied. |
| `created_by` / `updated_by` | FK to `users` — who entered it. |
| `created_at` / `updated_at` | When. |
| `deleted_at` | Soft delete; a retired threshold is never destroyed. |

Every placeholder currently carries:

```
source = 'DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE'
```

so one `WHERE` finds every unreviewed row. **Replacement procedure:**

1. Insert the hospital's real values with a real `source` and
   `effective_from`, `created_by` set to the approving clinician's user row.
2. Set `effective_to = now()` on the placeholder rows — **do not delete
   them.** A decision made last week must stay explicable against the
   threshold that was live last week.
3. Confirm zero live placeholders:
   ```sql
   SELECT count(*) FROM panic_thresholds
    WHERE source = 'DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE'
      AND deleted_at IS NULL
      AND (effective_to IS NULL OR effective_to > now());
   ```

⚠️ **Gap:** `created_by` is nullable, so a threshold *can* be inserted with
nobody named. Making it NOT NULL for clinically-sourced rows would need a
migration and a decision about the seed script; it is recorded here as a known
gap rather than changed unilaterally.

The same applies to `clinical_keywords`, `negation_patterns`,
`antibiotic_synonyms` (local brand names matter) and `mdro_rules`, none of
which carry a `source` column — **a second known gap**, noted, not fixed.

---

## 10 · Distinguishing the validated set from the 54 synthetic cases

Three mechanisms, two already enforced by tests:

1. **Per case: `provenance`.** `synthetic` | `real_anonymised`. Already used
   and already asserted.
2. **Per file: `meta.clinician_validated`.** Already guarded —
   `test_the_gold_set_does_not_claim_clinician_validation` fails if it is
   flipped to `true` without a named reviewer, a date, **and** every case
   ceasing to be marked synthetic. Verified to fail when deliberately tripped.
3. **Recommended: two files.** Keep `gold_set.yaml` as the synthetic
   regression net and add `gold_set_clinical.yaml` for the validated set.

   The synthetic set is worth keeping *forever* — it is a fast regression net
   that needs no patient data and can run in CI on any machine. The clinical
   set is the evidence for the gate. Mixing them means either the regression
   net drags patient-derived data into every CI run, or the evidence gets
   diluted by cases nobody reviewed. **They answer different questions and
   should not share a file.**

   This is a recommendation, not a change — splitting the fixture is a code
   change belonging to 3.8's execution.

---

## What must happen, in order

| # | Step | Owner | Blocked on |
|---|---|---|---|
| 1 | Book the clinician | Project owner | **nothing — this is the long pole** |
| 2 | Permission to use de-identified results for validation | Hospital admin / ethics | shares blocker #1 with [1.6 corpus](test-corpus-manifest.md) |
| 3 | Obtain the hospital's critical-value list | Lab services | — |
| 4 | Export and de-identify 100+ results (§1, §2) | Named de-identifier | 2 |
| 5 | Implement the schema extension (§1) + split the fixture (§10) | Engineering | 4 |
| 6 | Blinded clinician review (§4) | Clinician | 1, 5 |
| 7 | Walk disagreements, tune thresholds (§5) | Clinician + engineering | 6 |
| 8 | Replace placeholder thresholds (§9) | Clinician + engineering | 3, 7 |
| 9 | Full re-run, compute agreement (§8) | Engineering | 8 |
| 10 | Record the result in [`clinical-validation.md`](clinical-validation.md) | Engineering | 9 |

**Steps 1–4 and 6–7 cannot be done by writing code.** Step 1 has the longest
lead time and nothing else depends on being done first.

---

## Related

- Status record: [`clinical-validation.md`](clinical-validation.md)
- De-identification standard: [`test-corpus-manifest.md`](test-corpus-manifest.md)
- Harness: [`../api/tests/test_gold_set.py`](../api/tests/test_gold_set.py)
- Fixture: [`../api/tests/fixtures/gold_set.yaml`](../api/tests/fixtures/gold_set.yaml)
- Phase doc: [`build/phase-03-clinical-rule-engine.md`](build/phase-03-clinical-rule-engine.md)
