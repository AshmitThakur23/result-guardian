# Published-literature findings on the Phase 3 rule engine

> **Researched 2026-09-14.** Deep literature search on the published basis for our
> narrative keyword table and our negation detection.

---

## ⛔ READ THIS BEFORE USING ANYTHING BELOW

**This document is NOT clinical validation, and nothing in it closes Exit Gate 3.**

Exit Gate 3 requires *"gold set passes at ≥95% agreement with the clinician"* over
100+ **real anonymised results**. That is a measurement of a clinician's judgement
on this hospital's own cases. **No literature search can produce it.** See
[clinical-validation.md](clinical-validation.md), which stays authoritative on the
gate, and whose statement that **no clinician has reviewed anything** is unchanged
by this document.

What this research *does* do is move our seeded values from **made-up** toward
**published-and-cited**. A clinician still has to move them from
*published-and-cited* to *approved-for-this-hospital*. Two different steps.

**No rule, threshold, severity or keyword was changed as a result of this research,**
per the standing constraint in [`CLAUDE.md`](../CLAUDE.md) that Phase 3's rules,
thresholds and honesty guards are not to be modified. Everything below is recorded
as **evidence and open questions for the clinician review**, never as a licence to
silently re-grade a severity.

### Confidence tags — they are not decoration

| Tag | Means |
|---|---|
| `[FETCHED]` | The document at that exact URL was retrieved and read |
| `[SEARCH]` | **Second-hand.** Reconstructed from search-result summaries; the page was NOT opened |
| `NOT RETRIEVED` | Could not be obtained. **Do not cite it.** |

⚠️ **`WebFetch` was blocked by a permission hook for most domains during this
research.** The ACR and RCR category content below is therefore largely `[SEARCH]`.
Treat every `[SEARCH]` line as *"a real source exists — go read it"*, **not** as
*"this is what the source says."* The negation section (§4) is mostly `[FETCHED]`
and is the strongest evidence here.

---

---

## 0 · 🔴 Decisions this research raises — for the project owner, NOT for an engineer

**None of these have been acted on. Each needs an ADR and the owner's decision.**

| # | Decision | Why it cannot be made unilaterally |
|---|---|---|
| **D1** | **Reformulate Exit Gate 3 as a confidence bound**, not a point estimate | *"≥95 % agreement"* over 100 cases **cannot be demonstrated** — the 95 % CI is [88.8 %, 97.9 %]. **The ≥95 % figure is the build plan's and is "not negotiable downward."** This asks to change its *form*, never its *level*. §5D |
| **D2** | **Rewrite Exit Gate 5's "zero missed cases" measurement method** | The rule of three: zero misses in n cases proves a **3/n upper bound**, not zero. At 60 cases the honest claim is *"below 5 %,"* not *"zero."* §5D |
| **D3** | **Size the gold set on the critical stratum** — target **≥35 truly-critical cases** | Our A≥30 / B≥40 / C≥25 minimums are a *coverage* plan, not a *power* plan. §5D |
| **D4** | **Two blinded raters + an adjudicator**, and report clinician-vs-clinician agreement | One rater makes the reference standard unauditable, and clinicians measurably disagree (κ=0.43 in a published ED study). §5E |
| **D5** | **Record the miss : false-alarm cost ratio** as an explicit decision | **No published sensitivity minimum or alert-fatigue ceiling exists.** Inventing one is forbidden; stating our own is the defensible route. §5G |
| **D6** | **Ask the lab for their ISO 15189 critical value list** | Replaces every `panic_thresholds` placeholder at once, and they are obliged to have it. §5B |
| **D7** | **Confirm IEC registration and submit for review — do not self-exempt** | Exemption is granted by the EC, never claimed. The QI framing likely fails the generalizability test. §5H |
| **D8** | **Add DICOM de-identification to the protocol** | Currently absent. Burned-in pixel text survives every header scrub. §5H |

---

## 1 · The published frameworks we are not using

### ACR — Larson PA, Berland LL, Griffith B, Kahn CE Jr, Liebscher LA

*"Actionable findings and the role of IT support: report of the ACR Actionable
Reporting Work Group."* **J Am Coll Radiol. 2014;11(6):552–558.**
doi:10.1016/j.jacr.2013.12.016 · PMID 24485759
<https://www.jacr.org/article/S1546-1440(13)00840-5/fulltext> `[SEARCH]`

A **three-tier system keyed to time-to-communication**:

| Cat | Timeframe | Named example findings retrieved |
|---|---|---|
| **1** | **minutes** | ectopic pregnancy; intracranial haemorrhage; PE **when unstable, central and/or extensive**; ruptured/leaking aortic aneurysm; severe spinal cord compression; significant misplacement of tubes or catheters; **tension** pneumothorax; testicular/ovarian torsion; unexplained pneumoperitoneum; unstable spine fracture |
| **2** | **hours** | pneumothorax **without** tension; PE that is haemodynamically stable and peripheral; bone lesions at risk of pathologic fracture; intra-abdominal infections such as appendicitis or cholecystitis |
| **3** | **days** | cirrhosis; **probable malignancy in any location without acute danger to the patient**; haemodynamically significant arterial stenosis without acute symptoms |

The work group **explicitly states definitive lists are not possible** — these
"apply in most general hospital settings."

See also **Kuhn KJ, Larson DB et al., *"Critical Results in Radiology: Defined by
Clinical Judgment or by a List?"* JACR 2021;18(2):294–297**, PMID 32783896 `[SEARCH]`
— argues a list "is not meant to be definitive" and **its length should be carefully
considered.** Directly relevant to our 18-term table.

### RCR (UK) — a *different* system. Do not merge it with ACR.

Lineage: BFCR(12)11 → BFCR(16)4 → **RCR/AoMRC, *Alerts and notification of imaging
reports: Recommendations*, October 2022** `[SEARCH]`
<https://www.rcr.ac.uk/media/44sfqlbi/rcr-publications_alerts-and-notification-of-imaging-reports-recommendations_october-2022.pdf>
· Journal version Clin Radiol 2022, PMID 36564264

- **Critical** — requires *immediate emergency action*. Worked example: **tension pneumothorax**
- **Urgent** — evaluation *within 24 hours*. Worked example: **pulmonary embolism**
- **Unexpected significant** — unexpected, radiologist judges it significant. Example: apical lung cancer on a shoulder radiograph
- **New in 2022 — a separate CANCER pathway.** New cancer or new recurrence is flagged on its own axis; a CRITICAL alert may be raised alone *or in combination with* a CANCER alert

### 🔴 ACR and RCR disagree, and it matters to our schema

| Finding | ACR (2014) | RCR (2022) |
|---|---|---|
| Pulmonary embolism | **Cat 1** if unstable/central/extensive; **Cat 2** if stable & peripheral | **Urgent (24 h)** — the worked example of *urgent*, **not** critical |
| Pneumothorax | **Cat 1** only if tension; else **Cat 2** | **Critical** only if tension |
| Malignancy | **Cat 3 (days)** | **Its own CANCER pathway**, orthogonal to critical/urgent |

### Pathology has its own published framework — and it says not to do what we do

**Nakhleh RE, Myers JL, Allen TC, et al.** *"Consensus Statement on Effective
Communication of Urgent Diagnoses and Significant, Unexpected Diagnoses in Surgical
Pathology and Cytopathology From the College of American Pathologists and
Association of Directors of Anatomic and Surgical Pathology."*
**Arch Pathol Lab Med. 2012;136(2):148–154.** doi:10.5858/arpa.2011-0400-SA ·
PMID 21992705 `[SEARCH]`
<https://meridian.allenpress.com/aplm/article/136/2/148/64793/>

- CAP/ADASP **deliberately abandoned the term "critical value" for anatomic
  pathology** — "relatively few diagnoses in anatomic pathology are truly critical,"
  because *critical* implies minutes and AP results take hours to days.
- Two categories instead: **"urgent diagnosis"** and **"significant, unexpected
  diagnosis."**
- The consensus states each institution's AP policy **should be separate from the
  clinical-pathology critical-value policy, with a different expected timeframe.**

**We apply one keyword table with one severity axis to both radiology and
pathology** (`UNCERTAIN_CATEGORIES = ("radiology", "pathology")` in
[`api/app/rules/narrative.py`](../api/app/rules/narrative.py)). This is published
evidence that the *shape* is wrong, not merely the numbers.

---

## 2 · Audit of our 18 seeded keywords

Source of our table: [`api/scripts/seed_rules_dev.py`](../api/scripts/seed_rules_dev.py) lines 74–93.

| Term | Ours | Published position | Verdict |
|---|---|---|---|
| malignancy | critical | ACR **Cat 3 (days)**; RCR CANCER pathway | **Severity contradicted** |
| carcinoma | critical | as above | **Severity contradicted** |
| metastasis | critical | as above | **Severity contradicted** |
| metastases | critical | as above | **Severity contradicted** |
| neoplasm | follow_up | as above — *follow_up* is the better fit | ✅ Supported |
| "suspicious for malignancy" | critical | ACR Cat 3 | **Severity contradicted** |
| abscess | critical | nearest anchor is ACR **Cat 2** (intra-abdominal infections, *hours*). Term itself not named on any retrieved list | ⚠️ Unsourced; likely over-graded |
| septic | critical | **NOT FOUND** on any retrieved list | ⚠️ **Unsourced** |
| perforation | critical | ACR Cat 1 names **"unexplained pneumoperitoneum"**; institutional lists name **"free air"** | ⚠️ Concept supported, **our term is not the published one** |
| haemorrhage | critical | ACR Cat 1 names **"intracranial** haemorrhage" | ⚠️ Bare term is broader than any published entry |
| hemorrhage | critical | as above | as above |
| pulmonary embolism | critical | ACR: Cat 1 **only if** unstable/central/extensive. RCR: **Urgent**, not critical | ⚠️ `critical` is ACR's worst case only; disagrees with RCR |
| pneumothorax | critical | **tension** → Cat 1; non-tension → Cat 2 | ⚠️ Over-graded for the bare term |
| obstruction | follow_up | **NOT FOUND** | ⚠️ **Unsourced** |
| consolidation | follow_up | **NOT FOUND** | ⚠️ **Unsourced** |
| nodule | follow_up | Direction consistent with ACR Cat 3 and Lung-RADS 3 (1–2 % malignancy risk). **Fleischner NOT RETRIEVED** | ⚠️ Direction supported, **size thresholds uncited** |
| lesion | follow_up | only qualified form found is ACR Cat 2 "bone lesions at risk of pathologic fracture". Bare term **NOT FOUND** | ⚠️ **Unsourced** |
| effusion | follow_up | **NOT FOUND** | ⚠️ **Unsourced** |

**Scoreboard: 7 of 18 have a published anchor. 6 have none** — `septic`,
`obstruction`, `consolidation`, `lesion`, `effusion`, `abscess`. And on the
malignancy family, **our severity is more aggressive than the only guideline that
addresses it.**

⚠️ Over-flagging malignancy is the **safe** error. This is an alert-fatigue
question, not a patient-safety one. It is listed here as a clinician question, not
as a defect to fix unilaterally.

### Terms published lists name that we do not have

**ACR Category 1:** ectopic pregnancy · **aortic dissection** · ruptured/leaking
aortic aneurysm · severe spinal cord compression · significant misplacement of
tubes or catheters · **tension** pneumothorax (as distinct from pneumothorax) ·
unexplained pneumoperitoneum / free air · testicular or ovarian torsion ·
unstable spine fracture

**ACR Category 2:** appendicitis · cholecystitis · bone lesion at risk of
pathologic fracture

**ACR Category 3:** cirrhosis · haemodynamically significant arterial stenosis

**From institutional lists** (weaker evidence, but published and retrievable):
portal venous gas · ischaemic bowel · retained foreign body · malpositioned line
or tube · vascular disruption
<https://medicine.yale.edu/radiology-biomedical-imaging/quality-safety/critical-result-guidelines/>
· <https://www.uab.edu/medicine/radiology/images/Policies__Procedures/Policy_for_Communication_of_Critical_Findings.pdf>

---

## 3 · 🔴 Schema-level finding — bigger than any keyword

**We have one severity axis (`critical | follow_up`). Neither published system
maps onto it.**

- ACR is **3-tier by time-to-decision** (minutes / hours / days)
- RCR is **3-tier by urgency PLUS an independent cancer axis**
- CAP/ADASP says **pathology needs a different framework from radiology**

**We also cannot express a qualified term.** `_term_pattern()` is a whole-word
matcher, so `pneumothorax` fires at `critical` whether or not the report says
*tension*. But **tension vs not**, **intracranial vs not**, and **central/extensive
PE vs peripheral** are precisely the distinctions on which ACR splits Category 1
from Category 2. Our matcher cannot represent the distinction that the published
guidance is built on.

---

## 4 · Negation detection — the strongest evidence here, and a safety finding

### The algorithms

**NegEx** — Chapman WW, Bridewell W, Hanbury P, Cooper GF, Buchanan BG. *"A Simple
Algorithm for Identifying Negated Findings and Diseases in Discharge Summaries."*
**J Biomed Inform 2001;34(5):301–310.**
<https://www.sciencedirect.com/science/article/pii/S1532046401910299> —
**paywalled (HTTP 403), primary paper NOT READ.**

Reported performance `[SEARCH]`, corroborated independently by Ou & Patrick 2015:
**specificity 94.5 % · PPV 84.5 % · sensitivity 77.8 %.** **NPV NOT RETRIEVED — do
not cite one.**

**Trigger classes — read from the real published trigger file** `[FETCHED]`
<https://github.com/chapmanbe/negex/blob/master/negex.python/negex_triggers.txt>

| Tag | Meaning | Real examples |
|---|---|---|
| `PSEU` | pseudo-negation | `no increase`, `no change`, `no interval change`, `not cause` |
| `PREN` | pre-negation | `absence of`, `denies`, `free of`, `negative for`, `no evidence`, `no sign of`, `not demonstrate`, `fails to reveal` |
| `POST` | post-negation | `unlikely`, `was ruled out`, `have been ruled out` |
| `PREP` | pre-possible | `rule out`, `r/o` |
| `POSP` | post-possible | `not ruled out`, `did not rule out` |
| **`CONJ`** | **terminates scope** | **`but`, `however`, `nevertheless`, `yet`, `though`, `although`, `secondary to`, `cause of`, `source of`, `reason for`** |

**ConText** — Harkema H, Dowling JN, Thornblade T, Chapman WW. **J Biomed Inform
2009;42(5):839–851.** PMID 19435614 · PMCID PMC2757457 `[FETCHED]`
<https://pmc.ncbi.nlm.nih.gov/articles/PMC2757457/>

Negation performance, 120 reports / 2,277 conditions:

| Report type | Recall | Precision | F |
|---|---|---|---|
| Emergency department | .93 | .96 | **.95** |
| **Radiology** | .86 | 1.00 | **.93** |
| Discharge summaries | .89 | .84 | .86 |
| **Surgical pathology** | .75 | .75 | **.75** |

⚠️ **The pathology row rests on 4 negated conditions.** Quote it as "4 instances,"
never as "ConText's pathology performance." Independently consistent with **Ou Y,
Patrick J, *"Automatic negation detection in narrative pathology reports,"* Artif
Intell Med 2015;64(1):41–50** `[SEARCH]`, which found NegEx applied to pathology
without customisation performs worse.

**NegBio** — Peng Y et al., AMIA Informatics Summit 2018, arXiv:1712.05898
`[FETCHED]` — scope by **universal-dependency subgraph matching**, not a token
window. **+9.5 % precision, +5.1 % F1** over NegEx.

### ⚠️ The window rule — three sources disagree

1. **Six tokens** — Harkema 2009 (Chapman senior author) `[FETCHED]`
2. **Five tokens** — Wu et al. 2014 `[FETCHED]`
3. **No token window at all** — the shipped `GenNegEx.java` `[FETCHED]` comments
   *"Sentence boundaries serve as WINDOW for negation (suggested by Wendy Chapman)"*
   and terminates on the first `CONJ`/`PSEU`/opposing trigger. **There is no token
   counter in the real code.**

**Our seeded windows of 3 / 5 / 6 words have no citation behind them.** "6" matches
the 2001 description *as relayed by* the 2009 paper — second-hand, and contradicted
by the shipped implementation. **Record them as an engineering choice, not a
published value.**

### 🔴 The safety finding: our error asymmetry runs the wrong way

| Error | Consequence for us | Risk |
|---|---|---|
| Missed negation (~22 %, from sens. 77.8 %) | term fires anyway → false alarm | **Safe** |
| **False negation (~15 %, from PPV 84.5 %)** | an **asserted** finding is called negated → **hit discarded** | **A critical finding is suppressed** |

**Negation *precision* is the safety-critical metric here, not sensitivity** — and
84.5 % is not good enough for negation to act as an unguarded suppression gate.

**What makes this survivable is the FOLLOW_UP floor.** `narrative.py` discards
negated hits, but a report whose hits were all negated returns
`NARR_ALL_HITS_NEGATED` **at FOLLOW_UP**, so it still reaches a human.

> **The FOLLOW_UP floor for radiology and pathology is therefore load-bearing
> safety, not a nicety. It is what stops a negation false-positive becoming a
> missed critical result. Do not remove it, and do not let a later phase route
> around it.** (This is an inference from the published numbers, not a claim any
> paper makes about this system.)

### Published failure modes

**Wu S, Miller T, Masanz J, et al. *"Negation's Not Solved: Generalizability Versus
Optimizability in Clinical NLP."* PLoS ONE 2014;9(11):e112774** `[FETCHED]`
<https://pmc.ncbi.nlm.nih.gov/articles/PMC4231086/>

- **Scope over lists — our exact shape.** Worked example: *"no evidence of coughing,
  rales, or wheezing"* → **"wheezing" falls outside the window and is NOT marked
  negated.** Ours: `No evidence of metastasis, recurrence, or malignancy`.
- **Domain brittleness.** NegEx/YTEX F1 **95.3 %** on its own test set → 82.1 %
  (i2b2) → 71.3 % (MiPACQ) → **62.3 % (SHARP)**. A 33-point drop with no algorithm
  change. *"Practical negation detection is not reliable without in-domain training
  data."*
- **Morphological negation is invisible** to trigger methods — their example is
  **"afebrile"**. Radiology analogues `unremarkable`, `patent`, `intact` are not in
  the NegEx `PREN` list.

**Weng, Liu, Chen. *"Deep Learning Approach for Negation and Speculation
Detection…"* JMIR Med Inform 2023;11:e46348.** PMID 37097731 `[SEARCH]` — the most
on-point paper found: **15.01 % of words and 39.45 % of important diagnostic
keywords occurred in negative or speculative statements** unrelated to abnormal
findings. That is the published justification for doing negation handling at all.

### Alert burden — the arithmetic behind the 15 % fatigue ceiling

**Bandai/Nishimoto et al., *"Automatic detection of actionable radiology reports
using BERT,"* BMC Med Inform Decis Mak 2021** `[SEARCH]`
<https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8436473/> — **90,923 reports, of which
788 (0.87 %) were actionable.** Best model: **AUROC 0.9516 but AUPRC 0.5138.**

At ~1 % prevalence even a strong model produces mostly false alarms. **A keyword
list does considerably worse.** Any claim about our alert burden must be measured
against a base rate near 1 %, not against accuracy.

---

## 5 · Two defects in `narrative.py`, found by this research

### D-N1 · No `CONJ` termination — 🟡 by reading, NOT executed

[`narrative.py:206-219`](../api/app/rules/narrative.py#L206-L219) counts scope in
**words only**. There are **no termination terms in the seed at all**, while both
the shipped NegEx and ConText terminate on `but` / `however` / `although` /
`secondary to`.

```
No evidence of fracture, however a large abscess in the liver
                  └─ ~5 words ─┘ ≤ 6-word window  →  abscess NEGATED
```

A critical finding is suppressed on a sentence pattern radiologists write
constantly. **Caught by the FOLLOW_UP floor, so it reaches a human — but as
FOLLOW_UP, not CRITICAL.**

> 🟡 **This has NOT been executed.** It is a reading-level finding. Per
> [`CLAUDE.md`](../CLAUDE.md) — *a test that has never failed is not a test* — it
> needs a test that goes red before anyone believes it.

### D-N2 · List-form negation escapes the window

Documented by Wu et al. 2014 with a worked example of exactly our shape. Same
mechanism as D-N1, different trigger.

### ❌ A claim that did NOT survive checking

The research initially reported that **gold-set case C04 "passes for the wrong
reason."** **It does not.** C04's own description reads *"A long sentence where the
negation is nowhere near the finding. Unbounded negation scope would bury the
abscess"* — it tests bounded scope, and it tests it correctly. The `however` is
incidental. **The test is honest; the gap is that no test covers conjunction
termination at all.** Recorded here because a research claim taken at face value
would have put a false accusation into the record.

---

## 5A · 🟢 Published evidence that this product is solving a real problem

**This is the most valuable finding of the research, and it is TIER A — actually
retrieved and read.** Until now the project's problem statement had no citation
behind it.

### Most recommended follow-up never happens

**McDonald JS, Koo CW, White D, et al.** *"Addition of the Fleischner Society
guidelines to chest CT exam interpretive reports improves adherence to recommended
follow-up care for incidental pulmonary nodules."* **Academic Radiology
2017;24(3):337–344.** doi:10.1016/j.acra.2016.08.026 · PMID 27793580 `[FETCHED]`
<https://pmc.ncbi.nlm.nih.gov/articles/PMC5309169/>

- **510 patients. Only 198 (39 %) received their recommended follow-up care.**
- Template in report vs control: **45 % vs 31 %** (*p* = .0014)
- Of the non-adherent, **210/312 (67 %) never received the recommended CT at all**

**Putting the recommendation in the report text moved adherence from 31 % to 45 %.
That is the ceiling of what better *reporting* achieves — and it is why tracking,
not reporting, is the product.**

### A tracking programme roughly tripled ED follow-up

**Zaki-Metias KM, MacLean JJ, Satei AM, et al.** *"The FIND Program: Improving
Follow-up of Incidental Imaging Findings."* **J Digit Imaging 2023;36(3):804–811.**
doi:10.1007/s10278-023-00780-6 · PMID 36759382 `[FETCHED]`
<https://pmc.ncbi.nlm.nih.gov/articles/PMC10287591/>

- Overall: **30.8 % → 50.7 %** (*p* = 0.03)
- **Emergency department subgroup: 5/26 (19.2 %) → 22/40 (55.0 %)** (*p* = 0.01)

> **This is the closest published analogue to Result Guardian**, and it is direct
> evidence the approach works. It is also a warning: **even after the intervention,
> ~45 % of ED patients still did not complete follow-up.** A tracking system is a
> large improvement, not a solution. No claim of "zero missed results" is supportable.

⚠️ **Do NOT cite these as validation of our engine.** They validate the *problem*
and the *category of intervention* — not our rules, and not our accuracy.

⚠️ A claimed **"28 % to 77 %"** follow-up range circulated in search summaries with
**no identifiable source.** It is excluded deliberately. Do not use it.

---

## 5B · Laboratory critical values — the decisive finding is "there is no list"

### 🔴 No national or international consensus list exists

Retrieved and quotable `[FETCHED]`
<https://acutecaretesting.org/en/articles/critical-values-in-laboratory-medicine>:

> *"There is no internationally or nationally agreed list of laboratory tests that
> warrant assignment of critical limits … the formulation and review of all aspects
> of critical value policy is thus a matter for local laboratory/healthcare facility
> management."*

And every accreditation regime **requires each lab to define its own**:

- **ISO 15189:2012 clause 5.8** — the technical requirement for critical value
  reporting. **NABL (India) applies this standard.** — Agarwal R, Chhillar N,
  Tripathi CB, *Indian J Clin Biochem* `[FETCHED]`
  <https://pmc.ncbi.nlm.nih.gov/articles/PMC4310845/> (⚠️ page gives 2013, but IJCB
  vol. 30 is 2015 — **verify the year before citing**). That paper's list was built
  *"in concurrence with the treating physician."*
- **CLIA 1988** — labs "must develop and follow written procedures for reporting
  life-threatening laboratory results or panic values."
- **Joint Commission** — critical-value reporting is a National Patient Safety Goal.

> **This is the authoritative answer to "where do our thresholds come from."** They
> come from **this hospital's own lab director's approved critical value list**,
> which ISO 15189 obliges them to already have. That document is both the correct
> clinical source and the correct legal one. **Asking the lab for it beats every
> published value below.**

### How far real accredited labs disagree — the spread IS the finding

| Analyte | Spread across published sources |
|---|---|
| **Sodium low** | **110 – 130 mmol/L.** Yang 115 · CAP 623-lab mean 120 (range 110–125) · UK labs 110–130 |
| **Sodium high** | **147 – 170 mmol/L** |
| **Glucose low** | 45 mg/dL vs 60 mg/dL — **MGH documented changing its own from 60 → 45** |
| **Glucose high** | 15.0 – 40.0 mmol/L across UK labs ≈ **270 – 720 mg/dL** |
| **Platelet low** | **20 general wards vs 10 haematology — inside a single hospital** |
| Which analytes are on the list at all | Yang's hospital omits creatinine **and** haemoglobin; Thomas omits potassium, sodium **and** calcium. **There is no common core.** |

**Do not average these.** A 15 mmol/L spread in sodium between accredited labs is
evidence that these are **policy decisions, not physical constants.**

### Values actually retrieved `[FETCHED]` — reference only, NOT for seeding

**Thomas L**, *"Critical Limits of Laboratory Results for Urgent Clinician
Notification,"* **EJIFCC 2003;14(1):11–18**, Table 1
<https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6178782/fullTextXML> —
glucose &lt;45 / &gt;500 mg/dL · haemoglobin &lt;6.6 / &gt;19.9 g/dL · platelets
&lt;20,000 / &gt;1,000,000 /µL · creatinine &gt;7.4 mg/dL (no low) · WBC &lt;2,000 /
&gt;50,000 /µL.
⚠️ Table is **adult *and* paediatric**, not adult-only. ⚠️ The **µ character is
corrupt throughout the full text** (creatinine printed "654 mmol/l", troponin
"mg/l") — **never transcribe the printed units.** ⚠️ **No potassium, sodium or
calcium row exists** in that adult table.

**Yang D, Zhou Y, Yang C**, *PLoS One* 2013;8(3):e59518, Table 1 `[FETCHED]`
<https://pmc.ncbi.nlm.nih.gov/articles/PMC3597596/> — potassium 2.80 / 6.50 ·
sodium 115 / 160 · glucose 2.5 / 27.8 mmol/L · calcium 1.6 / 3.5 mmol/L ·
platelets 20 (10 haematology) / 1000 ×10⁹/L · WBC 1.5 / 50.0 ×10⁹/L.
⚠️ **One Chinese tertiary hospital's 2010 policy. Not a consensus list.**

### 🔴 Troponin cannot have a universal threshold — a schema finding

The only figure retrieved is Thomas 2003's `>0.1 µg/L`, which **predates
high-sensitivity assays and must not be used.**

A troponin decision threshold is the **99th-percentile upper reference limit of the
specific assay on the specific analyser in this hospital**, reported **sex-specifically**
by most manufacturers. A value correct for a Roche hs-cTnT analyser is **wrong** for
an Abbott hs-cTnI one — different molecules, different scales.

**Implication for our schema:** troponin thresholds need keying to
*(analyser, assay, sex)* and populating from the lab's own package insert.
⚠️ Recorded as an **engineering constraint to confirm with the lab**, not a sourced
claim — the IFCC assay table and the Fourth Universal Definition of MI were both
NOT RETRIEVED.

### NO SOURCED VALUE FOUND

Potassium critical **high** from a consensus source · **calcium in mg/dL** ·
creatinine critical **low** (no source gives one) · adult-specific haemoglobin and
platelet values · **troponin for any modern assay** · **any NABL India primary
document** · any RCPA / RCPath / AACC / Mayo / ARUP published table.

⚠️ Creatinine also has a deeper problem: **a single high threshold ignores baseline.
AKI is a delta, not a level.**

> **Conclusion: do NOT seed `panic_thresholds` from this research.** Tier-1 evidence
> justifies the *schema* — per-analyte, per-unit, **per-context (ward)**, per-assay
> for troponin, with `source` and an approval column — but the **numbers** must come
> from the hospital's own approved list. The `DEVELOPMENT PLACEHOLDER - NOT A
> CLINICAL SOURCE` tag stays until then.

---

## 5C · Fleischner Society 2017 — retrieved, and it changes what `nodule` means

**MacMahon H, Naidich DP, Goo JM, Lee KS, Leung ANC, et al.** *"Guidelines for
Management of Incidental Pulmonary Nodules Detected on CT Images: From the
Fleischner Society."* **Radiology 2017;284(1):228–243.**
doi:10.1148/radiol.2017161659 · PMID 28240562

⚠️ **The primary paper was NOT read** (paywalled; one PDF copy found is an
image-only scan). The tables below are `[FETCHED]` from NCBI Bookshelf
*Diseases of the Chest, Breast, Heart and Vessels 2019–2022*, eds. Hodler,
Kubik-Huch, von Schulthess (Springer 2019), **reproducing** Fleischner 2017.

**Solid nodules** <https://www.ncbi.nlm.nih.gov/books/NBK553863/table/ch5.Tab1/>

| Nodule | Low risk | High risk |
|---|---|---|
| Single **&lt;6 mm** | **No routine follow-up** | Optional CT at 12 mo |
| Single **6–8 mm** | CT 6–12 mo, then consider 18–24 mo | CT 6–12 mo, then 18–24 mo |
| Single **&gt;8 mm** | Consider CT / PET-CT / tissue sampling at **3 mo** | same |
| Multiple **&lt;6 mm** | No routine follow-up | Optional CT at 12 mo |
| Multiple **6–8 mm** | CT 3–6 mo, then consider 18–24 mo | CT 3–6 mo, then 18–24 mo |
| Multiple **&gt;8 mm** | CT 3–6 mo, then consider 18–24 mo | same |

**Subsolid nodules** <https://www.ncbi.nlm.nih.gov/books/NBK553863/table/ch5.Tab2/>

| Nodule | Recommendation |
|---|---|
| Single ground-glass **&lt;6 mm** | No routine follow-up |
| Single ground-glass **≥6 mm** | CT 6–12 mo to confirm persistence, then CT **every 2 y to 5 y** |
| Single part-solid **&lt;6 mm** | No routine follow-up |
| Single part-solid **≥6 mm** | CT 3–6 mo; if unchanged and solid component &lt;6 mm, **annual CT for 5 y** |
| Multiple subsolid **&lt;6 mm** | CT 3–6 mo; if stable consider CT at **2 and 4 y** |
| Multiple subsolid **≥6 mm** | CT 3–6 mo; manage by the **most suspicious** nodule |

### 🔴 What this means for our `nodule → follow_up` classification

**The shortest interval anywhere in Fleischner 2017 is 3 months. There is no
same-day row, no 24-hour row, no immediate-action row.** On retrieved evidence it
is **a scheduling instrument, not an alerting one** — which supports the
*direction* of our `follow_up` classification.

⚠️ **But no guideline prose saying "nodules are not critical" was retrieved.** Do
**not** hard-code `nodule ≠ critical` as though a guideline said so. The direction
is supported; the assertion is not.

⚠️ **Our model has no concept of nodule size at all** — and size is the entire axis
Fleischner turns on (&lt;6 mm needs *nothing*; &gt;8 mm needs action in 3 months).
A keyword `nodule → follow_up` flattens a six-row table into one value.

**Not retrieved:** table footnotes, volumetric (mm³) equivalents — **do not encode
&lt;100 / 100–250 / &gt;250 mm³ on this research's authority.** Non-applicability
(`[SEARCH]`, unverified): adults &lt;35, children, immunocompromised, known primary
cancer, and screening populations (Lung-RADS applies instead).

**ACR Incidental Findings Committee white papers** (2010 abdominal; 2013 IFC II
parts 1–4; 2015 thyroid; 2017 renal / adrenal / liver; 2018 thoracic; 2023 **ED
actionable incidental findings**) — **titles and URLs confirmed in the search index,
NO full text retrieved, author lists NOT RETRIEVED.** The 2023 ED paper is the most
relevant to us and is completely unread.

---

## 5D · 🔴 Exit Gate 3 as written cannot be demonstrated at n=100

**This is the most consequential finding in this document.**

Our gate is *"gold set passes at **≥95 % agreement** with the clinician"* over
**100+** results. Wilson score intervals, 95 %, computed directly:

| n | observed agreement | 95 % Wilson CI | width |
|---|---|---|---|
| **100** | **95 %** | **[88.8 %, 97.9 %]** | **9.0 pts** |
| 100 | 99 % | [94.6 %, 99.8 %] | 5.3 |
| 200 | 95 % | [91.0 %, 97.3 %] | 6.2 |
| 500 | 95 % | [92.7 %, 96.6 %] | 3.9 |

**At n=100, an observed 95 % cannot be statistically distinguished from 89 %.**
You would need ~**99 %** observed agreement before the CI lower bound reaches 95 %.

> ⚠️ **The ≥95 % figure is the build plan's and is *"not negotiable downward by
> anyone in this repository."* Nothing here lowers it, and nothing here may be
> used to lower it.** This is a request to the project owner to **reformulate the
> gate as a confidence bound rather than a point estimate** — e.g. *"lower bound of
> the 95 % CI on critical-class sensitivity ≥ 90 %."* That is a claim the data can
> actually support. **Needs an ADR and the owner's decision. Not to be changed
> unilaterally.**

### Sensitivity is the real binding constraint, and it is much tighter

The critical class is rare, so sensitivity rests on the count of **truly-critical**
cases, not on 100. With **zero misses observed**, the CI lower bound is
`n/(n+3.8416)`:

| critical cases | sensitivity lower bound (0 misses) | rule-of-three cap on miss rate |
|---|---|---|
| 20 | 83.9 % | 15 % |
| **35** | **~90 %** | ~9 % |
| 73 | ~95 % | ~4 % |
| 381 | ~99 % | ~1 % |

**To claim ≥90 % sensitivity you need ≥35 truly-critical cases** — even on a perfect
run. Our per-rule coverage minimums (A≥30, B≥40, C≥25) are a **coverage** plan, not
a **power** plan. Both are needed. Published method: **Buderer NMF, Acad Emerg Med
1996;3(9):895–900** (sizes for sensitivity under low prevalence); **Rotondi & Donner,
J Clin Epidemiol 2012;65(7):778–784** (R package `kappaSize`, `CI3Cats`).

### 🔴 The rule of three also hits Exit Gate 5

**Hanley JA, Lippman-Hand A.** *"If nothing goes wrong, is everything all right?
Interpreting zero numerators."* **JAMA 1983;249(13):1743–1745.**

With **zero events in n trials, the 95 % upper bound on the rate is 3/n.**

> **Exit Gate 5's "zero missed cases" clause cannot mean what it appears to mean.**
> Two weeks of ward shadow-running with zero misses does **not** establish a zero
> miss rate — it establishes an upper bound of 3/n. At 60 tracked cases the honest
> statement is *"miss rate below 5 % with 95 % confidence,"* **not** *"zero missed
> cases."* **That gate's measurement method needs writing to say so.**

### Statistics to report — GRRAS item 13 makes a bare percentage a checklist failure

Report **all four**, pre-specified:

1. **Percent agreement with 95 % Wilson CI** — the absolute, clinician-legible number
2. **Proportion of specific agreement, per severity class** — separates *"we agree about routine"* from *"we agree about critical"*
3. **A chance-corrected coefficient with CI — report both κ and AC1.** Given our skew they will diverge; reporting both and explaining the divergence is more honest than picking the flattering one
4. **Quadratically-weighted κ**, since severity is ordinal — tells you whether disagreements are near-misses or wild

**Keep §8's "exact severity match, no partial credit" as the gate arithmetic** — that
reasoning is sound (*"one pages a doctor and one does not"*). Add the rest as
required *reporting*, not as the gate.

The literature genuinely contests raw percent agreement **in both directions**:
- **Against** — McHugh ML, *Biochem Med* 2012;22(3):276–282 (PMID 23092060)
- **For** — **de Vet HCW et al., *"Clinicians are right not to like Cohen's κ,"* BMJ 2013;346:f2125** — κ is a *relative* measure while the clinician's real question is *absolute*
- **The kappa paradox will bite us specifically**, because our classes are skewed to `routine`: Feinstein & Cicchetti, *J Clin Epidemiol* 1990;43(6):543–549; Byrt, Bishop & Carlin 1993;46(5):423–429 (prevalence index, bias index, PABAK); Sim & Wright, *Phys Ther* 2005;85(3):257–268 — the best single reference
- ⚠️ **Read before adopting AC1:** Vach W, Gerke O, *MethodsX* 2023;10:102212 — AC1 can be positive *or negative* where there is **no association at all**. It is a different statistic, **not a drop-in replacement** for κ

### ⚠️ Implementation constraint — the statistics cannot live in `test_gold_set.py`

`test_no_agreement_rate_is_reported_for_synthetic_cases` parses that module's **AST
and fails on any division and any name containing `rate`/`pct`**. That guard is
deliberate and **must stay intact**. Put the κ/CI arithmetic in a **separate module
with its own tests.**

---

## 5E · One clinician is not defensible

Three independent reasons:

1. **A single-rater reference standard is unauditable.** GRRAS item 4 requires
   specifying the *rater population of interest*; one consultant is a sample of one.
2. **Clinicians measurably disagree with each other about urgency.** A multicentre
   French ED study of 1,578 patients found nurse–physician agreement on urgent vs
   non-urgent was only **moderate (κ = 0.43)**, subgroup κ from 0.09 to 1.00.
   <https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3215166/>
   **If the reference standard has that much internal variance, "95 % agreement with
   the clinician" is measured against an unquantified yardstick.**
3. **Panel reference standards must be described.** Bertens LCM et al., *PLoS Med*
   2013;10(10):e1001531 — finds panel diagnosis is **poorly described** and experts
   **frequently not blinded to the index test**.

**Recommended design:** two clinicians rate every case, **blinded to each other and
to the engine**; a third, more senior clinician adjudicates **discordant cases only**
(the "two-plus-one" configuration used in oncology blinded independent central
review). **Report clinician-vs-clinician agreement as a named result** — if it is
below our engine-vs-clinician figure, that is the **ceiling on what any classifier
could achieve**, and it is a finding about the task.

**Affordable fallback:** one clinician rates all 100, a second rates a random ~30 %
subsample purely to estimate inter-clinician agreement. Weaker, but honestly
reportable — and far better than nothing.

**Automation bias is why blinding is mandatory, not optional:** Goddard K, Roudsari
A, Wyatt JC, *JAMIA* 2012;19(1):121–127. An unblinded clinician anchors on the
engine's answer and **the agreement rate becomes self-fulfilling.**

---

## 5F · Reporting standards — which checklist, and which to refuse

**The checklist is chosen by study design, not by technology.** Being rule-based
rules out the AI-specific guidelines but **not** STARD or GRRAS, which are
method-agnostic.

| Standard | Verdict |
|---|---|
| **STARD 2015** — Bossuyt et al., *BMJ* 2015;351:h5527 | ✅ **PRIMARY.** Scope covers "**any other method for collecting information about the current health status of a patient**." Items that will bite: **12a** (pre-specified cut-offs — **our thresholds live in editable tables, so the table version used must be pinned**; `effective_from`/`effective_to` already supports this), **15** (indeterminate results — our FOLLOW_UP-on-unclassifiable path **must be counted, not dropped**), **19** (flow diagram, now required), **23** (full 3×3 confusion matrix, not a scalar) |
| **GRRAS** — Kottner et al., *J Clin Epidemiol* 2011;64(1):96–106 | ✅ **CO-PRIMARY, not optional.** Because our reference standard is a panel of humans, we must report the panel's own inter-rater agreement or it is unauditable. **Item 13 is where a bare "95 % agreement" fails.** Item 9 is where a *deliberating* panel fails — deliberation is not independent rating |
| **SQUIRE 2.0** — Ogrinc et al. | ✅ Conditional wrapper if written up as QI. **The only guideline whose scope explicitly welcomes a rule-based system.** Item 13e — **unintended consequences: alert fatigue, over-escalation, queue backlog** |
| **TRIPOD 2015** | ⛔ **Superseded by its own authors** — TRIPOD+AI says verbatim it *"should no longer be used"* |
| **TRIPOD+AI** — Collins et al., *BMJ* 2024;385:e078378 | ⚠️ **PARTIAL at best.** A full-text search for *rule-based*, *deterministic*, *expert system*, *knowledge-based* returned **zero hits**. Its glossary defines ML as learning *"without being explicitly programmed"* — the opposite of a rule engine. **A search summary claiming it covers rule-based systems does not appear in the article. Do not cite that.** Borrow items; claiming compliance would be a scope overclaim |
| **DECIDE-AI** | ⛔ Poor fit now (AI-scoped *and* live-evaluation-scoped). **Revisit for NODE B's inference path** — its human-factors emphasis would then be best-matched. ❌ Full text unreachable; scope wording unverified |
| **CONSORT-AI / SPIRIT-AI** | ⛔ Randomised trials. Ours is neither |
| **GUIDES checklist** | ⛔ An implementation/planning tool, **not a reporting guideline.** Do not cite it as a checklist |

**There is no CDS-alert-specific reporting guideline. That is a finding, not a gap
in the search.**

---

## 5G · 🔴 There is no published number to target — and inventing one is forbidden

**No published minimum sensitivity for clinical alerting exists. No published
false-positive rate at which clinicians demonstrably disengage exists.** A 2026
JAMIA systematic review of systematic reviews (22 studies) found **only 1 of 22 even
defined alert fatigue operationally.**

> Inventing either number would be exactly the fabricated evidence
> [`CLAUDE.md`](../CLAUDE.md) forbids. **State the cost ratio we are willing to own;
> do not let a metric imply it for us.**

The defensible published route is the **threshold approach**:
**Pauker SG, Kassirer JP**, *"The Threshold Approach to Clinical Decision Making,"*
**NEJM 1980;302(20):1109–1117** — the threshold is a *function of the cost ratio*,
not a tunable hyperparameter. And **Vickers AJ, Elkin EB**, *Med Decis Making*
2006;26(6):565–574 (decision curve analysis) — **the threshold probability encodes
how you weigh a false negative against a false positive.**

▶ **Action: record "we would accept N false FOLLOW_UPs to avoid one missed critical"
as an explicit decision in PROGRESS.md + an ADR.** That makes the value judgement
visible instead of burying it in a metric.

⚠️ **Our build plan's "~15 % flag rate per 100 discharges" retune trigger is an
internal project figure with no published basis.** Keep it — an explicit, monitored
operating limit beats none — but **record in the phase doc that it is a project
decision, not a literature-derived number.**

### 🟢 The evidence supports our review-queue-first architecture

**Override rate ≠ false-positive rate**, and the *delivery route* matters more than
the rate:

| Source | Finding |
|---|---|
| Nanji et al., *JAMIA* 2014;21(3):487–491 | 52.6 % of alerts overridden — **but 53 % of those overrides were judged *appropriate*** |
| Ancker et al., *BMC Med Inform Decis Mak* 2017;17:36 | Median interruptive drug-alert acceptance **0.0 %**. Desensitization driven by **repetition and complexity, not volume** (workload showed no significant association) |
| **Singh/Murphy EHR safety-net triggers** — Murphy DR et al., *BMJ Qual Saf* 2014 (PMID 23873756) | **PPV 58–71 %** — i.e. 30–40 % false positives — **and it was published and endorsed, because the trigger feeds a *review queue*, not an interruptive modal** |

> **Same classifier accuracy, opposite acceptability, decided entirely by the route.**
> Our review-queue-first, FOLLOW_UP-as-worst-case, never-auto-close-a-narrative
> design is the one the evidence supports. **Worth stating in the protocol as a
> deliberate, cited choice rather than leaving it an implementation detail.**

**And the line that settles the alert-fatigue argument** — van der Sijs H et al.,
*JAMIA* 2006;13(2):138–147, the field's canonical review `[FETCHED verbatim]`:

> *"The alerting system may contain error-producing conditions like **low
> specificity, low sensitivity**, unclear information content, unnecessary workflow
> disruptions, and unsafe and inefficient handling."*

**The foundational alert-fatigue paper names low sensitivity as an error-producing
condition alongside low specificity. Alert fatigue is not a licence to raise the
miss rate.**

⛔ **Retracted during research — do not use, anywhere:** all ECRI Top-10 hazard
rankings, and all Joint Commission Sentinel Event Alert 50 figures **including the
widely-quoted "85 %–99 % of alarm signals do not require clinical intervention"**
(primary unreachable, and it is an *estimate TJC attributes to other literature*,
not a measurement).

---

## 5H · Anonymisation and ethics — our premise is out of date

⚠️ **Nothing here is legal advice. The India-side conclusions need a local lawyer
and the hospital's IEC.**

### 🔴 Correction: the DPDP Rules, 2025 WERE notified — 13 November 2025

Not still in draft. <https://www.pib.gov.in/PressReleasePage.aspx?PRID=2190655>
Commencement is **phased**: Rules 1, 2, 17–21 from publication; Rule 4 from ~Nov 2026;
**Rules 3, 5–16, 22, 23 — including Rule 16, the research exemption — only from
~13 May 2027.**

### ⚠️ What is binding on us RIGHT NOW (2026), and is easy to miss

DPDP § 44(2) repeals the **IT (SPDI) Rules 2011** — **but only from ~13 May 2027.**
Until then **both regimes co-exist**, and under SPDI Rule 3 sensitive personal data
expressly includes *"physical, physiological and mental health condition"* and
*"medical records and history."*

> **The currently-binding regime is STRICTER on health data than the one replacing
> it** — the DPDP Act has no sensitive-data class at all.

### "We anonymised it, so DPDP doesn't apply" is an argument, not a safe harbour

DPDP § 2(t) defines personal data as data about an individual *"identifiable by or
in relation to such data."* The mainstream view is that truly anonymised data falls
outside — **but the Act contains no exemption clause for anonymised data and no
statutory definition of "anonymisation."** The 2019 Bill defined it; **that did not
survive into the 2023 Act.**

**The better-fitting route is § 17(2)(b) — the research exemption.** It requires
**(i) the data is not used to take any decision specific to a Data Principal.**
That is a **real design constraint: gold-set records must never feed back into the
care of the patients they came from.** Our corpus is already stored outside git and
outside the live system, which satisfies it — **say so explicitly in the protocol.**

### 🔴 There is no Indian statutory de-identification standard

**No Safe Harbor equivalent exists in Indian statute or in the notified Rules** — no
identifier list, no expert-determination mechanism, no risk threshold, no
definition. **This is a finding, not a gap in the search.**

**Consequence:** any standard we adopt — HIPAA Safe Harbor (45 CFR § 164.514),
ISO 25237, DICOM PS3.15 Annex E — we adopt **voluntarily as good practice.**
Document the choice and why it is adequate. **Never describe HIPAA as compliance we
are discharging** — it is US law with no application to an Indian hospital.

Two Safe Harbor items that break clinical datasets:
- **(C) all date elements except year** — a result timestamp is a date element. Our
  *"dates may be shifted but must stay internally consistent"* is the right instinct;
  **formalise it as a per-patient random offset** held in a separate access-controlled
  key file, or destroyed.
- **(R) any other unique identifying code** — narrative impressions routinely carry
  referring-doctor names, ward/bed numbers, UHIDs and accession numbers **in the
  prose**. **Scrub the text, not just the structured fields.**

### 🔴 A gap our protocol does not cover at all: DICOM

Harder than text de-identification and the commonest source of accidental leakage.

- **DICOM PS3.15 Annex E** — Attribute Confidentiality Profiles.
  <https://dicom.nema.org/medical/dicom/current/output/chtml/part15/chapter_e.html>
  **Annex E states its own limitation: the profiles do not replace a de-identification
  process — they are only part of one.**
- **Burned-in pixel text survives every header scrub.** Endemic in ultrasound and
  secondary capture. `BurnedInAnnotation` (0028,0301) is frequently absent or wrong
  and **cannot be trusted as a filter.**
- Aryanto et al., *Eur Radiol* 2015;25:3685–3695 — tested ten free toolkits;
  **only one removed all required elements at default settings.**

▶ **At n=100 the whole corpus is humanly reviewable, so make visual inspection of
every image mandatory** on top of an Annex E run. **Strip private tags wholesale and
remap all UIDs** — original UIDs are unique identifying codes under (R).

### Ethics — do NOT self-exempt

**ICMR National Ethical Guidelines, 2017.** Three review tracks (exemption /
expedited / full) categorised **by risk**, and scope extended in 2017 to **research
involving biological material and datasets**.

> **Exemption is granted by the Ethics Committee, not claimed by the investigator.**
> All research — including work believed exempt — is submitted with a justification.
> **If we intend to publish, expect full review.** Deciding to publish *after*
> obtaining an exemption is the failure mode.

⚠️ **The QI framing probably will not save us.** The generalizability test cuts
against us: a gold set built to characterise sensitivity/specificity, intended for
deployment beyond one unit, **is designed to produce generalizable knowledge** —
which reads as research, or at minimum an "overlap project," which Baily et al.
say should be **treated as research**.

⚠️ **Confirm the hospital's IEC is DHR/NAITIK-registered for Biomedical and Health
Research** — a separate regime from CDSCO-registered clinical-trial ECs.
<https://naitik.gov.in/DHR/Homepage>

❌ **No authoritative Indian guidance was found mapping a software-classifier
validation onto a QI/operational exemption. This needs local legal and IEC advice.**

---

## 6 · Questions for the clinician review — carry these into 3.8

These are the reason this document exists. **None may be resolved by an engineer.**

1. **Should malignancy be `critical` or `follow_up`?** ACR says Category 3 (days).
   Ours says critical. Over-flagging is safe but costs alert budget.
2. **Should radiology and pathology share one keyword table?** CAP/ADASP 2012 says
   their policies should be separate with different timeframes.
3. **Do we need a third severity tier?** Both ACR and RCR use three. We have two.
4. **Should cancer be its own axis**, per RCR 2022, rather than a severity?
5. **Are `septic`, `obstruction`, `consolidation`, `lesion`, `effusion`, `abscess`
   right for this hospital?** No published list we retrieved names them.
6. **Should we add the ACR Category 1 terms we lack** — aortic dissection, ectopic
   pregnancy, cord compression, torsion, tube malposition, unstable spine fracture,
   pneumoperitoneum?
7. **Should `perforation` be `pneumoperitoneum` / `free air`** — the words a
   radiologist actually writes?
8. **Should qualified terms be supported** (`tension pneumothorax` ≠ `pneumothorax`)?
   This is a schema change, not a config change.
9. **Should `however` terminate negation scope?** Published algorithms say yes.
   **This changes clinical behaviour, so it is the clinician's call, not ours.**

**Added after the laboratory and incidental-findings research:**

10. **Will the lab give us their ISO 15189 / NABL critical value list?** This is the
    single highest-value request in the project. It replaces every placeholder at
    once, and it is a document they are *required* to already have.
11. **Should platelet thresholds be ward-dependent?** Yang et al. document 20
    general vs **10 on haematology** inside one hospital — a real precedent, and a
    schema change (thresholds keyed to context, not just analyte).
12. **How is troponin configured here?** Which analyser, which assay, and is the
    99th-percentile URL sex-specific? **A universal troponin threshold is wrong by
    construction.**
13. **Should creatinine flag on a delta rather than a level?** AKI is a change from
    baseline; a single high threshold cannot see it.
14. **Should nodules carry size?** Fleischner turns entirely on mm (&lt;6 mm needs
    nothing; &gt;8 mm needs action in 3 months) and our model has no size concept.

---

## 7 · Still unread — the highest-value gaps

| Document | Why it matters | Status |
|---|---|---|
| **RCR/AoMRC Oct 2022 full PDF** | Reported to contain a **national, enumerated critical-findings list with standard alert codes**. A published list beats our reconstruction entirely | 🔴 **NOT RETRIEVED — highest value** |
| **Larson 2014 full text** | The actual Category 1/2/3 table. Ours above is `[SEARCH]` reconstruction | 🔴 NOT RETRIEVED |
| **Fleischner Society 2017** primary text | Tables now retrieved **second-hand via NCBI Bookshelf** (§5C). Still missing: footnotes, mm³ equivalents, and any prose on urgency | 🟡 **Tables obtained, paper unread** (paywalled; the one free PDF is an image-only scan) |
| **The hospital lab's own critical value list** | **ISO 15189 clause 5.8 / NABL require them to have one.** Replaces every `panic_thresholds` placeholder at once, and is the correct clinical *and* legal source | 🔴 **NOT REQUESTED — highest value in the whole project** |
| **CAP Q-Probes**: Wagar 2007 (163 labs, *Arch Pathol Lab Med* 131(12):1769–75, PMID 18081434) and Howanitz 2002 (623 institutions, PMID 12033953) | The only large multi-lab consensus threshold tables identified | 🔴 Paywalled — needs institutional access |
| **ACR ED actionable incidental findings white paper**, JACR 2023, S1546-1440(23)00123-0 | The most directly relevant paper found to our exact problem | 🔴 NOT RETRIEVED, entirely unread |
| **RCPA/AACB Consensus Statement on High Risk Laboratory Results** | An actual consensus statement, freely hosted | 🔴 NOT RETRIEVED |
| NegEx 2001 primary paper | Resolves the 5-vs-6-token question; NPV unknown | 🔴 Paywalled (403) |
| ACR Incidental Findings white papers; BTS nodule; TI-RADS; Bosniak | Incidental-finding follow-up | 🔴 NOT RETRIEVED |
| CAP/ADASP named example diagnoses | We have the category definitions, not the worked examples | 🔴 NOT RETRIEVED |

**To close these**, `WebFetch` needs to be permitted for `pmc.ncbi.nlm.nih.gov`,
`arxiv.org`, `aclanthology.org`, `link.springer.com`, `medinform.jmir.org`,
`jacr.org` and `rcr.ac.uk`, and the session web-search budget raised — **or** a
human downloads the three top documents and they are read locally.

---

## Related

- [clinical-validation.md](clinical-validation.md) — **authoritative on Exit Gate 3.** Still open. No clinician has reviewed anything
- [clinical-validation-protocol.md](clinical-validation-protocol.md) — how the gate actually gets closed; §9 covers threshold provenance
- [build/phase-03-clinical-rule-engine.md](build/phase-03-clinical-rule-engine.md) — the phase doc. **Its status is unchanged by this research**
- [`api/app/rules/narrative.py`](../api/app/rules/narrative.py) · [`api/scripts/seed_rules_dev.py`](../api/scripts/seed_rules_dev.py)
