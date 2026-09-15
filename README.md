# Result Guardian

> **No post-discharge investigation result is ever lost.**

A patient is discharged on Tuesday. Their urine culture comes back on Thursday
showing an organism resistant to the antibiotic they were sent home with. The
report lands in a lab system nobody is watching, the patient is at home, and the
doctor who ordered it has moved on to forty other people.

That is the failure this product exists to make impossible.

**A doctor physically cannot complete a discharge while an investigation has no
responsible owner and no expected-by date.** From that moment the result is
tracked by database timers, classified by a deterministic rule engine, and
escalated through a five-rung ladder that ends at the patient if nobody acts.

Runs entirely **on-premise**, on two machines, on a private LAN. **No internet
required. No patient data ever leaves the building.**

---

## Contents

- [The idea in one screen](#the-idea-in-one-screen)
- [Three rules that shape everything](#three-rules-that-shape-everything)
- [The two machines](#the-two-machines)
- [What is actually built](#what-is-actually-built-honest-status)
- [Quick start](#quick-start)
- [Walk the demo](#walk-the-demo)
- [How it works, stage by stage](#how-it-works-stage-by-stage)
- [The rule engine](#the-rule-engine-no-ai)
- [The AI, and its leash](#the-ai-and-its-leash)
- [Testing](#testing)
- [On a phone](#on-a-phone)
- [What is not built](#what-is-not-built)
- [Repo map](#repo-map)

---

## The idea in one screen

```
 DISCHARGE GATE          TRACKING              RULE ENGINE           ESCALATION
 ─────────────────────   ───────────────────   ──────────────────    ──────────────────
 Cannot discharge        Timers live in        Pure Python.          T+0   flag raised
 while a test has        PostgreSQL, not       Same input always     T+4h  owner reminded
 no owner and no         only in a queue.      the same output.      T+12h unit head
 expected-by date.       NODE A reboots →      Never calls the AI.   T+24h patient
                         pg_cron re-fires.     Never needs network.        contacted
```

The AI appears exactly once, at the end, behind a button a doctor has to press —
and every sentence it produces is checked against the source before anyone sees
it. See [The AI, and its leash](#the-ai-and-its-leash).

---

## Three rules that shape everything

**RULE 1 — The safety property never depends on AI.**
Tracking and escalation are database constraints and a state machine. Phases 0–5
are a complete, working product with **zero AI**.

**RULE 2 — The safety property never depends on the network between nodes.**
Everything that keeps a patient safe runs on NODE A. Unplug the GPU box and the
system loses a convenience, never a guarantee.

**THE ONE RULE — A later phase must never break an earlier one.**
Each phase degrades to the previous one, *not to silence*. Every phase that adds
a dependency ships a test that **takes it away** and asserts the old path still
works.

These are not aspirations. There is a test that switches NODE B off and asserts
the flag, the timers and the escalation ladder are all untouched.

---

## The two machines

| | Machine | Runs | Holds patient data |
|---|---|---|---|
| **NODE A** — core | Any office PC, CPU only | PostgreSQL, FastAPI, worker, Caddy, React dashboard | **Yes — all of it** |
| **NODE B** — inference | One shared GPU box | Ollama only. Stateless. | **Never** |

**What crosses the wire to NODE B:** the question, and the text of the
hospital's own approved guidance.
**What never does:** patient name, MRN, phone number, the PDF, or any row from
any patient table.

> Turn NODE B off and the patient is still tracked, still flagged, still
> escalated. Only the *Explain* button degrades — and it degrades to showing the
> retrieved guidance with no generated text, **not to silence**.

---

## What is actually built (honest status)

This project treats "written" and "done" as different words. A phase is ✅ only
when it has actually run.

| Phase | Status | Exit gate |
|---|---|---|
| 0 · Foundation | ✅ done | ✅ **passed** |
| 1 · Data model + **discharge gate** ★ | ✅ done | ✅ **passed** |
| 2 · Durable timers | ✅ done | ✅ **passed** |
| 3 · Clinical rule engine | 🛑 code done + audited | 🔴 **open — needs a clinician** |
| 4 · Ownership + escalation ★ | ✅ done + audited | ✅ **passed** |
| 5 · Dashboard, closure, audit ★ | ✅ done + audited | 🔴 open |
| 6 · Document ingestion (PDF/OCR) | ✅ done, run end to end | 🔴 open |
| 7 · Extraction + matching | 🔵 code done | 🔴 cannot close |
| 8 · RAG explanation | 🔵 built, run end to end | 🔴 cannot close |
| 9 · Hospital integration (HL7/FHIR) | 🔴 not started | ⬜ |
| 10 · Security + production | ⬜ not started | ⬜ |

### ⛔ The honest caveats, stated plainly

**No clinician has ever reviewed the rule engine's output.** Exit Gate 3
requires ≥95 % agreement with a clinician and **there is no agreement rate at
all**. Nobody should describe this system's accuracy with a number.

**Every panic threshold in the database is a development placeholder**, marked
`source = 'DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE'`. They are plausible
numbers, not any hospital's. Replacing them is one conversation with a lab.

**The knowledge base contains one fabricated demo document**, labelled as such in
its title, its `source_ref` and its publisher. No WHO, ICMR, antibiogram or NLEM
content is loaded.

What the design *does* guarantee by construction is **asymmetry**: unknown
escalates, missing data escalates, only two narrow paths ever auto-close, and a
narrative report can never auto-close whatever it says. It is built to be wrong
in the safe direction.

---

## Quick start

Needs **Docker** and **Docker Compose v2**. Nothing else.

```bash
git clone https://github.com/AshmitThakur23/result-guardian.git
cd result-guardian
cp .env.example .env
#  edit .env: POSTGRES_PASSWORD, RG_JWT_SECRET, RG_LLM_BASE_URL
docker compose up -d --build
docker compose exec api alembic upgrade head
curl http://localhost/api/health
```

The migrate step is **not optional** — the schema, including the worker's
heartbeat table, is owned by Alembic.

### Seed it

```bash
docker compose exec -T api python scripts/reset_dev_passwords.py
docker compose exec -T api python -m scripts.seed_rules_dev
docker compose exec -T api python scripts/seed_dev.py
docker compose exec -T api python scripts/seed_kb_demo.py
```

> ### ⚠️ Run all four again after any `pytest` run
>
> `test_migration_round_trip` downgrades and re-upgrades, which **drops and
> recreates the tables** — including `panic_thresholds`, `antibiotic_synonyms`,
> `clinical_keywords` and `mdro_rules`. The rule engine then runs with **no
> thresholds and no synonyms**, silently: cultures come back `follow_up` instead
> of `critical` because "Monocef" no longer maps to ceftriaxone, and nothing
> warns you. This has bitten us; it will bite you.

Then open **http://localhost** — password for every account is
`ResultGuardian#2026`.

| Code | Role | Start here for |
|---|---|---|
| `DOC1` | doctor | Worklist, cases, the Explain panel |
| `ADMIN1` | admin | Everything, plus **Guidance** (the knowledge base) |
| `LAB1` | lab_tech | Documents — PDF upload and the review queue |
| `HEAD1` | unit_head | The above plus Reports |
| `AUDIT1` | auditor | Audit trail, read-only |

---

## Walk the demo

The story is more convincing in this order.

**1. The gate refuses a discharge.** Open a patient with an outstanding
investigation and try to discharge them. It is refused, in words, with the
blocking orders named. *This is the product.*

**2. Assign and discharge.** Give each blocking order a responsible doctor and an
expected-by date. Now discharge succeeds — and the result is under contract.

**3. Enter the result the lab would send.** On the urine culture: organism
`Escherichia coli`, count `>100,000 CFU/mL`, specimen `urine`, antibiotic
`Ceftriaxone` → **R**.

Watch the severity preview **before you save**. It says *Critical*, and names the
discharge antibiotic it conflicts with. That is the rule engine, not AI.

**4. The worklist flags it.** Critical first, then oldest. The row carries a
colour, a coloured rail, an icon shape and a word — four channels, so it survives
greyscale, a projector, and colour-blindness.

**5. Ask why it matters.** Open the case, press **Explain**. ~15 seconds. You get
a short explanation, and under it the **exact passage of hospital guidance it
came from, with the quoted sentence highlighted inside it.**

**6. ★ Now break it on purpose.** As `ADMIN1`, turn NODE B off with the kill
switch. Press Explain again.

You get *"The AI assist is offline"* — **and the approved guidance is still shown
underneath**, because retrieval never left NODE A. The flag, the timers and the
escalation ladder are all untouched. That is RULE 2, on screen, in one click.

---

## How it works, stage by stage

```
PDF / manual entry
      ↓
 Phase 6   Document ingestion — PyMuPDF for native text, PaddleOCR for scans.
           Anything unreadable goes to a human WITH the page images, never to
           a stack trace. The workflow never stalls because parsing failed.
      ↓
 Phase 7   Extraction + matching. Report → pending order, by scoring
           arithmetic. ★ NO AI decides which patient a result belongs to.
           Signals are never summed, and a TIE GOES TO A HUMAN.
      ↓
 Phase 3   Rule engine. Deterministic Python. Details below.
      ↓
 Phase 4   Owner resolution — seven steps, ending at an admin fallback, so it
           never returns nobody. Then the escalation ladder.
      ↓
 Phase 5   Dashboard, acknowledgement, closure with a reason, and a
           hash-chained append-only audit log enforced by a database trigger.
      ↓
 Phase 8   Explain — the only step that uses NODE B.
```

**Timers live in PostgreSQL, not only in a queue.** A `pg_cron` sweep runs every
five minutes and re-fires anything overdue, so *NODE A reboots and nothing is
lost*. There is a chaos test: 50 cases, 2 restarts mid-flight, exactly 50 flags.

---

## The rule engine (no AI)

Three rules, all pure Python, reading configuration from **tables an admin can
edit** — never constants in code.

| | Reads | Example |
|---|---|---|
| **A — numeric** | the lab's own reference range, plus `panic_thresholds` | K⁺ 7.2 → **critical** |
| **B — culture** | organism, sensitivity panel, discharge medications | *E. coli* R to ceftriaxone, patient on Monocef → **critical** |
| **C — narrative** | `clinical_keywords` + negation patterns | "suspicious for malignancy" → **critical** |

Things worth knowing, all measured rather than assumed:

- **Thresholds are time-versioned.** When a guideline changes you add a row with
  a new `effective_from`. A result from last year still classifies against the
  threshold in force *when it was reported*.
- **Only two paths ever auto-close** — a no-growth culture, and a culture fully
  covered by the discharge antibiotic. Numeric and narrative rules never do.
- **Auto-close is AND-across-every-rule.** A covered culture *plus one benign
  microscopy line* stays open, because the narrative rule refuses.
- **"No evidence of malignancy" is not a flag** — the negation check is real.
- **A contaminant beats a resistance.** 4,000 CFU/mL in urine is not an
  infection, even if the organism is resistant to what the patient is taking.
- Indian lakh digit grouping (`1,50,000`) parses correctly. Censored values
  (`>1000`, `<0.5`) are handled as bounds, not numbers.

---

## The AI, and its leash

Phase 8 answers *"why does this matter?"* — and is wrapped in four constraints.

**1. It is never asked unless a doctor asks.** No auto-run, no prefetch, no
retry. The hook is a mutation, not a query, precisely so it cannot fire on its
own.

**2. It can only quote approved guidance.** A document is invisible to retrieval
until a named person approves it. The Guidance screen makes adding and approving
**two separate acts**, because a document becomes retrievable when somebody
approves it, never because somebody uploaded it.

**3. ★ Every quotation is verified by plain code before anyone sees it.**
The span verifier has **no import path to NODE B at all**. If a quoted sentence
is not in the source, the citation is rejected; if *every* citation is rejected,
the whole explanation is discarded rather than shown with fewer citations —
prose that looks sourced and is not is the dangerous artefact. Rejections are
written to `ai_rejections`, so the hallucination rate is **countable**.

**4. Failure is always silence, never a wrong answer.** Four paths return no
explanation — nothing retrieved, NODE B unreachable, malformed output, all
citations rejected. Each says which, each returns `200`, and **none touches the
flag**.

The measured example this was built around: asked "what is a critical potassium
level?", a live model answered **"6.0 mEq/L or higher"** — fluent, confident, and
from no source this system holds. The verifier exists for that sentence.

---

## Testing

```bash
docker compose exec -T api python -m pytest        # 1171 backend tests
cd web && npx vitest run                           # 187 frontend tests
cd web && npx playwright test                      # 53 end-to-end, real stack
```

The E2E suite runs against the **real** Caddy, the real API and the real
Postgres with its real constraints. Nothing is mocked.

**Conventions this repo actually enforces:**

- **A test that has never failed is not a test.** Any test guarding a defect is
  made to fail on purpose first — revert the fix, watch it go red, restore it.
  This caught an RBAC test that passed while 31 endpoints were reachable with no
  credential at all.
- **Check the exit code, never the last line.** `ruff check .` ends with "No
  fixes available", which reads like success while it is reporting five errors.
- **Degradation is tested, not intended.** Every phase that adds a dependency has
  a test that removes it.
- **`jsdom` performs no layout**, so unit tests cannot see a broken table.
  `e2e/layout.spec.ts` measures real geometry at six viewports in both themes;
  `e2e/contrast.spec.ts` measures WCAG contrast on rendered pages. Both assert a
  **minimum number of elements measured**, so a broken selector fails instead of
  reporting a confident green over nothing.

---

## On a phone

The Android app is a thin Capacitor shell that loads the live dashboard from
NODE A over the LAN — so there is nothing to rebuild on the web side when the UI
changes, and no patient data is ever stored on the device.

```bash
adb connect <phone-ip>:<port>
adb install -r web/android/app/build/outputs/apk/debug/app-debug.apk
```

Point `server.url` in `web/capacitor.config.ts` at NODE A. If NODE A's DHCP lease
moves, that file must change and the APK must be rebuilt.

> `ping` to NODE A may report 100 % loss while the app works perfectly — ICMP is
> blocked on a "Public" Wi-Fi profile, but TCP/80 passes. Don't trust ping.

---

## What is not built

Named here so nobody mistakes any of it for working.

- **No notification can leave the building.** `SmsAdapter` and `SmtpAdapter` are
  written but never registered, and there are no provider settings. Every email
  and SMS rung silently degrades to in-app. **The T+24h patient SMS cannot reach
  a patient.**
- **Hindi/Punjabi is partial.** Only the patient-facing template is genuinely
  translated; six other localised files still contain English.
- **Semantic search is not built.** Retrieval is Postgres full-text fused
  through RRF. `bge-m3` embeddings and the reranker named in the architecture
  are not installed, so `kb_chunks.embedding` is always NULL. This is a recorded,
  deliberate deviation — ranking is worse, guidance does not disappear.
- **LOINC has no data.** The mapping cascade is written, wired and indexed;
  `loinc_terms` has **0 rows** because the release is a registration-gated
  download.
- **FHIR R4 is not built.** `fhir` is only a value in an enum. That is Phase 9.
- **Ownership does not re-assign after rung 0.** Reminders follow the duty
  roster, but the dashboard keeps showing the original owner.

---

## Repo map

| Path | What |
|---|---|
| [`PROGRESS.md`](PROGRESS.md) | **Where we are.** The session log and phase status — start here |
| [`CLAUDE.md`](CLAUDE.md) | The rules this build is held to |
| [`docs/build/`](docs/build/) | Phase-by-phase plan, `phase-00` … `phase-10` |
| [`docs/architecture/`](docs/architecture/) | What happens at runtime |
| [`docs/adr/`](docs/adr/) | Settled decisions, with reasoning |
| [`docs/clinical-validation.md`](docs/clinical-validation.md) | **Has a clinician checked the rule engine?** (no) |
| `api/app/rules/` | The rule engine — start at `orchestrator.py` |
| `api/app/services/rag/verify.py` | The span verifier ★ |
| `web/e2e/` | End-to-end tests against the real stack |

**Scale:** 48 tables, 16 migrations, 74 API endpoints, 1411 tests.

---

## Stack

One PostgreSQL 16 instance carries everything — data, vectors (pgvector), the
queue and timers (pgmq), scheduled sweeps (pg_cron), fuzzy text (pg_trgm) and
keyword search (tsvector). **No Redis, no RabbitMQ, no Elasticsearch.** One thing
to back up, one thing to restore, one transaction boundary.

Python 3.11 · FastAPI · SQLAlchemy 2.0 async · Alembic ·
React 18 + TypeScript · Vite · Tailwind · TanStack Query ·
Ollama on NODE B · Capacitor for Android.
