# Outstanding through Phase 6 — the register to return to

> **Written 2026-09-14.** Everything still owed from Phases 0–6, in one place, so
> Phases 7 and 8 can be built without carrying the list in anyone's head.
>
> **Return to this after Phase 8.** Nothing here blocks Phase 7 or Phase 8.

---

## How to read this

Every row is **one of three things**, and they are not interchangeable:

| Kind | Meaning |
|---|---|
| 🔧 **Engineering** | Code or docs. An engineer can close it. Nothing is waiting on anyone else |
| 🧑‍⚕️ **Human input** | Needs a clinician, a lab, a hospital or a lawyer. **Code cannot close it, and no amount of engineering shortens it** |
| ⚖️ **Decision** | Needs the project owner. An engineer must not decide it alone |

**Nothing in here is a guess.** Every item names how it was found and what would
close it. Where a diagnosis was wrong, the wrong diagnosis is recorded too —
a register that only lists successes teaches nothing on the second reading.

---

## 🔴 P0 — the one thing that is actually broken

### 1 · 🔧 The timer/closure deadlock — CI is red because of this

| | |
|---|---|
| **Found** | CI run `34850577216`, 2026-09-14. **1 failed, 1048 passed** |
| **Test** | `tests/test_timer_lifecycle.py::test_closing_a_case_while_its_timer_fires_is_safe` |
| **Error** | `asyncpg.exceptions.DeadlockDetectedError`, on `mark_fired`'s `UPDATE sla_timers SET status='fired'` |
| **Severity** | **Real, not a test artefact.** Closing a case at the moment its SLA timer fires is an ordinary production event, and this is the exact Phase 2 path that exists to keep it safe |

**What has been ruled out** — record this so nobody repeats it:

- ❌ **The FK theory was wrong.** `sla_timers.case_id` references
  `pending_cases(id)`, so it looked like `close_case`'s `SELECT … FOR UPDATE` on
  the parent would conflict with the `FOR KEY SHARE` a child write needs. **A
  test written to catch that passed with the fix reverted** — Postgres *skips*
  the parent key check when the referencing column is not modified, and
  `mark_fired` never touches `case_id`. That test was **deleted**, not kept: one
  that cannot detect the defect reads as coverage and is worse than none.
- ❌ No trigger on `sla_timers` reaches `pending_cases` (checked `pg_trigger`).
- ❌ `claim_timer_for_firing` and `mark_fired` write **only** `sla_timers` — read
  in full, not skimmed.
- ❌ **Not reproducible locally in 25 consecutive runs.** It needs CI's timing.

**Already changed, and explicitly not claimed as the fix:** `close_case` now
takes `FOR NO KEY UPDATE`. That is the correct lock for a transaction that never
changes the row's key and it genuinely narrows the lock footprint — but the code
comment says plainly that it is **unverified as a fix for this**.

▶ **Next step:** reproduce with `deadlock_timeout` lowered and
`log_lock_waits = on`, so **Postgres prints the actual lock graph** instead of it
being inferred from outside. The cycle involves two transactions and two rows;
the server log will name both. Stop theorising and read the log.

---

## 🧑‍⚕️ P1 — human input. Start these early; they have the longest lead times

**These are the critical path for the whole project.** Three exit gates are held
open by them and **no engineering shortens any of it.** If one thing is started
before Phase 7, make it §2.

### 2 · 🧑‍⚕️ The lab's own critical value list — highest value, lowest effort

| | |
|---|---|
| **Blocks** | Phase 3 task 3.2 — `panic_thresholds` are seeded with round, made-up numbers tagged `DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE` |
| **Effort** | **One conversation.** The document already exists |
| **Why it exists** | **ISO 15189:2012 clause 5.8** — which NABL applies in India — plus CLIA and the Joint Commission each **require** a laboratory to define and approve its own critical value list |

**Ask for:** *"a copy of the lab's critical value / panic value list, and the
critical result notification SOP."*

⚠️ **Do not seed these from published literature.** Research on 2026-09-14
established there is **no national or international consensus list** — real
accredited labs differ by **15 mmol/L on sodium alone**, and MGH is documented
changing its own glucose threshold from 60 to 45 mg/dL. The spread *is* the
finding. The hospital's own approved list is both the correct clinical source
and the correct legal one. ▶ [`clinical-rule-research-findings.md`](clinical-rule-research-findings.md) §5B

### 3 · 🧑‍⚕️ A clinician, for Phase 3.8 — Exit Gate 3

| | |
|---|---|
| **Blocks** | **Exit Gate 3.** There is **no agreement rate**; 54 synthetic cases, 0 real |
| **Who** | Realistically a general physician or intensivist. Two, ideally — see §9 |

**Make it cheap for them:** they review pre-prepared cases at their own desk, and
the first session can be **30 cases, not 100** — enough to find systematic
disagreement before more is built on top.

▶ 14 specific questions are already written and waiting in
[`clinical-rule-research-findings.md`](clinical-rule-research-findings.md) §6 —
including whether malignancy should be `critical` (ACR says Category 3, *days*),
whether radiology and pathology should share one keyword table (CAP/ADASP 2012
says no), and whether `however` should terminate negation scope.

### 4 · 🧑‍⚕️ 200+ real de-identified reports — Exit Gates 1.6 and 6

| | |
|---|---|
| **Blocks** | **Exit Gate 6** (two of three clauses) and Phase 1.6 |
| **Status** | **0 of 200.** Nothing in this repository can satisfy it |

**Synthetic PDFs are unit-test fixtures and may never be counted toward this.**
The build plan says so directly and a test asserts it.

⚠️ Ask **after** §2 and §3 — §6 works out what anonymisation and approval
actually require, so the request can be specific and answerable.

### 5 · 🧑‍⚕️ Two weeks of ward shadow-running — Exit Gate 5

Plus clinician sign-off on flag quality. Calendar time; cannot be compressed.

⚠️ **Its "zero missed cases" clause cannot mean what it says** — see §8.

### 6 · 🧑‍⚕️ Ethics and data-protection clearance

- **Do NOT self-exempt.** Under ICMR 2017, **exemption is granted by the Ethics
  Committee, never claimed by the investigator.** All research is submitted,
  including work believed exempt.
- **The QI framing probably will not save you.** A gold set characterising
  sensitivity, intended for use beyond one unit, is *designed to produce
  generalizable knowledge* — which reads as research.
- ⚠️ **Confirm the hospital's IEC is DHR/NAITIK-registered** for Biomedical and
  Health Research — a separate regime from CDSCO trial ECs.
- 🔴 **The currently-binding regime is the stricter one.** The DPDP Rules 2025
  were notified 13 Nov 2025, but **the research exemption only commences
  ~13 May 2027**. Until then the **IT (SPDI) Rules 2011 remain in force**, and
  they expressly classify *"medical records and history"* as sensitive personal
  data — a category the DPDP Act does not have at all.
- 🔴 **There is no Indian statutory de-identification standard.** No Safe Harbor
  equivalent, no identifier list, no definition of "anonymisation". Any standard
  adopted — HIPAA Safe Harbor, ISO 25237, DICOM PS3.15 Annex E — is adopted
  **voluntarily as good practice**, and must never be described as compliance
  being discharged.

### 7 · 🧑‍⚕️ HIS/LIS vendor conversation — Phase 9

Opened, all questions unanswered. Gates Phase 9 entirely.

---

## ⚖️ P2 — decisions for the project owner

**An engineer must not make these alone.** Each needs an ADR.

### 8 · ⚖️ Two exit gates are stated in a way the data cannot support

**D1 — Exit Gate 3's "≥95 % agreement" cannot be demonstrated at n=100.**
With 100 cases and 95 agreements the 95 % Wilson CI is **[88.8 %, 97.9 %]** — an
observed 95 % is statistically indistinguishable from 89 %. You would need ~**99 %**
observed before the lower bound reached 95 %.

> ⚠️ **The ≥95 % level is the build plan's and is "not negotiable downward."
> Nothing here lowers it and nothing here may be used to lower it.** The ask is
> to restate the gate's **form** as a confidence bound — e.g. *"lower bound of
> the 95 % CI on critical-class sensitivity ≥ 90 %"* — which is a claim the data
> can actually support.

**D2 — Exit Gate 5's "zero missed cases" cannot mean zero.**
Hanley & Lippman-Hand, *JAMA* 1983 — the rule of three: **zero events in n trials
proves a 3/n upper bound, not zero.** At 60 tracked cases the honest statement is
*"miss rate below 5 % with 95 % confidence."* That clause needs its measurement
method rewritten.

**D3 — Size the gold set on the critical stratum.** Sensitivity rests on the
count of *truly-critical* cases, not on 100. **≥35 critical cases** are needed
for a ≥90 % sensitivity lower bound; 73 for ≥95 %. The per-rule minimums
(A≥30, B≥40, C≥25) are a **coverage** plan, not a **power** plan. Both are needed.

### 9 · ⚖️ One clinician is not defensible

A published multicentre ED study found clinician-vs-clinician agreement on
urgency was only **moderate (κ = 0.43)**. If the reference standard has that much
internal variance, *"95 % agreement with the clinician"* is measured against an
unquantified yardstick.

**Recommended:** two clinicians rate every case blinded to each other and to the
engine; a third adjudicates discordant cases only. **Report clinician-vs-clinician
agreement as a named result** — if it is below the engine's figure, that is the
ceiling any classifier could reach.

**Affordable fallback:** one rates all 100, a second rates a random ~30 %
subsample purely to estimate inter-rater agreement.

### 10 · ⚖️ Record the miss : false-alarm cost ratio

**No published minimum sensitivity for clinical alerting exists, and no published
false-positive rate at which clinicians disengage.** A 2026 JAMIA review of 22
systematic reviews found **only 1 defined alert fatigue operationally.**
**Inventing either number is forbidden.** State the ratio we are willing to own —
Pauker & Kassirer's threshold approach is the defensible route.

⚠️ The build plan's **"~15 % flag rate"** retune trigger is an internal figure
with **no published basis**. Keep it, but label it a project decision.

### 11 · ⚖️ Phase 8 generation has only 27 % headroom

Measured 2026-09-14: a realistic request took **21.9 s against a 30 s budget**,
**2,361 chars of reasoning for 222 chars of answer**. Four options are written
into [`build/phase-08-rag-explanation.md`](build/phase-08-rag-explanation.md) §8.4.
**Recommendation: benchmark a non-reasoning model, and make generation async
regardless.**

### 12 · ⚖️ Schema-shaped clinical questions

- **A third severity tier?** Both ACR and RCR use three; we have two.
- **Cancer as its own axis?** RCR 2022 treats it as orthogonal to severity.
- **Qualified terms?** `tension pneumothorax` ≠ `pneumothorax` is exactly where
  ACR splits Category 1 from Category 2, and our whole-word matcher cannot
  express it.
- **Ward-dependent thresholds?** Published precedent: platelets 20 general vs
  **10 on haematology**, inside one hospital.
- **Troponin keyed to (analyser, assay, sex)?** A universal troponin threshold is
  **wrong by construction**.
- **Creatinine on a delta, not a level?** AKI is a change from baseline.

---

## 🔧 P3 — engineering, closable without anyone else

### 13 · 🔧 D-N1: negation scope has no conjunction termination

**Proven by execution**, not asserted: `test_a_conjunction_terminates_negation_scope`
reports **XFAIL** on *"No evidence of fracture, however a large abscess in the
liver."* — the abscess is suppressed. Marked `xfail(strict=True)`, so **the day
it is fixed the test fails** and forces the marker's removal.

**The fix is deliberately withheld** — adding `CONJ` terminators changes clinical
behaviour, so it belongs to the §3 clinician review (question 9).

✅ The **FOLLOW_UP floor** is locked by a passing test and must never be weakened:
NegEx's published precision is ~84.5 %, so roughly one in seven "negated" findings
is actually asserted, and Rule C discards negated hits. **That floor is the only
thing between that and a missed result.**

### 14 · 🔧 Six seeded keywords have no published anchor

`septic` · `obstruction` · `consolidation` · `lesion` · `effusion` · `abscess`
— not found on any retrieved ACR, RCR or institutional list. And ~20 terms *on*
published Category 1 lists are **missing**, headed by **aortic dissection**,
spinal cord compression, ectopic pregnancy, torsion and tube malposition.

⚠️ **Do not edit the keyword table to match.** It goes to §3 as a question.
▶ The RCR's 43-condition national list is transcribed in
[`clinical-rule-research-findings.md`](clinical-rule-research-findings.md) §1.

### 15 · 🔧 Two documents are confirmed unobtainable

- **Larson 2014** (the ACR Category 1/2/3 table) — `jacr.org` returns **HTTP 403**
  on fulltext, abstract and PDF; Europe PMC confirms `isOpenAccess: "N"`.
  ⚠️ **The Category table in the research doc is `[SEARCH]` reconstruction and
  must not be treated as sourced.**
- **Fleischner 2017** — `inPMC: "N"`, no OA mirror. Tables obtained second-hand
  via NCBI Bookshelf; footnotes and mm³ equivalents still missing.
- Also unread: **CAP Q-Probes** (Wagar 2007, Howanitz 2002) — the only large
  multi-lab threshold tables identified — and the **ACR ED actionable incidental
  findings** white paper (JACR 2023).

**Needs institutional access or a purchased copy.** Not an engineering problem.

### 16 · 🔧 Smaller engineering debts

| | Item |
|---|---|
| 🔧 | **`testcontainers` is declared in `pyproject.toml` and unused.** CI builds the real `infra/postgres` image instead — strictly better. Either remove the dependency or record the deviation |
| 🔧 | **Phase 5.1's SAML/OIDC against hospital AD** — 🚫 explicitly out of scope for the MVP; listed so it is not rediscovered |
| 🔧 | **Frontend bundle is 527 kB** (152 kB gzipped) with no code splitting. Fine on a LAN; worth revisiting before a hospital rollout |
| 🔧 | **`bg-red-700`-style raw classes may creep back.** The token migration covered 618 uses; a lint rule banning raw palette classes in `src/` would keep it that way |
| 🔧 | **`/api/patients` 422s without a query.** Correct behaviour, but the register notes it so nobody "fixes" it into returning everything |

---

## ✅ What is genuinely finished — so it is not re-litigated

| | |
|---|---|
| **Exit Gate 0** | ✅ **PASSED.** All four clauses measured. **RULE 2 proven by the deliberate test** — Ollama stopped with both machines on one LAN: health stayed 200, only `llm_generation` degraded, whole suite green, **SLA timers and classification kept firing**. Recovery automatic in ~6 s |
| **Exit Gate 1** | ✅ PASSED |
| **Exit Gate 2** | ✅ PASSED — chaos test: 50 cases, 2 restarts, exactly 50 flags |
| **Exit Gate 4** | ✅ PASSED |
| Phases 1, 2, 4, 5, 6 code | ✅ Built, audited, run end to end |
| Cross-node link | ✅ `172.25.52.148` ⇄ `172.25.54.48`, 32–36 ms, proven **from inside the api container** |
| Admin kill switch | ✅ **Fixed** — it wrote to the table and changed nothing. 3 regression tests, proven red-then-green |
| Phase 6 E2E | ✅ 5 specs, span invariant checked in SQL, corrupt-file fallback asserted |
| Design system | ✅ Semantic tokens, dark mode, 618 raw classes migrated |
| Test counts | ✅ 187 frontend · 42 Playwright · 1048/1049 backend |

---

## 📌 The working rule this register exists to enforce

> **CI is the gate, not a local test run.**
>
> On 2026-09-14 eight commits were pushed while CI was red. Everything passed
> locally the entire time — because `ruff` was being run over `app worker tests`
> while **CI runs it over all of `api/`**. A narrower check than the gate's is
> not a check.
>
> **Verify the gate the way the gate runs, and look at the gate after pushing.**

---

## Related

- [`PROGRESS.md`](../PROGRESS.md) — session log and phase status
- [`clinical-validation.md`](clinical-validation.md) — **authoritative on Exit Gate 3**
- [`clinical-validation-protocol.md`](clinical-validation-protocol.md) — how that gate closes
- [`clinical-rule-research-findings.md`](clinical-rule-research-findings.md) — the literature, with `[FETCHED]`/`[SEARCH]` tags
- [`build/phase-06-verification-log.md`](build/phase-06-verification-log.md) — Phase 6's 18 defects
- [`build/phase-08-rag-explanation.md`](build/phase-08-rag-explanation.md) §8.4 — the generation budget
