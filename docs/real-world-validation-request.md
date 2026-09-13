# Real-world validation — request package

> **For the project team to use when approaching hospital administration, the
> ethics / privacy team, lab services, and clinicians.**
>
> 🔴 **Nothing in this document has been approved, decided or collected.**
> No permission has been requested. No clinician has been approached. No
> report has been collected. No clinical validation has taken place.
>
> Every open item carries **`[TO BE APPROVED]`**, **`[TO BE DECIDED]`** or
> **`[TO BE COLLECTED]`**. None of them may be ticked by anyone in this
> repository — each needs a person outside it.

**Source documents.** This package restates requirements that already exist;
it does not create new ones. Where the two differ, the source wins.

- [`clinical-validation-protocol.md`](clinical-validation-protocol.md) — how Exit Gate 3 gets closed
- [`test-corpus-manifest.md`](test-corpus-manifest.md) — the de-identification standard and the corpus target
- [`build/phase-03-clinical-rule-engine.md`](build/phase-03-clinical-rule-engine.md) — §3.8 and Exit Gate 3
- [`build/phase-05-dashboard-audit-mvp.md`](build/phase-05-dashboard-audit-mvp.md) — Exit Gate 5
- [`clinical-validation.md`](clinical-validation.md) — the honest status record
- [`../PROGRESS.md`](../PROGRESS.md) — project status and the two hold records

---

## 1 · Hospital permission request

> **`[TO BE APPROVED]`** — a draft for the project owner to put on hospital
> letterhead and submit. It has not been submitted.

**To:** Hospital Administration / Institutional Ethics Committee
**From:** `[TO BE DECIDED]` — project owner name and designation
**Date:** `[TO BE DECIDED]`
**Subject:** Permission to use de-identified investigation reports for development and clinical validation of Result Guardian

### 1.1 What the system does

Result Guardian tracks investigations still outstanding when a patient is
discharged, so a result arriving after discharge is not lost. It holds the
case open, escalates through a defined ladder if nobody acknowledges it, and
records every action in a tamper-evident audit log.

The safety function — tracking, timers, escalation — is deterministic and does
not depend on AI. A separate **rule engine** grades each result as `normal`,
`follow_up` or `critical`.

**That rule engine has not been reviewed by any clinician.** That is the
reason for this request, and it is stated in the project's own status record:
*"🔴 NOT VALIDATED. No clinician has reviewed any part of this."*

### 1.2 What permission is sought — three distinct purposes

We request permission to use **200 or more real, de-identified investigation
reports**. The three purposes are listed separately **because they are
different activities and may not attract the same approval.**

| | Purpose | What actually happens | Status |
|---|---|---|---|
| **(a)** | **System development** | Software reads the documents. No clinician involved. | `[TO BE APPROVED]` |
| **(b)** | **Phase 3 clinical validation of the rule engine** | **A qualified clinician reads ~100 of these reports and records an independent medical judgement**, which becomes the benchmark the system's grading is measured against. | `[TO BE APPROVED]` |
| **(c)** | **Future Phase 6 document-ingestion validation** | Measuring whether the system can read real PDFs correctly, including scanned ones. Software only. | `[TO BE APPROVED]` |

> ### ⚠️ Explicit question — please answer directly
>
> **Does the permission you grant cover BOTH (a) system development AND
> (b) Phase 3 clinical validation — or only one of them?**
>
> We ask because (b) is categorically different: it involves **a clinician
> recording a medical judgement on real patient data**, and that judgement
> then serves as the safety benchmark for the system.
>
> **We are not assuming that permission for (a) implies permission for (b).**
> If the scope covers development only, please say so and we will treat
> clinical validation as a separate submission.
>
> **Scope granted:** `[TO BE DECIDED — hospital]`

---

## 2 · De-identification requirements

**The project already has a de-identification standard. This request reuses
it and does not invent a second one.** From
[`test-corpus-manifest.md`](test-corpus-manifest.md):

> De-identification must remove, at minimum: patient name, MRN, phone,
> address, date of birth, relatives' names, treating doctor's name, and any
> barcode or QR code that encodes an identifier. Dates may be shifted but must
> stay internally consistent.

### 2.1 Removed from every report before it leaves the hospital

| Removed | |
|---|---|
| Patient name | ✔ |
| MRN | ✔ |
| Phone number | ✔ |
| Address | ✔ |
| Date of birth | ✔ |
| Relatives' names | ✔ |
| Treating doctor's name | ✔ |
| Any barcode or QR code encoding an identifier | ✔ |

### 2.2 Additional commitments

| Commitment | Why |
|---|---|
| **Ages, not dates of birth** | The rule engine needs age for age-banded thresholds. It never needs a date of birth. |
| **Sequence identifiers only** — `R001`, `R002`… | They *"carry no meaning and cannot be reversed."* |
| **No hashed MRNs, accession numbers or encounter numbers** | *"Not even hashed — a hashed MRN is still a stable identifier and a small hospital's MRN space is brute-forceable."* |
| **The re-identification key remains with the hospital records team** | If a mapping from our sequence ID back to a patient is kept at all, it *"lives with the hospital's records team under their access control, not in this repository."* |
| **Reports stored outside git** | *"Store outside git, manifest in git."* The repository already blocks `*.pdf`, `corpus/`, `uploads/` and `data/`. **No exceptions are to be added.** |
| **Dates may be shifted, but consistently** | The interval between collection and reporting is clinically meaningful and is used for matching. |
| **Free-text fields reviewed before storage** | Prose fields are *"the easiest place to accidentally write a name."* |

**Hospital confirmation that this standard is acceptable:** `[TO BE APPROVED]`

---

## 3 · Required hospital inputs

| # | Input | From | Status |
|---|---|---|---|
| 3.1 | **Critical-value / panic-value list**, with a citable source reference and the date it took effect | Lab Services | `[TO BE COLLECTED]` |
| 3.2 | **List of every laboratory that sends this hospital reports** — not only the main one | Lab Services / Medical Records | `[TO BE COLLECTED]` |
| 3.3 | **Confirmation of the approved de-identification standard** (§2, or the hospital's own) | Privacy Officer / DPO | `[TO BE APPROVED]` |
| 3.4 | **Named person responsible for collection and de-identification** | Hospital / project owner | `[TO BE DECIDED]` |
| 3.5 | **Approved secure storage location and process** — outside the code repository, backed up, access-controlled | Hospital IT / project owner | `[TO BE APPROVED]` |

### On 3.1 — why the critical-value list matters

The system currently holds threshold rows explicitly marked
`source = 'DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE'`. They must be
replaced with the hospital's own values before any clinical use.

The source must be citable and specific — the protocol's example of the right
shape is *"AIIMS Rishikesh Lab SOP v3, §4.2, approved 2026-09"*, and it is
explicit that a URL to a textbook is **not** acceptable.

Superseded values are **retired, never deleted**: *"A decision made last week
must stay explicable against the threshold that was live last week."*

---

## 4 · Shared corpus target

**One approved collection serves every purpose.** The clinical-validation
subset is drawn from the same reports as the document-reading corpus. No
second collection is requested.

| | |
|---|---|
| **Count** | **200+** real anonymised reports |
| **Coverage** | *"every lab the hospital actually uses, not just the main one"* |

### 4.1 Categorisation — capture **both** schemes during collection

| Scheme | Axis | Values |
|---|---|---|
| **Corpus** | Capture | `native` (digital text) · `scanned` (needs OCR) |
| **Corpus** | Discipline | `lab` · `radiology` · `pathology` · `micro` |
| **Corpus** | Pages | `single` · `multi` |
| **Validation subset** | Rule | `A` (numeric) · `B` (culture) · `C` (narrative) · `mixed` |

⚠️ **Both schemes must be recorded at collection time.** They are different
classifications of the same report, and the manifest warns that retro-fitting
either one is *"painful"*.

### 4.2 Deliberate over-sampling

> *"Deliberately over-sample **scanned** and **micro**: scanned documents are
> where OCR confidence collapses, and culture/sensitivity reports drive Rule
> B, the highest-clinical-value rule in Phase 3."*

### 4.3 Proposed collection tracker — `corpus_id` only, no patient identifiers

`[TO BE COLLECTED]` — a tracker already exists with headers and **zero rows**.

| Column | Contents |
|---|---|
| `corpus_id` | Sequence identifier — the only key |
| `sha256` | Checksum of the de-identified file |
| `lab_name` | Issuing laboratory |
| `discipline` | `lab` · `radiology` · `pathology` · `micro` |
| `capture` | `native` · `scanned` |
| `pages` | `single` · `multi` |
| `report_status` | e.g. final / amended |
| `collected_on` | Date |
| `deidentified_by` | Named de-identifier (§3.4) |
| `deidentified_on` | Date |
| `notes` | **No identifiers** |

Review layer, for reports promoted into the validation subset:

| Column | Contents |
|---|---|
| `corpus_id` | Joins to the row above |
| `gold_set_id` | `R001`… |
| `rule` | `A` · `B` · `C` · `mixed` |
| `clinician_reviewed_on` | Per report — *"a 100-case review runs over several sittings"* |
| `reviewed_by` | Reviewer identity (§5.2) |
| `blinded` | Was the system's answer hidden? |
| `agreement` | Did clinician and engine match? |
| `resolution` | Where they disagreed (§5.3) |

---

## 5 · Phase 3 validation subset

### 5.1 Sampling target — minimum 100 reports

| Rule | Minimum | Why (protocol's wording) |
|---|---|---|
| **A — numeric** | **≥ 30** | *"the widest value space"* |
| **B — culture** | **≥ 40** | *"★ highest clinical value; over-sample it"* |
| **C — narrative** | **≥ 25** | *"negation is where a rule engine is weakest"* |
| **mixed** | **≥ 5** | *"real reports are often more than one rule"* |
| **Total** | **≥ 100** | *"the plan's own floor"* |

Status: `[TO BE COLLECTED]`

Report content must be *"exactly the shape the API accepts today"*, because
*"a real case must be gradeable by the exact code path a live result takes, or
the validation measures something the product does not do."*

### 5.2 Clinician blinded review

`[TO BE DECIDED]` — no clinician has been approached. This has the longest
lead time and nothing else depends on being done first.

**The review is blinded:** the clinician's severity is recorded **before** they
see the engine's, and whether this happened is recorded per report. *"An
unblinded review is still useful, but it is a different and weaker claim."*

The clinician answers in their own vocabulary, mapped to `normal` /
`follow_up` / `critical`. If a report *"does not map cleanly onto three
buckets, that is itself a finding"* — it is recorded, not forced.

**Reviewer identity — required for each reviewer:**

| Field | Status | Why |
|---|---|---|
| **Full name** | `[TO BE COLLECTED]` | *"An agreement rate attributed to nobody is not attributable."* |
| **Qualification** | `[TO BE COLLECTED]` | |
| **Specialty** | `[TO BE COLLECTED]` | *"A microbiologist validating Rule B and a radiologist validating Rule C are different claims."* |
| **Registration number** (MCI/NMC or state council) | `[TO BE COLLECTED]` | *"Makes the reviewer a real, checkable person."* |
| **Affiliation** | `[TO BE COLLECTED]` | *"Which hospital's practice these thresholds now reflect."* |

Identity is recorded **per run and per report**, because *"more than one
clinician may review different rules, and a single global name would
misattribute."*

**Review dates** are recorded per report and per run, alongside the engine
version the run scored — *"A validation is valid for the engine version and
configuration it was run against."*

### 5.3 Disagreement resolution vocabulary

> *"A disagreement is data, not a failure to be cleared."*
> *"Sit with a clinician, walk every disagreement, **tune thresholds, not code.**"*

| `resolution` | Meaning | What changes |
|---|---|---|
| `threshold_tuned` | The clinician is right and the configuration was wrong | A configuration row. **Never rule code.** |
| `clinician_revised` | On discussion the clinician changed their own answer | The clinician's severity, with the reason recorded and *"visible rather than silently overwritten"* |
| `accepted_gap` | Both answers defensible; the engine's is acceptable | Nothing — but it *"counts as a **disagreement** in the arithmetic"* |
| `rule_defect` | The engine is wrong in a way no threshold fixes | A code change, its own regression test, and a **full re-run** |

### 5.4 Agreement — ≥95% overall **and** per rule

The arithmetic is fixed **in advance**, *"before anyone knows what number they
produce"*:

- Denominator is **every case scored**, including ones the clinician found hard — *"Dropping hard cases is how a rate gets inflated."*
- **`accepted_gap` counts as a disagreement.** *"It is one. It is merely a tolerable one."*
- **Exact severity match.** No partial credit for *"critical vs follow_up is close"* — *"It is not close: one pages a doctor and one does not."*
- **`clinician_revised` cases stay in**, scored on the clinician's final answer, with the revision visible.
- **`threshold_tuned` cases are re-scored on a full re-run**, not marked agreed in place — *"Otherwise the tuning is being graded against the cases that motivated it."*
- **One rate overall, plus a rate per rule** — *"95% overall can hide 70% on Rule C."*

⚠️ **`rule_defect` invalidates the run.** Changing rule code changes answers to
cases already scored, so the rate *"must be recomputed from a full re-run —
not patched."* The engine version before and after is recorded.

**The ≥95% threshold is the build plan's and *"is not negotiable downward by
anyone in this repository."***

Status: `[TO BE DECIDED]` — no review has taken place and **no agreement rate
exists**.

---

## 6 · Phase 5 shadow-run planning

Exit Gate 5 requires *"**Two weeks of shadow running** in one ward with manual
result entry"* and *"**Zero missed cases**"*.

| # | Question | Status |
|---|---|---|
| 6.1 | **Which ward?** One ward only. Which, and who is its clinical lead? | `[TO BE DECIDED]` |
| 6.2 | **Which 14-day period?** A continuous fortnight — start and end dates | `[TO BE DECIDED]` |
| 6.3 | **Who performs manual result entry?** Named staff, shift coverage, and who covers absence | `[TO BE DECIDED]` |
| 6.4 | **How will independent ground truth for "zero missed cases" be established?** | `[TO BE DECIDED]` |
| 6.5 | **Who adjudicates a suspected miss?** A named person or panel, and the route when they disagree | `[TO BE DECIDED]` |

> ### ⚠️ 6.4 has no answer in this project's documentation
>
> The gate says *"zero missed cases"*, but **no measurement method is defined
> anywhere in the source documents.** The phase doc characterises the clause
> only as *"a claim about a fortnight of real patients."*
>
> This requires a hospital decision before the fortnight begins, covering at
> least: what counts as a miss; what the **independent** source of truth is —
> the system's own records cannot be the benchmark for its own misses; how
> reconciliation is performed and how often; and what evidence is retained.
>
> **No method is proposed here.** Inventing one and presenting it as the
> standard would be exactly the kind of unfounded claim this document exists
> to prevent.

**Clinician sign-off on flag quality** is a separate Exit Gate 5 clause, and
the phase doc records that it *"is the same ≥95% agreement requirement that
Phase 3's gate is still blocked on."* Status: `[TO BE DECIDED]`

---

## 7 · Decision checklist

| # | Item | Owner | Status |
|---|---|---|---|
| 1 | Does permission cover **both** development and clinical validation? | Administration / Ethics | `[TO BE DECIDED]` |
| 2 | Written permission to use de-identified reports | Administration / Ethics | `[TO BE APPROVED]` |
| 3 | De-identification standard accepted | Privacy Officer / DPO | `[TO BE APPROVED]` |
| 4 | Named collector / de-identifier | Hospital / project owner | `[TO BE DECIDED]` |
| 5 | Secure storage, backup and access-control process | Hospital IT / project owner | `[TO BE APPROVED]` |
| 6 | Critical-value / panic-value list, with source and effective date | Lab Services | `[TO BE COLLECTED]` |
| 7 | List of all report-sending laboratories | Lab Services / Medical Records | `[TO BE COLLECTED]` |
| 8 | Clinician(s) engaged, and which rule types each takes | Project owner / clinician | `[TO BE DECIDED]` |
| 9 | 200+ real anonymised reports, both categorisation schemes captured | Named de-identifier | `[TO BE COLLECTED]` |
| 10 | 100+ report validation subset meeting A/B/C/mixed minimums | Named de-identifier | `[TO BE COLLECTED]` |
| 11 | One sampling plan satisfying both over-sampling targets | Project owner + clinician | `[TO BE DECIDED]` |
| 12 | Ward, 14-day period and named entry staff | Ward clinical lead | `[TO BE DECIDED]` |
| 13 | Independent ground-truth method for "zero missed cases" | Hospital | `[TO BE DECIDED]` |
| 14 | Miss adjudication process and adjudicator | Hospital | `[TO BE DECIDED]` |

**Nothing in this table is complete.**

---

## 8 · Evidence checklist

What must exist before either gate can be considered:

| Evidence | Status |
|---|---|
| Written permission document, with its scope stated | `[TO BE COLLECTED]` |
| Confirmed de-identification standard | `[TO BE COLLECTED]` |
| Named de-identifier, recorded per report | `[TO BE COLLECTED]` |
| 200+ de-identified reports outside git, tracker populated | `[TO BE COLLECTED]` |
| 100+ validation cases linked by `corpus_id`, distribution met | `[TO BE COLLECTED]` |
| Per report: clinician severity, reviewer identity, review date, blinded flag, rationale where it disagrees | `[TO BE COLLECTED]` |
| Per run: reviewer, completion date, engine version | `[TO BE COLLECTED]` |
| Every disagreement with a `resolution` from the closed vocabulary | `[TO BE COLLECTED]` |
| Overall agreement rate **and** a rate per rule, from a full re-run | `[TO BE COLLECTED]` |
| Hospital critical-value list with citable source and effective date | `[TO BE COLLECTED]` |
| Shadow run: ward, dates, daily entries, flags, acknowledgements | `[TO BE COLLECTED]` |
| Miss reconciliation under the agreed independent method | `[TO BE COLLECTED]` |
| Named clinician's sign-off on flag quality | `[TO BE COLLECTED]` |

---

## 9 · What the project must NOT claim yet

- **Do NOT claim Phase 3 is clinically validated.** It is not. The status record says *"🔴 NOT VALIDATED. No clinician has reviewed any part of this."*
- **Do NOT quote any agreement percentage.** None exists. The current gold set holds **54 synthetic cases and 0 real anonymised results**, and a guard test fails if anything computes a proportion while the set is synthetic.
- **Do NOT present the synthetic gold set as clinical validation.** *"Every expectation in it was written by the same people who wrote the rules — which is exactly the circularity a clinician exists to break."*
- **Do NOT invent** reviewer names, review dates, clinical threshold sources, permissions, or an agreement rate.
- **Do NOT claim permission has been granted**, or that its scope covers clinical validation, until the hospital has answered §1.2 in writing.
- **Do NOT claim any reports have been collected.** The corpus stands at **0 of 200+**.
- **Do NOT claim Exit Gate 3 or Exit Gate 5 has passed.** Both are 🔴 OPEN.
- **Do NOT treat green tests, CI, or end-to-end runs as clinical evidence.** They demonstrate the software behaves as written; they say nothing about whether what was written is clinically right.
- **Do NOT use synthetic reports as gate evidence** — not for Phase 3, and not for the future document-ingestion gate. *"Generated PDFs do not reproduce the skew, stamps, bleed-through, bilingual headers or broken table borders that real labs emit."*
- **Do NOT infer zero missed cases** from an absence of complaints, or from the system's own records.
- **Do NOT weaken the honesty guards.** Two tests enforce this and *"are load-bearing, not decoration."*

---

## One note on sequencing

The permission in §1 is the long pole — it is recorded as having a multi-month
lead time — and it **unblocks two things at once**: the Phase 3 clinical
validation subset and the document-ingestion corpus are drawn from the same
approved collection. Engaging a clinician (§5.2) is the other long-lead item
and depends on nothing else being done first.

Both can be started immediately and in parallel. Everything else in this
document waits on them.
