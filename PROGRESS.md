# Result Guardian — Progress Ledger

> **Read this first, every session.** It is the resume point.
> Updating it after every unit of work is mandatory — see the protocol in [`CLAUDE.md`](CLAUDE.md).

**Last updated:** 2026-09-15

> ## ▶ RESUME HERE
>
> **Repo:** https://github.com/AshmitThakur23/result-guardian (private, `main`)
>
> **Machines** — never write "this machine"; name the owner.
> **NODE A = Abhinendra's laptop**, `LAPTOP-06ER0HBM`. Docker present. No NVIDIA GPU.
> **NODE B = Ashmit's machine**, `LAPTOP-5JCGN9SJ`, **172.25.54.48** — ✅ **provisioned 2026-09-14**,
> RTX 3050 / 4 GB VRAM, Ollama `v0.15.2` serving `qwen3:4b`. On NODE A set
> `RG_LLM_BASE_URL=http://172.25.54.48:11434`.
> See [ADR 0006](docs/adr/0006-node-roles-corrected.md), which supersedes ADR 0005.
>
> **📋 EVERYTHING STILL OWED FROM PHASES 0–6 IS IN ONE FILE:**
> **[`docs/phase-6-outstanding.md`](docs/phase-6-outstanding.md)** — the register to
> return to **after Phase 8**. Sixteen items, each marked 🔧 engineering,
> 🧑‍⚕️ human input or ⚖️ owner decision, with how it was found and what would
> close it. **Nothing in it blocks Phase 7 or Phase 8.**
>
> The two with the longest lead times, and therefore the real critical path:
> **ask the lab for their ISO 15189 critical value list** (one conversation, and
> it replaces every `panic_thresholds` placeholder), and **book a clinician**
> (Exit Gate 3 has no agreement rate).
>
> ⚠️ **CI is GREEN again** (`0ce8a10` — 1052 passed, 1 xfailed), **and the timer
> deadlock is still open.** It hit **once in ~11 runs (≈9 %)**, so a couple of
> passes is the expected outcome either way. §1 of that register stays open until
> the cause is understood, not until the light goes green.

> **🏁 EXIT GATE 0 IS CLOSED — 2026-09-14. All four clauses verified.**
> The two nodes are **connected**: `172.25.52.148` ⇄ `172.25.54.48` on
> `172.25.48.0/20`, **32–36 ms**, proven from **inside the API container** — the
> only path that counts, because `localhost` and `host.docker.internal` mean the
> wrong machine in there.
>
> **RULE 2 is proven, not asserted.** Ollama was deliberately stopped on NODE B
> with both machines on one LAN: `/api/health` stayed **200**, `degraded_features`
> was **exactly `["llm_generation"]`**, the **entire backend suite stayed green**,
> and the worker kept logging `sla_timers` and `classify` **message_handled**
> throughout. It recovered on its own in ~6 s with **no action on NODE A**.
>
> ⛔ **Never diagnose the link with `ping`** — Windows blocks inbound ICMP, so it
> false-negatives on a working network. That false negative cost this project a day.
> Use `Test-NetConnection -Port`, and read the new **"a config value is not
> verified until something has READ it"** rule in [`CLAUDE.md`](CLAUDE.md) before
> trusting any address in a doc.
>
> **🏁 CI is GREEN — all 3 jobs (2026-09-12, run `34670777455`).** All nine defects D1–D9
> are **verified**, not merely fixed. The NODE A Postgres image builds and runs with
> pgmq 1.4.4 + pg_cron 1.6 + all six extensions, and migrations apply cleanly.
>
> **🛑 PHASE 3 IS ON HOLD FOR CLINICAL VALIDATION. EXIT GATE 3 IS 🔴 OPEN.**
> 3.1–3.7 are built, audited and **committed locally (not pushed)**; **3.8 needs a
> clinician and has not had one.** There is **no agreement rate** and **0 real
> anonymised results** in the gold set — it is 54 synthetic cases.
> **Phase 3 is deferred until after all planned phases**, then returned to.
> ▶ See **[🛑 PHASE 3 CLINICAL VALIDATION — RETURN AFTER ALL PHASES](#-phase-3-clinical-validation--return-after-all-phases)**
> below, and [docs/clinical-validation.md](docs/clinical-validation.md).
>
> **📄 PHASE 6 IS BUILT AND RUNS END TO END (2026-09-14).** Upload → pgmq →
> worker → text + spans, native and scanned, with the mandatory fallback to
> Phase 3 manual entry. 1040 backend tests and 187 frontend tests pass; ruff,
> black and mypy strict are clean. Nine defects were found and fixed on the
> way — **three of them only by the end-to-end run**, including OCR being
> entirely broken while every unit test passed.
> ▶ Full record: **[`docs/build/phase-06-verification-log.md`](docs/build/phase-06-verification-log.md)**.
>
> **🔴 EXIT GATE 6 IS OPEN AND CODE CANNOT CLOSE IT.** Two of its three
> clauses are measurements over **200 real PDFs**, and the corpus is at **0**.
> The synthetic PDFs in `api/tests/_documents.py` are unit-test fixtures for
> our own plumbing and **must never be presented as gate evidence**.
> Phase 6 was started as a **knowing exception** while Exit Gate 5 was open —
> recorded below.
>
> **▶ NEXT, on NODE A:** bring up the **full compose stack** — the piece still unproven.
> `cp .env.example .env` (set `POSTGRES_PASSWORD`, `RG_JWT_SECRET`, `RG_LLM_BASE_URL`) →
> `docker compose up -d --build` → `docker compose exec api alembic upgrade head` →
> `curl localhost/api/health`. That covers Exit Gate 0 items 1–3 and finishes 0.2/0.5/0.7.
> ⚠️ Port 5432 is taken on NODE A by the native `postgresql-x64-18` service — dev compose
> already maps **5433**, so do not change it.
>
> **Then Exit Gate 0 still needs NODE B:** `infra/nodeb/setup-windows.ps1` on Ashmit's
> machine, both on one LAN, and `curl http://<NODE_B_IP>:11434/api/tags` from NODE A —
> plus the power-off-NODE-B degradation check. Open decision #5 (LAN IPs) is undecided.
>
> **🏁 THE MVP IS CODE COMPLETE (2026-09-13).** Phases 1–5 are built and audited.
> A doctor can sign in, see their flags CRITICAL-first, open a case, read why it was
> flagged in plain English, and close it with a reason — and every one of those acts
> lands in a hash-chained audit log that detects tampering. **Zero AI. Single node.**
>
> **🔴 But Exit Gate 5 is OPEN, and so is Exit Gate 3.** Both are blocked on the same
> thing: **a clinician who has not yet looked at the rule engine.** Gate 5 additionally
> needs two weeks of shadow-running in a real ward. Neither is a coding task.
> ▶ See [Phase 5's exit gate](docs/build/phase-05-dashboard-audit-mvp.md#-exit-gate-5).
>
> **▶ NOW IN PHASE 1.** Phase 0 is functionally PASS on NODE A; only 0.3 (NODE B
> provisioning) and Exit Gate 0's two cross-node clauses remain, and neither blocks
> Phase 1, which is NODE-A-only.
>
> **✅ Phase 1.1 is COMPLETE — all 11 tables**, across migrations `0002_core_schema`
> and `0003_core_schema_remaining`. `case_events` is append-only, enforced by a
> database trigger and proven to reject both UPDATE and DELETE.
> **✅ Phase 1.2 is COMPLETE** — migration `0004_phase_1_2_indexes`.
> **✅ Phase 1 BACKEND COMPLETE and audited — 1.1, 1.2, 1.3, and 6 of 7 of 1.8.**
> A full verification pass on 2026-09-12 found **zero current defects**.
> **✅ Phase 1.4 — Discharge gate UI is COMPLETE (2026-09-12).** The frontend now
> exists: `web/`, React 18 + TS + Vite + Tailwind, three-step gate at
> `/encounters/:id/discharge`. **59 tests pass**, `npm run build` is green, and the
> bundle is served live through Caddy (`/encounters/<id>/discharge` → 200, assets 200).
> **There is no skip control anywhere** and a test enforces that by scanning every
> button and link on the page.
> 🟡 **One thing did not run: the Playwright E2E spec.** It is written
> (`web/e2e/discharge-gate.spec.ts`, 10 tests asserting against the database, not the
> screen) and its live-DB seeder is verified working — but Chromium build 1243 is not
> downloaded, so the spec has **never executed**. It stays 🟡, not ✅.
> **✅ Phase 1.5 — Supporting screens is COMPLETE (2026-09-12).** Patient search
> (MRN/name/phone in one box, on the Phase 1.2 trigram index), encounter detail
> (orders + contracts + medications in one read), manual order creation,
> discharge medication entry, and a deterministic seed script
> (20 patients · 60 orders in varied states · 10 doctors).
> **294 backend + 99 frontend tests pass**, coverage **87%** (was 79%), ruff/black/
> mypy-strict/tsc/build all clean, **autogenerate drift = 0 — no migration needed.**
> 🔒 **The documented order-creation race is closed.** `create_manual_order` takes
> the *same* `FOR UPDATE` encounter lock the discharge action takes, so an order
> can never slip past a concurrent discharge. Proven, not assumed: with the lock
> temporarily removed the race test failed **6 of 6 runs** with the unsafe outcome
> `['discharged', 'order_created']`; with it restored, 6 of 6 pass.
> **✅ Phase 1.8 COMPLETE and 🏁 EXIT GATE 1 PASSED (2026-09-12).** Chromium
> installed; **22 Playwright tests pass** against the real stack, asserting out of
> Postgres rather than off the screen. All three Exit Gate 1 clauses verified in a
> real browser, with NODE B unreachable throughout.
> The browser run earned its keep: it found **one real product defect** jsdom had
> missed — a restored draft whose responsible doctor was outside the first page of
> search results rendered an **empty** field that the gate nonetheless accepted.
> Fixed, with three regression tests.
> ⚠️ **1.6 (corpus) and 1.7 (vendor) remain 🔴 open human/calendar items.** They are
> not Exit Gate 1 clauses: 1.6 gates **Phase 6**, 1.7 gates **Phase 9**. Neither
> blocks Phase 2.
> **✅ Phase 2.1 — the timer model is COMPLETE (2026-09-12).** Migration
> `0005_sla_timers`: the table that carries timer truth, plus the `pg_cron` sweep
> that closes the gap if the queue is ever lost. **330 backend tests** (+36),
> drift 0, and a real head→0004→head migration round trip.
> ⚠️ **Creating a timer on discharge is Phase 2.2, not 2.1** — the build plan puts
> it under "Timer lifecycle". Phase 1.3's discharge still enqueues its own
> `result_due` wake-up and writes no `sla_timers` row; wiring them together is
> 2.2's first bullet. Until then the sweep has nothing to sweep, which is the
> correct state of a half-built phase.
> ⚠️ **One genuine spec ambiguity, reported not buried:** the plan names
> `idempotency_key (unique)` and never defines its construction. See the Open
> decisions table.
> **✅ PHASE 2 IS COMPLETE — audited, pushed (`13a7151`), CI green.**
> 2.1 model · 2.2 lifecycle · 2.3 missing-result/lab flags · 2.4 manual intake ·
> 2.5 tests · **Exit Gate 2 PASSED** (50 cases, two restarts, exactly 50 flags).
> **425 backend** · 102 frontend · 22 Playwright · coverage 88.20% · drift 0 at head
> `0006_phase_2_lifecycle` · health `ok` with `llm.reachable: false`.
> The audit found **no P0/P1/P2 defects**. It did find and fix one test-isolation
> gap (seed cleanups orphaned Phase 2 rows) and recorded one cross-phase gap as
> open decision #8 (`orders.status` is advanced by no phase; the gate over-blocks,
> which fails safe).
>
> **▶ NOW IN PHASE 3 — the clinical rule engine.** Deterministic, **no AI**
> (RULE 1). ⚠️ **3.8 and Exit Gate 3 are clinician-gated**: they need 100+ real
> anonymised results and a clinician's sign-off at ≥95% agreement, so the gate
> cannot close from code alone — the same shape as 1.6/1.7.
>
> Section-level detail is in the status tables in
> [`docs/build/phase-00-foundation.md`](docs/build/phase-00-foundation.md) and
> [`docs/build/phase-01-data-model-discharge-gate.md`](docs/build/phase-01-data-model-discharge-gate.md).

---

## Phase status

**Legend — same markers everywhere (here and in every phase doc):**

| Marker | Means |
|---|---|
| ✅ **done** | Built **and verified**. Actually ran, actually passed. |
| 🟡 **written, never run** | Committed but never executed. **Not done.** |
| 🔵 **in progress** | Being worked on now |
| ⬜ **not started** | — |
| 🔴 **blocked** | Waiting on something external |
| 🚫 **out of scope** | Decided against; ADR linked |

> **Every phase doc has its own 📍 STATUS SUMMARY table at the top** showing which sections are done.
> Tick as you go — see the protocol at the top of [`CLAUDE.md`](CLAUDE.md). **Written is not done.**

| Phase | Node | Status | Exit gate | Notes |
|---|---|---|---|---|
| — Knowledge base | — | ✅ **done** | n/a | 20 docs extracted from both PDFs, 2026-09-11 |
| [0 · Foundation](docs/build/phase-00-foundation.md) | A + B | ✅ **done** | ✅ **PASSED 2026-09-14** | **All 4 clauses verified.** Full stack on NODE A — health 200, `db: ok`, heartbeat, migrations `0013`. **Both nodes connected** (`172.25.52.148` ⇄ `172.25.54.48`, 32–36 ms), proven from inside the API container. **RULE 2 closed by the deliberate test**: Ollama stopped on NODE B → health still **200**, only `llm_generation` degraded, **whole suite green**, and **SLA timers + classification kept firing**. Recovered automatically in ~6 s with no action on NODE A |
| [1 · Data model + discharge gate ★](docs/build/phase-01-data-model-discharge-gate.md) | A | ✅ **done — all code sections** | ✅ **PASSED** | **This is the product, and it works.** 1.1–1.5 + 1.8 all ✅. 1.6 corpus + 1.7 vendor stay 🔴 (human/calendar; gate Phases 6 and 9, not Phase 2) |
| [2 · Durable timers](docs/build/phase-02-durable-timers.md) | A | ✅ **done** | ✅ **PASSED** | 2.1–2.5 all built, audited and pushed (`13a7151`). Chaos test: 50 cases, 2 restarts, exactly 50 flags. Sweep-only recovery proven |
| [3 · Clinical rule engine](docs/build/phase-03-clinical-rule-engine.md) | A | 🛑 **ON HOLD — code done + audited (3.1–3.7)** | 🔴 **OPEN** | 3 of 4 gate clauses proven end to end. **3.8 needs a clinician and has not had one — there is no agreement rate.** [clinical-validation.md](docs/clinical-validation.md). Not pushed |
| [4 · Ownership + escalation ★](docs/build/phase-04-ownership-escalation.md) | A | ✅ **done + audited** | ✅ **PASSED** | 4.1–4.7 built; 6 defects found and fixed. ⚠️ Started with Exit Gate 3 open — a knowing exception, see above. Not pushed |
| [5 · Dashboard, closure, audit ★](docs/build/phase-05-dashboard-audit-mvp.md) | A | ✅ **done + audited (5.1–5.7)** | 🔴 **OPEN** | 🏁 **MVP code complete.** Audit found 31 unauthenticated endpoints and a chain-forking concurrency bug; both fixed with regression tests. Gate needs 2 weeks of ward shadow-running and a clinician sign-off — neither can be produced by code. Not pushed |
| [6 · Document ingestion](docs/build/phase-06-document-ingestion.md) | A | ✅ **done + run end to end (6.1–6.5)** | 🔴 **OPEN** | Upload → pgmq → worker → text + spans; native **and** scanned, real OCR at 0.997. **18 defects found**, 9 only by the end-to-end run — including one unreadable PDF crash-looping the worker and taking Phase 2's timers with it. Gate needs **200 real PDFs; corpus is 0**. ▶ [verification log](docs/build/phase-06-verification-log.md) |
| [7 · Extraction + matching](docs/build/phase-07-extraction-matching.md) | A (+B) | 🔵 **code done — 7.1–7.7** | 🔴 **CANNOT CLOSE** | Started 2026-09-14 as a **knowing exception**, Exit Gate 6 open. Migration `0014` adds 5 tables; round-trip clean, drift 0. **7.5 matching contains no AI** — pure arithmetic, thresholds as arguments. **Ties go to a human** (verified by removing the rule: 2 tests went red with `auto_matched` where `needs_review` belonged). **Signals are never summed**; a missing collection date counts as disagreement. 15 tests. 🔴 Still open: template editor UI, `document_span_id` plumbing, LOINC release not loaded. Gate measured on **100 labelled real documents; corpus is 0** |
| [8 · RAG explanation](docs/build/phase-08-rag-explanation.md) | A + B | 🔵 **8.1–8.6 built and run end to end** | 🔴 **CANNOT CLOSE** | Started 2026-09-15 as a **knowing exception**, Exit Gate 7 open. Migration `0016`: `kb_documents`/`kb_chunks`, HNSW + GIN. Retrieval RRF `k=60`, **degrades to keyword-only with no embedder**. ★ **Span verifier is plain code with no import path to NODE B** — fuzzy 0.95 for typography not paraphrase, min 20 chars, all-citations-fail rejects the **whole** response; **proven by disabling it — 3 tests went red**. Every rejection is a row in `ai_rejections`, invented text kept verbatim. **8.6 UI shipped and verified end to end against a live NODE B** — the quote is highlighted inside its source passage and the E2E asserts that highlighted text is really in a `kb_chunks` row. 21 API tests + 4 E2E. ★ **Kill switch now actually stops generation** — it did not, until `949821d`. 🔴 Still open: **the only KB content is a labelled demo fixture** (`scripts/seed_kb_demo.py`) — no real hospital guidance, no antibiogram; **8.2 PDF ingestion not written** (text-only). Gate needs a clinician |
| [9 · Hospital integration](docs/build/phase-09-hospital-integration.md) | A | 🔴 blocked | ⬜ | 3–5 wks. ⛔ gated by hospital IT / HIS vendor |
| [10 · Security + production](docs/build/phase-10-security-production.md) | A + B | ⬜ not started | ⬜ | 3 wks + external test turnaround |

**Timeline reference:** MVP at 11 weeks (team of 3) / 21 weeks (solo). Production at 25 / 46 weeks.

---

> ### ⚠️ Knowing exception — Phase 6 started with Exit Gate 5 open
>
> **2026-09-14.** Both [`phase-06-document-ingestion.md`](docs/build/phase-06-document-ingestion.md)
> and the governing rule say *"Do not start a phase until the previous exit
> gate passes."* Exit Gate 5 is **open** — it needs two weeks of ward
> shadow-running and a clinician sign-off, neither of which can be produced by
> code. Phase 6 was started anyway, on the project owner's instruction.
>
> **This is a knowing exception to the build plan's own ordering rule, not an
> oversight** — recorded here because [`CLAUDE.md`](CLAUDE.md) requires exactly
> that, as was done when Phase 1 started before Exit Gate 0 closed and when
> Phase 4 started with Exit Gate 3 open.
>
> **Why it is defensible:** Phase 6 changes how a result *arrives* — a file
> instead of typing — and nothing about how it is tracked, graded, escalated
> or closed. Its mandatory fallback routes any document it cannot read to the
> Phase 3 manual entry form, so the worst case is the workflow the hospital
> already has. Phase 5's open clauses are about clinical behaviour over a
> fortnight; Phase 6 does not touch that behaviour.
>
> **What it does not license:**
> - Phase 6 passing its own gate would **not** close Exit Gate 5.
> - **Exit Gate 6 cannot be closed at all right now.** It requires 200 real
>   PDFs and the corpus stands at **0 of 200+**. The code is built; the gate
>   stays 🔴 OPEN until real documents exist.
> - Synthetic PDFs are used as unit-test fixtures for the plumbing. They are
>   **not** gate evidence, and the phase doc's prohibition stands.

> ### ⚠️ Knowing exception — Phase 4 started with Exit Gate 3 open
>
> **2026-09-13.** `phase-04-ownership-escalation.md` says *"Do not start this
> phase until Exit Gate 3 passes."* Exit Gate 3 is **open** and can only be
> closed by a clinician, who is not booked yet. Phase 4 was started anyway, on
> the project owner's instruction.
>
> **This is a knowing exception to the build plan's own ordering rule, not an
> oversight** — recorded here because [`CLAUDE.md`](CLAUDE.md) requires exactly
> that when a phase starts on an unclosed gate (the same was done when Phase 1
> started before Exit Gate 0 closed).
>
> **Why it is defensible:** Phase 4 routes and escalates whatever severity
> Phase 3 produces. It does not depend on those severities being clinically
> *correct*. A clinician later tuning thresholds changes rows in
> configuration tables, not Phase 4 code — so the two do not conflict.
>
> **What it does not license:** Phase 4 passing its own gate does **not** close
> Exit Gate 3, and the project is not validated until the section below is
> worked through.

# 🛑 PHASE 3 CLINICAL VALIDATION — RETURN AFTER ALL PHASES

> **This is a DEFERRED task. It is not permission to start clinical validation now.**
> Read this before declaring the project validated, and before anyone quotes a
> number about the rule engine's clinical accuracy.

**Deferred on:** 2026-09-13 · **Return when:** all planned implementation phases are complete · **Owner:** project owner (needs external people, not code)

## Where Phase 3 actually stands

| | |
|---|---|
| **Technical implementation** | ✅ **Complete.** 3.1–3.7 built, audited, committed locally. 658 backend tests, 91% coverage, 30 Playwright against the real stack. |
| **Clinical validation (3.8)** | 🛑 **INTENTIONALLY ON HOLD.** Not failed, not skipped — deferred, deliberately. |
| **Exit Gate 3** | 🔴 **OPEN.** Three of four clauses are proven; the fourth needs a clinician. |

## ⛔ Do not

- **Do NOT consider Phase 3 clinically validated.** It is not, and no amount of green tests makes it so.
- **Do NOT present the current gold set as clinical validation.** It holds **54 synthetic cases and 0 real anonymised results**. Every expectation in it was written by the same people who wrote the rules — which is exactly the circularity a clinician exists to break.
- **Do NOT invent** reviewer names, review dates, clinical threshold sources, or an agreement percentage.
- **Do NOT weaken or bypass the honesty guards.** Two tests in `api/tests/test_gold_set.py` enforce this: one fails if `clinician_validated` is flipped without a named reviewer and a date, the other parses the harness's own AST and fails if any code computes a proportion while the set is synthetic. Both were deliberately tripped and observed to fire. **They are load-bearing, not decoration.**
- **Do NOT declare the project fully validated** until this section is closed out.

## When you return — what has to happen

**[`docs/clinical-validation-protocol.md`](docs/clinical-validation-protocol.md) is the source of truth for the process.** It settles the schema, the identity rules, the reviewer fields and the arithmetic *in advance*, so nobody designs them under time pressure in a room with a consultant. Follow it; do not improvise a second process.

1. **Obtain 100+ real anonymised results** meeting the protocol's distribution — Rule A ≥30, Rule B ≥40 (over-sample: highest clinical value), Rule C ≥25, mixed ≥5.
2. **Obtain the hospital-approved critical/panic-value source.** All 8 `panic_thresholds` rows are still development placeholders, findable with one query:
   `WHERE source = 'DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE'`
3. **Obtain written permission and a named de-identifier.** This shares its blocker with the [Phase 1.6 corpus](docs/test-corpus-manifest.md) — one approval unblocks both.
4. **Arrange qualified clinician review**, blinded: the clinician's severity is recorded *before* they see the engine's.
5. **Keep clinician judgement and engine output in separate fields.** ⚠️ The current fixture has a single `expected_severity`. If the clinician's answer and the pass/fail expectation are the same field, "fixing" a failing case makes the agreement rate **100% by construction** — arithmetically correct and meaningless. Protocol §1 defines the split.
6. **Resolve disagreements using the protocol's closed vocabulary** — `threshold_tuned` · `clinician_revised` · `accepted_gap` · `rule_defect`. **Tune thresholds in tables, never rule code.**
7. **Re-run the validation harness** (`pytest tests/test_gold_set.py`) on the full set.
8. **Only then calculate and report agreement.** Never before real clinician validation exists.
9. **Only if Exit Gate 3 passes**, prepare the final Phase 3 audit and push.

## The intended project flow — do not reorder

```
Phase 3 technical work
   └─> HOLD  ◀── we are here
        └─> continue implementing later phases
             └─> finish all planned phases
                  └─> RETURN TO PHASE 3 CLINICAL VALIDATION   ◀── this section
                       └─> complete real clinician validation
                            └─> final project validation
```

**Why deferring is safe:** Phases 0–2 guarantee no result is *lost*; Phase 3 only decides how loudly to say so. A wrong severity still leaves the case tracked, recorded, visible and overrulable. That is why an unvalidated rule engine may ship *behind* the tracking, and may never ship *instead of* it. See the reasoning in [`docs/clinical-validation.md`](docs/clinical-validation.md).

**Status record:** [`docs/clinical-validation.md`](docs/clinical-validation.md) — what is and is not validated, kept honest.

---

# 📄 PHASE 6 DOCUMENT INGESTION — BUILT; EXIT GATE 6 STILL 🔴 OPEN

> ⚠️ **This section was written on 2026-09-14 to stop the phase being
> started, and the phase was then started the same day on the project
> owner's instruction.** It is kept — rewritten, not deleted — because the
> *second* blocker it names never cleared and still gates the exit.
>
> Read this before anyone reports a Phase 6 **result**.

**Built on:** 2026-09-14 · **Exit Gate 6:** 🔴 OPEN, and **code cannot close it**

**Source of truth:** [`docs/build/phase-06-document-ingestion.md`](docs/build/phase-06-document-ingestion.md) for what the phase is; [`docs/build/phase-06-verification-log.md`](docs/build/phase-06-verification-log.md) for what was actually built, run and fixed.

## Status

| | |
|---|---|
| **Phase 6 implementation** | ✅ **BUILT AND RUN END TO END.** Migrations 0012/0013; `documents`, `document_pages`, `document_spans`; the `ingest` consumer; upload endpoint, watched folder, virus hook; native + scanned extraction with real OCR; review queue, overlay and retry UI. 18 defects found and fixed. |
| **Exit Gate 6** | 🔴 **OPEN.** 1 of 3 clauses met. |

**What is still 🟡 — written and never executed**, and therefore not done:
the watched-folder consumer (off by default until a real share is mounted),
the ClamAV `clean` path (no scanner deployed here), and the table-structure
test (needs a real tabular report). Each is listed with its reason in §4 of
the verification log.

## The blocker that did NOT clear — and still gates the exit

**The Phase 1.6 corpus is at 0 of 200+ real PDFs.**

Two of Exit Gate 6's three clauses are *measurements over real hospital
paperwork*:

- **200 real PDFs, ≥95% producing usable text** — 0 of 200.
- **Spans spot-checked on 20 documents** — needs 20 real documents.

The third clause, *failures land in the review queue with page images
visible*, is met and tested three ways.

The phase doc's own blocking prerequisite says why the first two cannot be
faked:

> do not fake it with synthetic PDFs, they will not surface the failure modes
> real labs produce

The ≥95% figure is a claim about how the extractor copes with **a particular
hospital's** fax headers, stamp overlays, carbon-copy scans and bilingual
letterheads. Measuring it against PDFs we generated ourselves would measure
our own fixture generator. The synthetic PDFs in
[`api/tests/_documents.py`](api/tests/_documents.py) are unit-test fixtures
for our plumbing and **must never be counted toward the 200**.

## The blocker that was knowingly overridden

**1 · Exit Gate 5 was OPEN.**

The governing rule in
[`docs/build/00-governing-rules-and-topology.md`](docs/build/00-governing-rules-and-topology.md):

> *"Do not start a phase until the previous exit gate passes."*

and Phase 6's own status summary repeats it:

> *"⚠️ **Do not start this phase until Exit Gate 5 passes.**"*

Exit Gate 5 has three unticked clauses — two weeks of ward shadow-running,
zero missed cases, and clinician sign-off on flag quality. **None of them can
be produced by writing code.**

**2 · The Phase 1.6 real corpus does not exist.** — ⚠️ **STILL TRUE.**

**0 of 200+** real anonymised PDFs collected —
[`docs/test-corpus-manifest.md`](docs/test-corpus-manifest.md). Phase 6 names
this as a blocking prerequisite in its own first lines:

> *"⛔ **BLOCKING PREREQUISITE:** the de-identified test corpus from **Phase
> 1.6**. If it is not ready, this phase cannot start — **do not fake it with
> synthetic PDFs, they will not surface the failure modes real labs produce.**"*

This blocker stands **on its own**, and it is the one that did not clear. The
implementation was built anyway on the project owner's instruction; **Exit
Gate 6 remains open because of this**, and no amount of further code will
close it.

## ⛔ Do not

- **Do NOT tick Exit Gate 6.** The implementation being finished is not the gate passing. Two of its three clauses are measurements over 200 real PDFs.
- **Do NOT present synthetic PDFs as evidence for Exit Gate 6.** Synthetic files are acceptable as fixtures for unit-testing plumbing where that is genuinely useful; they are **not** gate evidence. Exit Gate 6 is measured against real documents, and the phase doc says why: generated PDFs do not reproduce the skew, stamps, bleed-through, bilingual headers or broken table borders that real labs emit.
- **Do NOT invent** documents, permissions, de-identification sign-off, clinical validation, or gate results.
- **Do NOT modify Phase 3's clinical validation status.** It remains 🔴 NOT VALIDATED — see [the Phase 3 hold section](#-phase-3-clinical-validation--return-after-all-phases).
- **Do NOT change Phase 5's Exit Gate status.** It remains 🔴 OPEN, for the reasons recorded in its own gate block.

## What has to happen before Exit Gate 6 can close

1. **Obtain 200+ real anonymised PDFs** under the existing corpus, permission and de-identification requirements in [`docs/test-corpus-manifest.md`](docs/test-corpus-manifest.md) — written permission, a named de-identifier, storage outside git with only the manifest tracked. All five blockers there need human action. ⚠️ This shares its approval with the [Phase 3 gold set](#-phase-3-clinical-validation--return-after-all-phases): **one permission unblocks both.**
2. **Categorise the corpus as it is collected** — native/scanned, lab/radiology/path/micro, single/multi-page — which is task 6.6 and the manifest's own instruction.

## Exit Gate 6 — where it actually stands

Verbatim from [`docs/build/phase-06-document-ingestion.md`](docs/build/phase-06-document-ingestion.md):

- [ ] 🔴 **200 real PDFs** processed: **≥95%** produce usable text — **0 of 200**
- [ ] 🔴 Spans map correctly (spot-check the overlay on **20** documents) — the overlay is built and the span invariant is proven against synthetic PDFs, but **spot-checking 20 real documents needs 20 real documents**
- [x] ✅ Failures land in the review queue with page images visible — tested at unit level, over HTTP, and demonstrated by accident when OCR was broken for an entire run and **no document was lost**

**One of three.** The two open clauses are measurements over real hospital
paperwork and cannot be satisfied with generated files.

---

## ⏳ Long-lead items — calendar-gated, start regardless of phase

These are not blocked by code. They are blocked by other people, and they take months. **The build plan says to start the top two in week one of Phase 1.**

| Item | Source | Blocks | Status | Started |
|---|---|---|---|---|
| De-identified corpus, 200+ real reports | 1.6 | **Phase 6 cannot start without it** | 🔴 **opened, 0/200** → [manifest](docs/test-corpus-manifest.md) | 2026-09-12 |
| HIS/LIS vendor conversation | 1.7 | **Phase 9** | 🔴 **opened, all unanswered** → [spec](docs/integration-spec.md) | 2026-09-12 |
| Clinician time — gold set, 100+ results | 3.8 | **Exit Gate 3** | 🛑 **deferred by decision** → [return-after-all-phases](#-phase-3-clinical-validation--return-after-all-phases) | — |
| Clinician time — 100-question AI eval set | 8.7 | **Exit Gate 8** | `not started` | — |
| Medico-legal liability policy, signed | 10.3 | **Go-live** | `not started` | — |
| Hospital's own critical-value list (for `panic_thresholds`) | 3.2 | Phase 3 seeding | 🔴 **still outstanding** | Dev placeholders seeded under `source = 'DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE'`; one `WHERE` finds all 8 |

---

## ❓ Open decisions

Record the decision, the date, and the reasoning. A decision that lives only in chat gets re-litigated.

| # | Decision | Forced by | Status |
|---|---|---|---|
| 1 | **OPD scope** → **OUT for v1, schema stays ready.** `encounters.type` keeps `opd` so no migration is needed later; the gate binds to `ipd\|emergency\|daycare`. No reliable visit-closure trigger exists, and OPD volume would blow the 15% alert-fatigue ceiling before thresholds are tuned. Safe because Phase 7.5 routes caseless results to an orphan queue. | Phase 1 scope note | ✅ **decided 2026-09-11** → [ADR 0003](docs/adr/0003-opd-out-of-scope-v1.md) |
| 2 | **`duty_roster` owner** → **the unit head, weekly.** HR sync is Phase 9.4 (after MVP) so it cannot be the v1 answer. The unit head is rung 2 of the ladder, so a stale roster escalates to the person maintaining it. Ships with a weekly reminder, a stale badge, and fallthrough to unit head. | Phase 4.1 | ✅ **decided 2026-09-11** → [ADR 0004](docs/adr/0004-duty-roster-ownership.md) |
| 3 | **Node roles** → ~~this laptop is NODE B~~ **CORRECTED: Abhinendra's laptop (`LAPTOP-06ER0HBM`) is NODE A; Ashmit's (`LAPTOP-5JCGN9SJ`) is NODE B.** The original entry was backwards. | Phase 0.4 | ✅ **re-decided 2026-09-11** → [ADR 0006](docs/adr/0006-node-roles-corrected.md), superseding [ADR 0005](docs/adr/0005-node-roles-and-model.md) |
| 4 | **Model** → **`qwen3:4b`. A sound decision, not a placeholder.** It was derived from **4 GB of VRAM on the machine that actually runs inference** — `nvidia-smi` on Ashmit's `LAPTOP-5JCGN9SJ` confirms the RTX 3050 is on **NODE B**, and NODE A has no NVIDIA GPU at all (Intel UHD only). 8B q4 (~5–6 GB) genuinely does not fit. Re-benchmark before Phase 8 as the build plan says; `RG_LLM_MODEL` is an env var. | Phase 8.4 | ✅ **decided 2026-09-11, re-confirmed 2026-09-12** → [ADR 0006 correction](docs/adr/0006-node-roles-corrected.md). *Was briefly reopened on a wrong GPU attribution; that is now resolved.* |
| 5 | **Network method → shared Wi-Fi (`Studentwifi_5G`), both nodes on `172.25.48.0/20`.** NODE A `172.25.52.148`, NODE B `172.25.54.48`, gw `172.25.48.1`. Measured 2026-09-14: the campus SSID **does** permit peer traffic (TCP to ports 80/8000 succeeded), so no hotspot, router or crossover cable is needed for dev. ⚠️ **Both addresses are DHCP** — re-read them after every network change, and re-scope NODE B's firewall rule. A hotspot stays the fallback; **the hospital deployment must use a managed switch with reservations, not this.** | Phase 0.4 | ✅ **decided 2026-09-14** → [`docs/network-runbook.md`](docs/network-runbook.md) |
| 6 | **`sla_timers.idempotency_key` construction** → **`case_id:timer_type:fire_at`** (UTC, microsecond precision). ⚠️ **Derived, not quoted — the build plan names the column and marks it unique but never defines its construction.** This is the only reading that satisfies both 2.2 ("one `result_due` per case at `contract.expected_by`"; a replayed discharge must not create a second) and 2.3 ("re-check every 24h until resolved" — a different instant, so a different timer). A second `UNIQUE(case_id, timer_type, fire_at)` was deliberately **not** added: the plan specifies one unique column, and encoding the rule twice means two places to change. | Phase 2.1 | ✅ **confirmed 2026-09-12** — 2.2 now creates real timers and the construction held unchanged. A replayed discharge recomputes the identical key and the unique index refuses it; 2.3's 24h re-checks are distinct instants and so distinct timers, exactly as predicted |
| 7 | **`(status = 'fired') = (fired_at IS NOT NULL)`** as a CHECK on `sla_timers`. ⚠️ **Derived** from the column's meaning; the plan lists the field but states no rule. A fired timer knows when it fired, and nothing else claims to have fired. | Phase 2.1 | ✅ **confirmed 2026-09-12** — every 2.2 transition was implemented against it and none needed relaxing. `pending→fired` sets both together in one statement; `pending→cancelled/superseded` leaves `fired_at` null; a fired timer is never rewritten, because cancel and supersede both carry `WHERE status = 'pending'` |
| 8 | **`orders.status` is never advanced by result intake, so the discharge gate keeps blocking an order whose result has already arrived.** The build plan assigns order-status transitions to **no phase**: Phase 1.5 sets it at creation, Phase 9's HIS feed would set it later, and Phase 2.4's three bullets are "transition the case, cancel `result_due`, enqueue classification" — none of which mention the order. Left unchanged because adding the transition would invent behaviour the plan gives to no phase, and the current behaviour **fails safe**: it over-blocks rather than letting an unowned result through. Verified empirically in the Phase 2 audit — a result recorded before discharge is stored with `case_id = NULL` (Phase 7.5 routes those) and the gate still lists the order as blocking. | Phase 2.4 / Phase 7 | ⬜ **open** — decide in Phase 7 whether intake advances `orders.status`, or whether the gate should read result presence instead |

---

## 🔍 Pre-install scan — 2026-09-11

Per the standing rule in `CLAUDE.md`: check both drives before installing anything. **Nothing needed installing.**

> **Rule updated 2026-09-11 (user):** the scan stays mandatory, the permission prompt does not.
> **Check first — if it is already there, use it; if it is missing, install it without asking.**
> Report either way and log it here. Large things still go to `D:` (`C:` is ~93% full), and
> anything over ~5 GB gets a heads-up before it lands.

| Tool | Found | Note |
|---|---|---|
| Ollama | ✅ `v0.15.2`, `%LOCALAPPDATA%\Programs\Ollama` | not running; configure only |
| Docker + Compose | ✅ `29.7.2` / `v5.4.0` | daemon stopped |
| Git / gh CLI | ✅ `2.49.0` / `2.78.0` | `AshmitThakur23` was inactive — switched |
| Python / Node | ✅ `3.13.7` / `v22.18.0` | API runs 3.11 **in the container** |
| PostgreSQL | ⚠️ native install present | dev compose uses host port **5433** to avoid the clash |
| `make` | ❌ absent | `tasks.ps1` mirrors the `Makefile` |
| GPU | see per-machine notes below | figures below are **per machine** - do not read one machine's number as the other's |

> **The scan table above mixed both machines' figures on 2026-09-11 and had to be split.**
> Disk and GPU are machine-specific. **Always label which machine a number came from.**

### NODE B - Ashmit's machine (`LAPTOP-5JCGN9SJ`, user `asus`)

| Item | Value | Note |
|---|---|---|
| GPU | **RTX 3050 Laptop, 4 GB VRAM** | Verified with `nvidia-smi`. **This is NODE B's GPU** - so `qwen3:4b` in decision #4 *is* correctly derived. See the hardware correction in `CLAUDE.md` |
| Disk | **C: 37.7 GB free (83%)**, D: 177 GB | was 19 GB / 93% - see cleanup below |
| Ollama | `v0.15.2` installed | `OLLAMA_MODELS=D:\Nexus AI\.ollama\models`, holds `mistral:7b` 4.07 GB. **Do not repoint it** - `D:\Nexus AI` depends on that model. Let `qwen3:4b` land beside it |

#### C: cleanup - 2026-09-11, +18.3 GB freed

Docker needs headroom and `C:` was at 93%. Cleared only regenerable caches; **nothing on `D:` touched, `mistral:7b` intact, 10 recent VS Code workspaces kept.**

| Item | Freed |
|---|---|
| WinSxS - `DISM /StartComponentCleanup` (**no** `/ResetBase`) | **+15.4 GB** - component store 26.58 -> 11.19 GB |
| VS Code `workspaceStorage`, folders 90+ days old (48 of 58) | +4.33 GB |
| npm cache | +1.53 GB |
| Temp files | +0.76 GB |

**Deliberately NOT touched:** `Windows\Installer` (21.7 GB - breaks app repair), hibernation (laptop loses Fast Startup), Playwright (used by two other projects), Python packages, Chrome's Gemini Nano model (re-downloads immediately), and `/ResetBase` (can break updates on Win11 25H2).

### NODE A - Abhinendra's machine

| Item | Value | Note |
|---|---|---|
| Machine | **`LAPTOP-06ER0HBM`**, user `Abhinendra Singh` | Lenovo 83BF · Win 11 Home SL, build 26200 |
| CPU | **Intel Core i5-12450H** (12th gen) | 8 physical / 12 logical cores |
| RAM | **15.7 GB** | |
| Disk | **C: 39.5 GB free of 268 GB** (85% used) · **D: 682.8 GB free of 683.6 GB** | D: is effectively empty → Docker images and any corpus belong there |
| GPU | **Intel UHD Graphics only — NO NVIDIA GPU**, `nvidia-smi` absent | ✅ Independently confirms the RTX 3050 is on **NODE B**. Irrelevant anyway: NODE A never runs inference (RULE 2) |
| Port 5432 | ⚠️ **occupied** — native `postgresql-x64-18` service is running | This is why dev compose maps **5433**. Required on **both** machines, not just NODE B |
| Ollama | installed at `%LOCALAPPDATA%\Programs\Ollama` | **Unnecessary on NODE A** and unused. Inference is NODE B's job |

**NODE A toolchain — differs from NODE B, do not assume either machine's versions:**

| Tool | NODE A | NODE B (for contrast) |
|---|---|---|
| Docker / Compose | **29.5.2** / **5.1.4** | 29.7.2 / v5.4.0 |
| Python | **3.11.9** — matches the project pin exactly | 3.13.7 |
| git / gh | **2.53.0** / **2.87.3** | 2.49.0 / 2.78.0 |
| Node | **v24.14.0** | v22.18.0 |
| `make` | **absent** | absent — `tasks.ps1` is justified on both |

#### Second scan - 2026-09-11, lint/test toolchain

Scanned, all three absent, installed (new rule: no prompt needed). Small, on `C:`, well under the 5 GB heads-up threshold.

| Tool | Before | After |
|---|---|---|
| ruff / black / mypy | absent | `0.16.7` / `26.5.1` / `2.3.1` |
| Project + dev extras | not installed | `pip install -e ".[dev]"` - **which is itself the proof that defect D1 is fixed** |
| Python | - | `3.11.9`, matching the project pin |

---

## 📓 Session log

Newest first. One line per completed unit of work.

### 2026-09-16 — ⏹ NODE B STOOD DOWN. Ollama no longer starts at login, and `qwen3:4b` is deleted.

> **Owner's instruction, the project being complete.** Read this before assuming NODE B is reachable.

- ⛔ **NODE B no longer serves anything automatically.** The logon scheduled task `Result Guardian - Start Ollama` and the `shell:startup` shortcut were both **removed**, and Ollama is stopped (GPU 0 MiB). **Any Phase 8 explanation path will now read `ConnectionRefused` until someone starts it by hand** — which is RULE 2 behaving correctly, not a fault. `docs/` and [`infra/nodeb/start-ollama.bat`](infra/nodeb/start-ollama.bat) are untouched, so running that script restores NODE B in one step.
- 🗑 **`qwen3:4b` deleted** at the owner's decision, freeing **2.33 GB** (blobs 10 → 5, 6.40 GB → 4.07 GB). The measured comparison against `mistral:7b` that justified the model switch is recorded in the 2026-09-14 entry and in [`docs/build/phase-08-rag-explanation.md`](docs/build/phase-08-rag-explanation.md); the weights themselves were only corroborating evidence and are re-pullable.
- ✅ **`mistral:7b` deliberately kept and verified still working** — it belongs to the separate `D:\Nexus AI` project, and deleting a sibling model is exactly the kind of change that breaks a neighbour silently. Not trusted from `ollama list`: the model was **loaded and asked a question**, and it answered. 5 blobs / 4.07 GB and the manifest intact.
- ℹ️ **Nothing was uninstalled.** Ollama itself stays (Nexus AI depends on it), and the removals above are registry/Startup entries only — all reversible.

### 2026-09-15 — ★ A "flaky test" was a real 500, and the UI audit found three classes that emit no CSS

- ★ 🔴 **`POST /api/cases/{id}/explain` accepted any UUID and 500-ed.** It never checked the case existed: it ran retrieval, spent **~14 s of NODE B's GPU**, and then died inside the verifier, because `ai_rejections.case_id` has a foreign key to `pending_cases` and a rejected citation cannot be recorded against a case that is not there. **Now 404s before any work is done.** Proved red-then-green with a test that also asserts NODE B is *not* called.
- ⚠️ **It presented as a flaky E2E — 1 run in 3.** It only fires when the model happens to write a citation that fails verification, and that varies run to run. **A missing existence check plus a non-deterministic model is how a real defect hides as a flake.** Chasing it instead of retrying was the right call; after the fix the spec went **4 for 4**.
- 🔴 **Three Tailwind classes in shipped code emit NO CSS AT ALL** — confirmed by compiling the project's own config against probe markup *and* by grepping the built bundle:
  | Class | Where | Consequence |
  |---|---|---|
  | `bg-critical-subtle/40` | `Worklist.tsx` | **The critical-row tint never rendered.** Safety-relevant, not cosmetic |
  | `ring-brand/30` | `Badge.tsx`, `ExplainPanel.tsx` | Fell back to an off-palette default blue that never dark-adapts |
  | `duration-120` | `Button.tsx` | Transitions ran at 150 ms, not the documented 120 ms |
  **Cause:** the palette maps to bare `var(--rg-*)` strings with no `<alpha-value>`, so Tailwind cannot synthesise alpha and **drops every `/opacity` utility on a token silently.** Fixed with named row tokens (`--rg-critical-row`, `--rg-followup-row`, `--rg-brand-line`) — a tint is a design decision, so it gets a name rather than arithmetic.
- 🔴 **20 × `border-slate-100` across every data table** — `#f1f5f9` is near-white, so in dark mode every grid in the product had glowing white hairlines. The single most visible reason the UI read as unfinished. → `border-line`.
- 🔴 **`bg-brand text-white` failed WCAG AA in dark mode** (`--rg-brand` is `oklch(70%)` there, ≈2.3:1) on the primary button, the danger button and the ExplainPanel ordinal. → `text-ink-inverse`, which flips with the theme. **6 sites.**
- 🔴 **`text-2xs` was 11px** — below the floor for anything clinical. Removed from the config entirely.
- ✅ **A contrast gate that measures rather than claims**: `web/e2e/contrast.spec.ts` walks the real rendered worklist in **both** themes, resolves every colour with `getComputedStyle` (the browser converts OKLCH→RGB for free, so no colour library), and computes WCAG ratios in six lines. It asserts a **minimum number of nodes measured**, so a changed selector reports red rather than a confident green over nothing.
- ⚠️ Severity was encoded in **two** channels only (colour + word) and the `-subtle` fills sit within 4 lightness points, so **at 2 m, or in greyscale, critical and normal were indistinguishable.** Being re-encoded across five redundant channels.

### 2026-09-15 — ✅ Phase 8.6 Explain UI shipped; three real defects found by writing its E2E

- ✅ **The Explain panel renders a verified explanation against real guidance.** Proven end to end: `phase8-explain.spec.ts` clicks Explain on a ceftriaxone-resistant *E. coli* case, waits on a real ~14 s call to NODE B, and asserts the highlighted quote is **≥ 20 characters, shorter than its surrounding passage, and actually present in a stored `kb_chunks` row**. Not a screenshot — a check that the verifier's offsets line up with the source.
- ★ 🔴 **The admin kill switch did not stop Phase 8 generation.** `/explain` never read `llm_enabled`; the only thing consulting it was the liveness probe. An admin could switch inference off, watch the health badge go grey, and the Explain button would go on calling NODE B. Phase 5 sells that switch as the lever you pull "in one click at 3am". **Fixed** — resolved from the settings table before NODE B is dialled, so it takes effect on the next request, not the next restart.
- ★ 🔴 **A dead NODE B took the retrieved guidance down with it.** 8.6 requires the panel to fall back to the raw chunks, and the response model said so in a docstring — but **no field ever carried them**, so every failure path returned `evidence: []`. Retrieval is entirely NODE A, so the outage costs the paraphrase and nothing else; the hospital's own text was in the knowledge base the whole time. `retrieved` is a **separate type** from `evidence` (no `quoted_text`, no `match_ratio`) because those words mean "a model said it and the verifier confirmed it".
- 🔴 **`POST /api/kb/documents` returned 500 for an unknown `publisher` or `doc_type`.** Both were unvalidated `str`, so anything `ck_kb_documents_publisher` refused surfaced as an unhandled `IntegrityError`. An admin who typed "NICE" instead of "nice" was told the server was broken. Now `Literal`s mirroring the `CHECK` → a 422 that names the allowed values.
- ⚠️ **Both of the first-draft regression tests passed against the unfixed code.** Recorded because it is exactly the failure CLAUDE.md warns about and it happened **twice in one hour**: the vocabulary test read the standalone `Publisher` alias instead of `IngestRequest`'s field, and the 422 test allowed a 401 and got one, because authorisation runs before validation. Both were rewritten and then **made to fail on purpose** before being trusted. All three fixes are red-then-green.
- ✅ **Phase 8 had no E2E at all; it has four now**, and three assert that *nothing* is shown — NODE B off, no approved guidance, and an unapproved document that stays invisible although its text sits in the same table as the approved copy. The happy path needs a model actually loaded on NODE B, so it **skips with the reason** rather than mocking the thing under test.
- 🔍 **Independent corroboration of the reboot finding below.** At the start of this session NODE A's health said `llm.reachable: true` while NODE B's `/api/tags` returned **`{"models":[]}`** and `mistral:7b` answered *"model not found"*. Same failure Ashmit then reproduced by rebooting. ⚠️ **A reachable NODE B is not a usable one** — `/api/health` opens a socket, and an Ollama serving an empty model directory passes that check. The Phase 8 E2E therefore asks **NODE B directly** whether the generation model is loaded, rather than trusting NODE A's probe.
- 🔧 **The knowledge base is emptied by any full `pytest` run** — `test_migration_round_trip` drops and recreates `kb_documents`/`kb_chunks`. Correct behaviour for the test; it means a KB populated by hand vanishes the next time anyone runs the suite. **[`api/scripts/seed_kb_demo.py`](api/scripts/seed_kb_demo.py) makes it one command.** ⛔ Its document is labelled a **fabricated demo fixture** in its title, its `source_ref` *and* its publisher — it is not clinical guidance and closes no gate.
- 🔴 **Phase 8.6 is 🔵, not ✅.** Three items are genuinely unbuilt and named in the phase doc: source click-through (blocked — 8.1 ingests text, so there is no page image), `ai_feedback` (**the table does not exist, and 8.7 needs it**), and per-case response caching (every click is a fresh ~14 s call).
- ✅ **Full gate green, checked by exit code:** ruff `0`, black `0`, mypy `0` (136 files), backend pytest `0`, frontend `tsc` `0` · 187 vitest · build `0`, and the **whole E2E suite 46 passed / 2 skipped** — so THE ONE RULE holds, Phases 1–6 still pass with Phase 8 present.
- ⚠️ **`black --check` piped through `tail` reported success while black was reporting a file to reformat** — the shell returned `tail`'s exit code. Same trap as the ruff incident, caught this time because the exit code was read separately. The CLAUDE.md rule earned its place again.

### 2026-09-15 02:45 — 🔴→✅ THE REBOOT TEST RAN AND FAILED. Fixed, and it corrected two claims below.

- 🔴 **The failure, exactly.** NODE B rebooted at 02:43. `ollama list` came back **empty** and `ollama ps` empty — a server was answering, so the models had not been deleted; it was reading the wrong path. **Nothing was lost: `D:\Nexus AI\.ollama\models` still held 10 blobs / 6.40 GB and both the `mistral` and `qwen3` manifests.**
- ⛔ **Correction 1 — "the tray app sanitises its child's environment" is wrong, and it was my claim.** The server's own config dump after the reboot shows **four of five variables arrived intact** — `OLLAMA_HOST`, `OLLAMA_KEEP_ALIVE` (`2562047h47m`, i.e. `-1`), `OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS`. Only **`OLLAMA_MODELS`** was replaced with `C:\Users\asus\.ollama\models`. An environment that delivers four of five is not sanitised. What is **measured** is that `ollama app.exe` overrides that one variable; **why is not known and is no longer asserted** anywhere in the repo.
- ⛔ **Correction 2 — the `shell:startup` shortcut was never actually verified.** It was "proved" by stopping Ollama and relaunching through the shortcut, which tested the **script** and nothing about the **trigger**. At the real login it lost the race: this machine starts OneDrive, Docker, Chrome, Edge, Epic and Adobe, and before the Startup folder was serviced an `ollama` command found no server and spawned **the tray app** — the one launcher that breaks `OLLAMA_MODELS`. Process times prove it: `ollama app.exe` created **02:45:17**, 100 s after boot, at the moment the command was typed. **Simulating a trigger tests the payload, not the trigger.**
- ✅ **Fix: a logon scheduled task**, `Result Guardian - Start Ollama`, 20 s delay, running the **same repo script** — not queued behind the Startup-folder backlog. The shortcut is kept as a second trigger; a duplicate start is harmless because the second `ollama serve` cannot bind 11434. Nothing installed, nothing elevated.
- ✅ **Verified by execution, not by simulation this time**: server stopped, port confirmed refusing, task run → `LastTaskResult 0`, **no `ollama app.exe`**, `/api/tags` lists **both** models, `ollama ps` → `mistral:7b · UNTIL: Forever`. Cold load **7.9 s**, warm **0.3 s**, model left warm.
- ✅ **Survived the reboot unchanged**: NODE B still `172.25.54.48`; firewall rule enabled, TCP 11434, remote `172.25.52.148` only.
- 🟡 **Still unproven: the scheduled task at a real login.** It has been run on demand, which is the same class of evidence that just failed. **It is 🟡 until a reboot shows `ollama list` populated with no command typed first.**

### 2026-09-15 — 🟡 Ollama set to start at login on NODE B — trigger later DISPROVEN, see the entry above

- 🔴 **The finding that prompted this: nothing started Ollama at boot at all.** All six mechanisms were searched on NODE B — `HKCU\Run`, `HKLM\Run`, `WOW6432Node\Run`, both Startup folders, scheduled tasks, Windows services. **Ollama was in none of them.** What had been keeping it alive was a manual `ollama serve` in a shell that happened to carry the right variables. So the failure after a reboot was never "the models reverted to `C:`" — it was **connection refused, no server at all**.
- ⛔ **The registry could not have fixed it, and that was already proven here.** `OLLAMA_MODELS` sat at **Machine** scope for four days while the tray app went on reading `C:\Users\asus\.ollama\models`; `server-1.log` (2026-09-11) records `total blobs: 0`. `ollama app.exe --hide --fast-startup` hands its child a sanitised environment. **Scope is not the mechanism** — the variables have to be set in the same shell that launches the server, which is what [`infra/nodeb/start-ollama.bat`](infra/nodeb/start-ollama.bat) does.
- ✅ **Shortcut to that script placed in `shell:startup`** (minimised, per-user). Nothing installed, nothing elevated; deleting the shortcut undoes it completely. The script is **tracked in the repo**, not a local copy, so it survives a re-clone.
- ⛔ **"Verified by taking Ollama down and bringing it back through the shortcut" — this was not a verification of the trigger, and the reboot 40 minutes later proved it.** Relaunching a shortcut by hand exercises the script, not the login path. See the 02:45 entry above.
- ✅ **Cross-node facts unchanged and re-measured**: NODE B `172.25.54.48` (Wi-Fi, gw `172.25.48.1`) — still what NODE A holds; firewall rule `Result Guardian NODE B` enabled, TCP 11434, remote scoped to `172.25.52.148` **only**; **0** inbound Block rules for `ollama.exe`.
- 🟡 **The real reboot test has NOT been run** — it needs the owner to restart the machine, and it must not happen during the demo. Until it does, login-time startup is verified by simulation only. **`qwen3:4b` was not deleted**; it is the evidence behind the model switch.

### 2026-09-15 — 🔵 Phase 8 core built: retrieval, generation, and the span verifier

- ⚠️ **Started with Exit Gate 7 OPEN, on the owner's instruction** — the corpus is still 0. **Exit Gate 8 cannot close either**, since it needs a clinician's judgement on generated explanations.
- ★ **8.5, the span verifier, contains no AI and never will.** A guard implemented with the thing it guards against is not a guard, so every decision is string comparison and the module has no import path to NODE B. It exists because of something **measured, not feared**: asked what a critical potassium level is, the live `qwen3:4b` answered *"6.0 mEq/L or higher"* — fluent, confident, and from **no source this system holds**.
- ✅ **Strict where it can afford to be.** Fuzzy 0.95 is for a re-typed dash, **not for paraphrase**; a quote under 20 characters verifies against nothing (*"the"* is in every document ever written); a chunk from a document whose approval was revoked between retrieval and verification is refused at **both** ends. **If every citation fails, the whole response is rejected** — not shown with fewer citations, because an explanation whose evidence all failed is confident prose with nothing behind it.
- ✅ **Every rejection is a row in `ai_rejections`, invented text kept verbatim.** A verifier that silently discards is indistinguishable from a model that never hallucinates. **Proven by disabling the verifier — three tests went red**, including the fabricated-quote one.
- ✅ **Retrieval degrades to keyword-only** when no embedder is installed. THE ONE RULE for one query: a missing model costs ranking quality, never guidance. Recorded as a deliberate deviation — bge-m3 plus torch is ~5 GB and unavailable over the campus link; the embedder is a drop-in with no code change.
- ✅ **Generation strips reasoning with `rsplit`**, because the model emits `</think>` with **no opening tag** and the usual regex matches nothing. Every failure path returns `None`: a timeout, a refusal, malformed JSON and an unreachable node all mean *no explanation shown*, with the flag beneath untouched.
- 🔧 **Two real defects in my own work.** The relevance floor was applied to the **fused RRF score**, which is meaningless — RRF is built from rank alone, so a perfect keyword hit and a useless one both score 1/61. Floors now sit on each half's own scale. And the chunk lookup bound a hand-built Postgres array literal that asyncpg cannot adapt; an expanding bindparam is the form that works.
- 🔴 **A third defect only CI could catch.** The test fixture set `approved_by` from `(SELECT id FROM users LIMIT 1)` — fine on a seeded developer database, NULL on CI's empty one, which left a non-NULL `approved_at` beside a NULL approver and `ck_kb_documents_approval_complete` refused it. **The constraint was right and the fixture was borrowing state it did not own.** Local runs could not have found this.
- ✅ **18 tests.** ruff, black, mypy strict, alembic check, the full suite and **CI** all green.

### 2026-09-14 — 🔵 Phase 7 started (knowing exception), 7.5 matching built

- ⚠️ **Started with Exit Gate 6 OPEN, on the owner's instruction.** Two of that gate's three clauses are measurements over **200 real PDFs** and the corpus is **0**. Recorded in the phase doc too, so the disagreement with the build plan's ordering rule stays visible rather than becoming an oversight. **Exit Gate 7 cannot close either** — it is measured on **100 labelled real documents**.
- ✅ **Migration `0014`** adds the five tables Phase 7 needs: `document_classifications`, `extraction_templates`, `loinc_terms`, `test_synonyms`, `match_decisions`. `unit_conversions` and `antibiotic_synonyms` already existed and are reused. **Round-trip 0014 → 0013 → 0014 clean; `alembic check` reports zero drift.**
- ✅ **7.5 matching is built, and there is no AI in it.** Pure arithmetic over 7.5's signal table, thresholds passed as arguments so a hospital can retune without a code change. `match_decisions` stores **the score, the method and every candidate** — not just the winner — so a wrong match is investigable rather than merely regrettable.
- ★ **The tie rule is the important part.** A sort always produces a winner; with two candidates at 0.90 any tie-break invents a distinction the evidence does not support and is **right about half the time**. Ties go to a human. **Verified by removing the rule and watching two tests go red** with `auto_matched` where `needs_review` belonged — the wrong-patient bug, caught.
- ★ **Signals are never summed.** A near-identical name plus matching birthday, test and date stays at **0.70** — below the auto threshold by construction — so no pile of weak coincidences can reach an auto-match without an identifier agreeing. **A missing collection date counts as disagreement**, or a dateless result would score against every open case for that patient.
- ✅ **15 tests.** ruff, black, mypy strict, full suite and the round-trip all clean — run with **the gate's own commands**, per the new CI rule.
- 🔧 **Two defects in my own work, found by the linters and fixed rather than suppressed:** a bare `dict` annotation mypy rejected, and a redundant CHECK written `(A) = (B AND A)` that says the same thing as the constraint beside it in a form nobody can read.

### 2026-09-14 — 🔴 OPEN DEFECT: a deadlock in the timer/closure race, found by CI

- 🔴 **CI has been red since `687bd98`, and I did not check it for eight commits.** Two separate causes, one fixed and one still open. **Everything passed locally throughout** — which is exactly why local green is not evidence.
- ✅ **Cause 1, fixed: `ruff` E501 in `api/scripts/`.** I had been running `ruff check app worker tests`; **CI runs `ruff check .` over the whole of `api/`**, which includes `scripts/`. My verification was narrower than the gate's. Now linted the way CI does — ruff, black and mypy strict all clean.
- 🔴 **Cause 2, STILL OPEN: `DeadlockDetectedError` in `test_closing_a_case_while_its_timer_fires_is_safe`.** 1 failed, 1048 passed. The failing statement is `mark_fired`'s `UPDATE sla_timers SET status='fired'`. This is the Phase 2 path that races a case closure against its own SLA timer firing — an ordinary production event, not a test-only situation.
- ❌ **My first diagnosis was wrong, and I am recording that rather than hiding it.** I theorised an FK-induced cycle: `sla_timers.case_id` references `pending_cases(id)`, so `close_case`'s `SELECT … FOR UPDATE` on the parent would conflict with the `FOR KEY SHARE` a child write needs. **Disproven by my own test** — it passed with the fix reverted, because **Postgres skips the parent FK check when the referencing column is not modified**, and `mark_fired` never touches `case_id`. The test was deleted rather than kept, since a test that cannot detect the defect is worse than none.
- **What has been ruled out so far:** no trigger on `sla_timers` touches `pending_cases`; `claim_timer_for_firing` and `mark_fired` write **only** `sla_timers` (read in full, not skimmed); the FK path above. **Not reproduced locally in 25 consecutive runs** — it needs CI's timing.
- ⚠️ **`close_case` now takes `FOR NO KEY UPDATE` instead of `FOR UPDATE`.** That is the correct lock for a transaction that never changes the row's key, and it genuinely reduces the lock footprint — **but it is explicitly NOT claimed as the fix**, and the code comment says so. Shipping an unverified fix with a confident comment is the fabricated-evidence failure mode this project exists to avoid.
- ▶ **Next step when this is picked up:** reproduce with `deadlock_timeout` lowered and `log_lock_waits = on` so Postgres prints the actual lock graph, rather than reasoning about it from the outside. The cycle involves two rows and two transactions; the server log will name both.

### 2026-09-14 — NODE B benchmarked before Phase 8, and it has less headroom than assumed

- ✅ **The GPU is genuinely doing the work.** Measured on NODE B: RTX 3050, **2,939 MiB of 4,096 MiB VRAM**, 37 % utilisation while answering, model resident (`UNTIL: Forever`). 31.1 tok/s locally, **26.0 tok/s measured from NODE A** across the LAN.
- ⚠️ **`ollama ps` reports a 29 % CPU / 71 % GPU split** — the model is 4.2 GB and the card holds 4.0 GB, so part of it runs on the CPU. **`qwen3:4b` is still the right choice** (8B does not fit at all), but this is the ceiling for this hardware and will not improve without a different card.
- 🔴 **The Phase 8 30-second budget has only 27 % headroom, and that is measured.** A realistic request — one ~60-word retrieved chunk, *"two sentences, do not add facts"* — took **21.9 s** and 569 tokens. **Reasoning was 2,361 characters; the answer was 222.** The model spends roughly nine tenths of its output thinking and every token is discarded. 8.1 s of margin on a *short* chunk is a coincidence, not a design.
- 🔴 **`think: false` does not work, and the usual strip silently fails.** Neither `/api/generate` nor `/api/chat` suppresses the monologue, and `/no_think` returns **empty content**. The model emits `</think>` **with no opening `<think>`**, so the paired-tag regex every tutorial uses matches nothing and passes the whole monologue through. `rsplit("</think>", 1)[1]` is what works; both are measured side by side in the phase doc.
- ⚠️ **Asked what a critical potassium level is, the model answered "6.0 mEq/L or higher"** — fluent, plausible, and from **no source this system holds**. That is the unverified clinical claim CLAUDE.md forbids reaching a clinician, and it is the clearest argument yet for the span verifier being plain code with no AI in it.
- ✅ **The grounded answer was faithful** — given a source chunk it added no facts and stayed inside it. The approach is sound; the **cost** is the open question. Four options are written into 8.4 for the owner to choose between, including using a non-reasoning model, since this step is phrasing retrieved text rather than reasoning.
- **Phase 8 remains ⬜ not started.** Nothing was built; this is preparation done while the link was fresh rather than discovered halfway through 8.4.

### 2026-09-14 — 🏁 EXIT GATE 0 IS CLOSED. All four clauses verified.

- ✅ **The gate that has been open since the project began is closed.** All four clauses now carry measured evidence, not intent. Phase 0 moves 🔵 → ✅.
- ✅ **And it recovered on its own — the gate closes on both edges.** NODE B restarted Ollama and **nothing was done on NODE A**: within ~6 s, `reachable: true`, **32 ms**, `degraded_features: []`, with `qwen3:4b` and `mistral:7b` both served again — confirmed **from inside the API container**. **Degradation and recovery are both automatic:** no restart, no config change, no manual re-enable. A transient NODE B outage costs prose generation for its duration and nothing else, exactly as the degradation ladder promises.

### 2026-09-14 — ✅ RULE 2 PROVEN BY THE DELIBERATE TEST. Exit Gate 0 clause 4 closed properly.

- ✅ **The literal scripted step finally ran.** Ollama was deliberately stopped on NODE B at **18:44:41**, **with both machines on one LAN and the path proven working seconds earlier** — so this is the scripted test, not a network outage standing in for it. The caveat that has qualified this clause since 2026-09-14 morning is **discharged**.
- **NODE B killed three processes, tray app first** (`ollama app.exe` 20244, then servers 2064 and 25640). **Killing the tray first is the part that matters** — otherwise it relaunches the server within seconds and you test a live NODE B while believing it is dead. Zero processes remained; port 11434 stopped listening.
- ✅ **Measured on NODE A with NODE B confirmed dead:** `/api/health` → **HTTP 200** (not 503), `status: "ok"`, `llm.reachable: false` with `error: "ConnectTimeout"` — **the reason reported, not swallowed** — `degraded_features: ["llm_generation"]` **and nothing else**, `worker_heartbeat_age_s` 3–5 s.
- 🟢 **The whole backend test suite is green with NODE B dead — pytest exit code 0.** And the strongest evidence is in the worker log *during* the outage: `{"queue":"sla_timers","event":"message_handled"}` and `{"queue":"classify","event":"message_handled"}`. **Phase 2's SLA timers fired and Phase 3's classification ran while the inference node was switched off.** That is RULE 2 as a measurement rather than an intention.
- ⚠️ **Noted, not fixed:** the error is `ConnectTimeout`, not `ConnectionRefused` — with no listener behind an allow rule Windows drops rather than resets. So **"NODE B is powered off" and "NODE B is unreachable" remain indistinguishable at this boundary**, the same ambiguity seen when the machines were on different networks.
- 📏 **New CLAUDE.md rule: "A config value is not verified until something has READ it."** Added after **the same defect class appeared three times in one day** — NODE B's `OLLAMA_MODELS` never reaching the server (`mistral:7b` invisible for 3 days), NODE B's disconnected adapter answering `/api/tags` to itself, and NODE A's `.env` holding the old IP while the runbook asserted otherwise. It is *"written ≠ done"* one layer down, and harder to catch because the value **is** there when you look at it. Rule of thumb now recorded: ask the **consuming process** (`docker compose exec <svc> printenv VAR`), never the file — and **never test an endpoint from the machine that serves it, or a container's path from the host.**

### 2026-09-14 — 🟢 THE TWO NODES ARE CONNECTED. Measured on NODE A, all three layers.

- ✅ **NODE A → NODE B works end to end.** `Test-NetConnection 172.25.54.48 -Port 11434` → **`TcpTestSucceeded: True`** (`PingSucceeded: False`, as expected — Windows blocks inbound ICMP). From **inside the API container** — the only path that actually counts — `httpx.get('http://172.25.54.48:11434/api/tags')` → **HTTP 200**, `qwen3:4b` 2.5 GB + `mistral:7b` 4.37 GB. And the application's own view: `curl localhost/api/health` → **`"llm":{"reachable":true,"latency_ms":36}`** with **`"degraded_features":[]`** — empty for the first time in this project.
- **What actually unblocked it:** NODE B removed the two auto-created `ollama.exe` inbound **Block** rules (a Block rule beats an Allow rule) and added the Phase 10.1 allow rule scoped to `172.25.52.148` only — `e523ee8`. `OLLAMA_HOST` needed no change; Ollama was already bound to `0.0.0.0`.
- ⚠️ **A stale-config defect was found doing this, and it would have wasted another hour.** `.env` still carried `RG_LLM_BASE_URL=http://192.168.0.168:11434` — NODE B's old *home-network* address — so the first health check after the firewall fix still returned `reachable: false` with `ConnectTimeout`, **looking exactly like a firewall that had not worked**. The runbook asserted `.env` was "already set"; it was not. Fixed, containers recreated, then `reachable: true`. ▶ **A config file is not verified until something has read it** — same class of defect as NODE B's stale IP answering `/api/tags` to itself.
- 🔴 **Exit Gate 0 clause 4 is STILL only closed by outcome, not by the literal step.** The deliberate version — **stop Ollama on NODE B while NODE A watches, both machines on one LAN** — is now **possible for the first time** and has **not been run**. Ashmit is standing by. Until it runs, the clause stays qualified in the phase doc.

### 2026-09-14 — D-N1 proven by execution, and the RCR national list retrieved in full

- ✅ **D-N1 is no longer a reading-level claim — it is an executed, reproducing defect.** `test_a_conjunction_terminates_negation_scope` in `api/tests/test_rules_numeric_narrative.py` runs `No evidence of fracture, however a large abscess in the liver.` and reports **XFAIL**: the abscess really is suppressed. Marked **`xfail(strict=True)`**, so **the day the defect is fixed the test FAILS and forces the marker's removal** — CI stays green meanwhile, and the defect cannot quietly stop being tracked. **The fix is deliberately NOT applied:** adding `CONJ` terminators changes clinical behaviour and belongs to the 3.8 clinician review, not to an engineer.
- ✅ **The safety floor is now locked by a passing test.** `test_a_wrongly_negated_finding_still_reaches_a_human` asserts a wrongly suppressed critical finding still returns **FOLLOW_UP, never NORMAL**. This is what stops NegEx's ~15% false-negation rate becoming a missed result, and it must never be weakened. **190 Phase 3 tests pass, 1 xfail.**
- ✅ **The published spec confirms the diagnosis precisely.** Chapman's own ConText paper (PMC2757457, fetched): NegEx's scope is *"a window of six tokens … **If any of these six tokens is a termination term** … the scope ends at that point."* **Our six-word window matches NegEx — we implemented the bound and omitted the escape.** That also settles the 5-vs-6 dispute in our favour: 6 is right.
- ✅ **NegEx performance now verbatim from the Europe PMC abstract**, not second-hand: *"specificity of 94.5% … positive predictive value of 84.5% … sensitivity of 77.8%."* **NPV is not reported — do not assert one.**
- 🟢 **The RCR/AoMRC Oct 2022 national list was retrieved IN FULL (37-page PDF, read), and it corrects what we assumed.** The categories are **not** "critical / urgent / unexpected significant" — that triad is the RCR *quoting the HSIB recommendation it answers*. The RCR's own answer is **three alert codes: `CANCER`, `CRITICAL`, `ADDITION`**, plus **Table 1's 43 named critical conditions** (explicitly *"not intended to be definitive"*). Cancer is confirmed **orthogonal**: *"A single CRITICAL alert can be triggered or in combination with a CANCER alert."*
- 🔴 **Biggest surprise: the RCR gives NO per-category timeframes.** We assumed minutes/hours/days tiers. It has **one rule for all three categories — alert "immediately upon completion of the report"** — and puts the only numeric interval on **escalation**: *"A 48-hour interval before escalation is reasonable."* **Urgency is expressed by adding a channel, not by shortening a timer:** *"the alert should be supplemented by direct verbal communication."* Two further clauses map onto our design: the **referrer may redirect** an alert (our ownership model), and patient-facing release is **embargoed until the referrer authorises it**.
- 🔴 **Two documents are now confirmed UNOBTAINABLE, not merely unfetched.** **Larson 2014** (the ACR Category 1/2/3 table): `jacr.org` returns **HTTP 403** on fulltext, abstract and PDF, and Europe PMC confirms `pmcid: null`, `isOpenAccess: "N"`. **Fleischner 2017**: `inPMC: "N"`, `isOpenAccess: "N"`, no OA mirror. Both need institutional or purchased access. ⚠️ **The ACR Category 1/2/3 table in the research doc therefore remains `[SEARCH]` reconstruction and must not be treated as sourced.** Fleischner's full 15-author list was confirmed.
- **Permission fix that unblocked all of this:** the WebFetch denials were never a bug — `~/.claude/settings.json` simply had no journal domains on its allow-list, so every `ebi.ac.uk` / `pmc.ncbi.nlm.nih.gov` / `jacr.org` call was denied at the door. Eight domains added on the owner's explicit instruction.

### 2026-09-14 — Literature research on the Phase 3 rule engine → [`docs/clinical-rule-research-findings.md`](docs/clinical-rule-research-findings.md)

- ⛔ **This is NOT clinical validation and Exit Gate 3 is UNCHANGED.** No clinician has reviewed anything. **No rule, threshold, severity or keyword was modified** — per the standing constraint in CLAUDE.md. Everything below is recorded as evidence and as questions for the 3.8 review.
- 🟢 **The product's problem statement now has a citation, and it is the most valuable thing found.** McDonald et al., *Acad Radiol* 2017 (PMID 27793580): of 510 patients with incidental pulmonary nodules, **only 39% received their recommended follow-up**; 67% of the non-adherent never got the scan at all. Putting the recommendation *in the report text* moved adherence 31% → 45% — **that is the ceiling of better reporting, and it is why tracking is the product.** And Zaki-Metias et al., *J Digit Imaging* 2023 (PMID 36759382): a follow-up tracking programme took ED completion from **19.2% → 55.0%**. ⚠️ **That is also the honest warning — ~45% still failed after the intervention. "Zero missed results" is not a supportable claim.**
- 🔴 **There is no national or international consensus list of laboratory critical values** — quoted directly, and **ISO 15189 clause 5.8 (which NABL applies in India), CLIA and the Joint Commission all require each lab to define its own.** Real accredited labs disagree by **15 mmol/L on sodium low (110–130)** and MGH is documented changing its own glucose threshold 60 → 45 mg/dL. **The spread is the finding; do not average it.** ▶ **Consequence: `panic_thresholds` must NOT be seeded from literature.** The `DEVELOPMENT PLACEHOLDER - NOT A CLINICAL SOURCE` tag stays until the hospital's own lab-director-approved list arrives — that document is the correct clinical *and* legal source, and they are obliged to already have it.
- 🔴 **Troponin cannot have a universal threshold, by construction.** It is the 99th-percentile URL of *the specific assay on the specific analyser*, reported sex-specifically. A Roche hs-cTnT value is simply wrong for an Abbott hs-cTnI analyser. Schema implication: key it to *(analyser, assay, sex)*.
- ⚠️ **Keyword audit: 7 of 18 seeded terms have a published anchor; 6 have none** (`septic`, `obstruction`, `consolidation`, `lesion`, `effusion`, `abscess`). The **malignancy family's `critical` severity is contradicted** by ACR's Actionable Reporting Work Group, which places probable malignancy in **Category 3 — days**. Over-flagging is the *safe* error, so this is an alert-budget question for the clinician, not a defect to fix unilaterally.
- 🔴 **Schema-level finding, bigger than any keyword.** We have one severity axis (`critical | follow_up`). **ACR is 3-tier by time-to-decision; RCR is 3-tier plus an independent CANCER axis; CAP/ADASP 2012 says pathology needs a framework separate from radiology** — and we apply one keyword table to both. We also cannot express a **qualified** term, yet *tension* pneumothorax vs pneumothorax and *intracranial* haemorrhage vs haemorrhage are exactly where ACR splits Category 1 from Category 2.
- 🔴 **Negation is a safety-critical suppression gate and its error runs the wrong way.** NegEx's published PPV is **84.5%** — so ~15% of things it calls negated are actually asserted, and our engine *discards* negated hits. **Negation precision, not sensitivity, is the metric that matters here.** What saves us is the **FOLLOW_UP floor**: all-hits-negated still returns `NARR_ALL_HITS_NEGATED` at FOLLOW_UP and reaches a human. **That floor is load-bearing safety, not a nicety — a later phase must never route around it.**
- 🟡 **Defect D-N1, by reading only, NOT executed.** `narrative.py` counts negation scope in words with **no conjunction termination**, while both the shipped NegEx and ConText terminate on `but`/`however`/`although`. `No evidence of fracture, however a large abscess` puts "abscess" ~5 words out, inside our 6-word window → **suppressed**. Needs a test that goes red before anyone believes it. Also: **our 3/5/6-word windows have no citation** — sources say six (Harkema 2009), five (Wu 2014), and the shipped NegEx code has **no token counter at all**.
- ❌ **A research claim that did NOT survive checking, recorded so it cannot come back.** The agent reported gold case **C04 "passes for the wrong reason."** It does not — C04's own description says it tests *unbounded negation scope*, and it tests that correctly. **The test is honest; the real gap is that no test covers conjunction termination.** Verified by reading the fixture and `narrative.py` rather than trusting the report.
- 🔴 **Exit Gate 3 as written cannot be demonstrated at n=100, and this is the most consequential finding.** *"≥95 % agreement"* over 100 cases gives a 95 % Wilson CI of **[88.8 %, 97.9 %]** — an observed 95 % is statistically indistinguishable from 89 %. You would need ~**99 %** observed before the lower bound reaches 95 %. **The ≥95 % level is the build plan's and is "not negotiable downward" — nothing here lowers it, and nothing here may be used to lower it.** The ask is to restate the gate as a **confidence bound** ("lower bound of the 95 % CI on critical-class sensitivity ≥ 90 %"), which is a claim the data can support. **Needs an ADR and the owner's decision — recorded as D1, not acted on.**
- 🔴 **The rule of three hits Exit Gate 5 too.** Hanley & Lippman-Hand, *JAMA* 1983: with zero events in n trials the 95 % upper bound is **3/n**. **Two weeks of shadow-running with zero misses does not establish "zero missed cases"** — at 60 tracked cases the honest claim is *"miss rate below 5 % with 95 % confidence."* That clause's measurement method needs rewriting (**D2**).
- ⚠️ **Sensitivity, not agreement, is the binding constraint — and the gold set is not sized for it.** Sensitivity rests on the count of *truly-critical* cases, not on 100. **≥35 critical cases are needed for a ≥90 % sensitivity lower bound**, 73 for ≥95 %. Our A≥30/B≥40/C≥25 minimums are a **coverage** plan, not a **power** plan (**D3**).
- ⚠️ **One clinician is not defensible.** A published multicentre ED study found clinician-vs-clinician agreement on urgency was only **moderate (κ = 0.43)** — so "95 % agreement with the clinician" is measured against an unquantified yardstick. Recommended: **two blinded raters + a senior adjudicator for discordant cases**, with clinician-vs-clinician agreement reported as a named result — if it is below our engine-vs-clinician figure, **that is the ceiling on what any classifier could achieve** (**D4**). Blinding is mandatory, not optional: unblinded raters anchor on the engine and the agreement rate becomes self-fulfilling (automation bias, Goddard *JAMIA* 2012).
- 🔴 **There is no published minimum sensitivity for clinical alerting, and no published false-positive rate at which clinicians disengage.** A 2026 JAMIA review of 22 systematic reviews found **only 1 defined alert fatigue operationally**. **Inventing either number would be exactly the fabricated evidence CLAUDE.md forbids.** The defensible route is Pauker & Kassirer's threshold approach: **state the miss : false-alarm cost ratio as an explicit recorded decision** (**D5**). Our "~15 % flag rate" retune trigger is an internal project figure with **no published basis** — keep it, but label it as a project decision.
- 🟢 **The evidence endorses our review-queue-first architecture.** Singh/Murphy's post-discharge EHR safety-net triggers — the closest published analogue — were **published and endorsed at 58–71 % PPV**, i.e. 30–40 % false positives, **because they feed a review queue rather than an interruptive modal.** Same accuracy, opposite acceptability, decided by the route. And the canonical alert-fatigue review (van der Sijs, *JAMIA* 2006) names **"low sensitivity" as an error-producing condition alongside low specificity — alert fatigue is not a licence to raise the miss rate.**
- ⚠️ **Reporting standards: STARD 2015 + GRRAS are co-primary; three AI checklists are out of scope and one is superseded.** GRRAS item 13 makes **a bare "95 % agreement" a checklist failure** — statistical uncertainty is required. **TRIPOD 2015 is superseded by its own authors**; **TRIPOD+AI's full text contains zero hits for "rule-based"/"deterministic"/"expert system"** and its glossary defines ML as learning *"without being explicitly programmed"* — claiming compliance would be a scope overclaim. **DECIDE-AI is the right standard later, for NODE B's live inference path.**
- ⚠️ **Implementation constraint worth knowing before anyone writes the statistics:** the κ/CI code **cannot live in `test_gold_set.py`** — `test_no_agreement_rate_is_reported_for_synthetic_cases` parses that module's AST and fails on **any division** and **any name containing `rate`/`pct`**. That guard is deliberate and stays; the arithmetic goes in a separate module.
- 🔴 **Our data-protection premise is out of date, and the currently-binding regime is the stricter one.** **The DPDP Rules, 2025 were notified 13 Nov 2025** (not still draft), but commencement is phased — **the research exemption (Rule 16) only takes effect ~13 May 2027.** Until then the **IT (SPDI) Rules 2011 remain in force alongside**, and they expressly classify *"medical records and history"* as sensitive personal data — **a class the DPDP Act does not have at all.** Also: **there is no Indian statutory de-identification standard** — no Safe Harbor equivalent, no identifier list, no definition of "anonymisation" (the 2019 Bill had one; it did not survive into the 2023 Act). **"We anonymised it, so DPDP doesn't apply" is a legal argument, not a safe harbour.** Any standard we adopt is adopted *voluntarily*; HIPAA must never be described as compliance we are discharging.
- ⚠️ **Two protocol gaps found: DICOM, and self-exemption.** Our de-identification covers text and barcodes but **not DICOM headers or burned-in pixel text**, which survives every header scrub and is endemic in ultrasound (**D8**). And **ethics exemption is granted by the Ethics Committee, never claimed by the investigator** — the QI framing likely fails the generalizability test, since a gold set characterising sensitivity for deployment beyond one unit is *designed to produce generalizable knowledge* (**D7**).
- ⚠️ **Evidence quality is mixed and is tagged per-claim in the doc.** `WebFetch` was blocked for most domains and the search budget was exhausted, so the ACR/RCR category content is **search-summary reconstruction, not a verbatim read**. Fleischner's tables were obtained second-hand via NCBI Bookshelf; **the primary paper is still unread**. The three highest-value unread documents are the **RCR/AoMRC Oct 2022 national critical-findings list**, **Larson 2014's actual table**, and the **CAP Q-Probes threshold tables** (paywalled).

### 2026-09-14 — Cross-node blocker identified: it was never the network

- **The "different networks" blocker logged below is RESOLVED.** NODE B joined `Studentwifi_5G` and is now `172.25.54.48/20`, gw `172.25.48.1`. NODE A is unchanged at `172.25.52.148/20` on the same gateway — **one subnet, no hotspot, no cable.**
- **Two assumptions in that entry were wrong, and both would have cost an hour.** (1) *"Campus SSIDs usually run client isolation"* — `Studentwifi_5G` does **not**; TCP from NODE B to NODE A succeeded on ports 80 and 8000. (2) *"`ping` and TCP:11434 both fail"* treated a silent `ping` as evidence — **Windows blocks inbound ICMP by default, so `ping` is a false negative between two Windows hosts.** The runbook's own "test with `ping`" advice was the thing pointing the wrong way; it has been replaced with `Test-NetConnection -Port` throughout.
- 🔴 **The actual blocker, found on NODE B: two auto-created `ollama.exe` inbound *Block* rules** on the Public profile, made by Windows when the first listen prompt was dismissed. **A Block rule beats an Allow rule**, so the Phase 10.1 allow rule would have changed nothing and looked like a network fault. The fix is two steps — remove the blocks, *then* allow 11434 from `172.25.52.148` only — and the old `netsh` one-liner has been removed from the runbook because it was the insufficient half.
- **A stale-IP defect in `infra/nodeb/setup-windows.ps1`, fixed on NODE B** (`2825a21`). It picked the first non-loopback IPv4, which on a disconnected Ethernet adapter was a stale static `172.18.30.66` — and **that address answered `/api/tags`**, because a request from a machine to its own address never leaves the box. The check read green while NODE A could never have reached it. Now filters on adapter `Up` + default gateway, and warns about every address it skipped. `ipconfig | Select-String IPv4` is banned in the runbook for the same reason.
- **Verified from NODE A**, both nodes on one subnet: `Test-NetConnection 172.25.54.48 -Port 11434` → `TcpTestSucceeded: False`. Consistent with the Block rules still in place. **This is the one outstanding action, and it must run elevated on NODE B.**
- 🟡 **Nothing about Exit Gate 0 changes yet.** Clause 4's deliberate "stop Ollama" run still needs a working path first, and the path is still blocked. Reachability is unproven until `TcpTestSucceeded` is `True` from NODE A.

### 2026-09-14 — Exit Gate 0: three of four clauses verified on NODE A

- **Full stack up on NODE A and healthy.** `docker compose up -d` → `alembic current` at `0013` → `curl localhost/api/health` through Caddy → **200**, `db: "ok"`, `worker_heartbeat_age_s: 1`. That closes gate clauses 1 and 2, and finishes the "stack never run" caveat that has sat in this file since Phase 0.
- **RULE 2 demonstrated at the HTTP boundary.** With NODE B unreachable: still **200**, `status: "ok"`, `llm.reachable: false`, `degraded_features: ["llm_generation"]` and nothing else. The whole 1048-test backend suite also passes with NODE B unreachable, which is the same property asserted much more broadly.
- ⚠️ **But not the literal test, and it matters.** NODE B was unreachable because the two machines were **on different networks**, not because Ollama was stopped. At the HTTP boundary those are indistinguishable, and losing the network path is arguably the stronger test — RULE 2 is about the network between nodes — but the deliberate "stop Ollama" run still has to happen. Recorded in the gate itself, not just here.
- ⛔ **SUPERSEDED — see the entry above, dated the same day. Both conclusions in this bullet turned out to be wrong.** ~~🔴 **The two machines are on different networks — this, not the firewall rule, is the blocker.**~~ NODE A `172.25.52.148/20` gw `172.25.48.1` (SSID `Studentwifi_5G`); NODE B `192.168.0.168/24` gw `192.168.0.1`. `ping` and TCP:11434 both fail. **A firewall rule scoped to NODE A's current address would achieve nothing and would be stale** — NODE A was on `192.168.0.156` earlier the same day and has since moved to campus Wi-Fi. Campus SSIDs also usually run client isolation, which this runbook already calls *"the single most common wasted hour"*. Options and their trade-offs recorded in [`docs/network-runbook.md`](docs/network-runbook.md).
- **Clarified an ambiguity in gate clause 4.** *"and no error"* means the endpoint does not error — not that `llm.error` is absent. `tests/test_health.py` deliberately asserts the reason **is** populated (*"the reason is reported, not swallowed"*). A health endpoint that hides why NODE B is missing is worse than one that says so.

### 2026-09-14 — Phase 6 document ingestion, built and run end to end

- **⚠️ Started as a knowing exception** with Exit Gate 5 open, on the project owner's instruction — recorded above. **Exit Gate 6 stays 🔴 OPEN**: two of its three clauses are measurements over 200 real PDFs and the corpus is at **0**. Synthetic PDFs are unit-test fixtures and are **not** gate evidence.
- **Migrations `0012` and `0013`.** 0012: `documents` (sha256 UNIQUE — dedup falls out of content addressing), `document_pages` (`is_scanned` **per page**, not per document), `document_spans` (char offsets + bbox, because *"Phase 8's span verifier is impossible without them"*). 0013: `audit_log.entity_type` gains `document`. `alembic check` reports **drift 0**.
- **The span invariant holds** — `text_layer[char_start:char_end] == text`, asserted in memory, after a Postgres round-trip, and end to end over HTTP. Without it every citation the product ever shows would point slightly to the left of the value it claims to quote.
- **Native path, scanned path, and the mandatory fallback all run.** Real PaddleOCR reads a scanned A4 page at **0.997** confidence. Every failure — encrypted, corrupt, timed out, OCR absent, child killed — ends with a document in the review queue with its pages rendered beside the Phase 3 manual-entry route. **The workflow never stalls because parsing failed.**
- **🔴 The most serious finding: one unreadable PDF was taking Phase 2 and Phase 4 down with it.** A 15-megapixel render drove the worker into its memory limit; the kernel SIGKILLed the process — which also runs the SLA-timer and notification consumers. pgmq redelivered, the process died again, **16 times**, and the DLQ was never reached because the dead-letter check only ran when the handler *raised*, and a process that is killed never raises. Fixed three ways: dead-letter **on arrival** past the attempt limit; **cap renders at 4.2 MP**; and **move extraction into a child process**, so a dead child costs one document instead of the escalation ladder.
- **The 120s budget did not fit a scanned page** — 115.4s at a 2-CPU quota, so every scan would have timed out into review and the feature would have been useless. Thread oversubscription was ruled out by measurement first (`OMP_NUM_THREADS=2` → 116.3s; it is simply CPU-bound). Worker raised to 6 CPUs: **115.4s → 38.7s**.
- **18 defects found and fixed**, each with a regression test; the three most serious were each **proven red before being fixed**. Nine were found only by running the thing end to end — including OCR being completely broken while every unit test passed, because the unit tests stub the engine out.
- **Gates:** ruff · black · mypy strict all clean; **1048 backend tests** and **187 frontend tests** pass; `tsc --noEmit` clean; full E2E exit 0.
- **Deviation:** Docling replaced by PyMuPDF `find_tables()` — Docling pulls ~2.5 GB of PyTorch into an image that must run air-gapped on a CPU-only server, and its output would need fuzzy alignment back to our span map, which is the exact failure mode `native.py` exists to prevent. Recorded in the phase doc.
- ▶ Full record, every item and every defect: [`docs/build/phase-06-verification-log.md`](docs/build/phase-06-verification-log.md).

### 2026-09-14 — NODE B provisioned (Phase 0.3), on Ashmit's machine

Ran on `LAPTOP-5JCGN9SJ`. **6 of 8 items in 0.3 ticked; 0.3 stays 🟡** — the firewall rule and the from-NODE-A check both remain, and both block Exit Gate 0.

*State before, kept for rollback:* Ollama `v0.15.2` installed but **not running**; `OLLAMA_MODELS` user=`D:\Nexus AI\.ollama\models`, machine=`D:\nexus ai\.ollama\models`; **`ollama list` empty**; C: 37.7 GB free, D: 177 GB free; no firewall rule on 11434.

- Verified the machine before touching anything: `LAPTOP-5JCGN9SJ` / `asus`, RTX 3050 **4096 MiB**. LAN IP **192.168.0.168** (Wi-Fi, gw 192.168.0.1).
- **Found a pre-existing break.** `OLLAMA_MODELS` was set at User *and* Machine scope, but the server ignored it — running on the `C:` default with `total blobs: 0`. `server-1.log` shows the same on 2026-09-11, so **`mistral:7b` had been invisible for days** and the separate `D:\Nexus AI` project was silently broken. Cause: `ollama app.exe --hide --fast-startup` hands its child a sanitised environment.
- Ran `setup-windows.ps1 -ModelsPath "D:\Nexus AI\.ollama\models" -SkipFirewall`. Because it starts `ollama serve` **directly** from a shell carrying the vars, the settings took effect — **and repaired `mistral:7b` as a side effect.** `ollama list` now shows both models.
- **Verified:** `qwen3:4b` cached to D: (2.33 GB) · `/api/tags` → 200 on `localhost` **and** `192.168.0.168` · `ollama ps` → **`UNTIL: Forever`**, proving `KEEP_ALIVE=-1` propagated · generation answers in 1.7–7 s · blobs on `C:` = **0**, on `D:` = **10**, proof by construction that the server reads D:. After: C: 44.0 GB free, D: 175.1 GB free.
- **Nothing was installed.** Ollama was already present (check-first rule). `qwen3` turned out to be supported by v0.15.2, so no upgrade was needed — the updater advertises v0.34.0 and it was deliberately **not** taken, since another project depends on this install.
- Recorded NODE B's IP and the outstanding firewall command in [`docs/network-runbook.md`](docs/network-runbook.md).

⚠️ **Two risks, neither blocking today:**
1. **Durability untested.** The env fix depends on how `ollama serve` is launched. If Windows relaunches it via the tray app at next login it may revert to the `C:` default and hide `mistral:7b` again. **Re-check `ollama list` after a reboot.**
2. **`qwen3:4b` is a reasoning model.** `response` is empty by default while it emits `thinking`; it loads 29% CPU / 71% GPU (4.2 GB vs 4 GB VRAM). Phase 8.4's strict-JSON contract needs `think=false` + `format: json` and a token budget for the preamble.

> 📌 **Note on history.** NODE B had been committing on a parallel branch whose root differed from `origin/main`, so its local commits were duplicates under different hashes. It was reset to `origin/main` — NODE A's 41 commits are authoritative and untouched — and only this provisioning record was re-applied on top. **Nothing from NODE A was overwritten or force-pushed.**

### 2026-09-13 — Phase 5.1 → 5.7 and the MVP (implemented + audited, **not pushed**)

- **⚠️ Started with Exit Gate 4 passing but Exit Gate 3 still 🔴 open**, on the project owner's instruction, and **Exit Gate 5 is also 🔴 open** — see the last bullet. Phase 5 shows and closes whatever severity Phase 3 produced; it does not make those severities clinically correct.
- **Migrations `0010` and `0011`.** 0010: `sessions`, `audit_log`, `audit_anchors`, three `users` columns (`failed_login_count`, `locked_until`, `password_changed_at`), the `closure_reason` CHECK, `rg_audit_next_seq()` and the append-only trigger. 0011: `system_settings` plus `rg_verify_audit_chain()`, `rg_anchor_audit_chain()` and the 02:30 IST pg_cron job. Both round-trip; `alembic check` reports **drift 0**.
- **5.1 Auth.** Argon2id (RFC 9106 profile, library defaults), 15-minute access token, 12-hour refresh **rotated and stored SHA-256-hashed**; reuse of a rotated token revokes the whole family. Lockout at 5 failures for 15 minutes, counted **on the account, not the IP** — an attacker who rotates addresses walks straight past an IP-keyed counter. Every login failure returns **one message** for unknown-code / wrong-password / locked / deactivated; the audit log records which it actually was. Break-glass lifts department scoping, needs at least 20 characters of reason, and is logged with its own action, its own column and a partial index.
- **🔴 The audit's most serious finding: 31 clinical endpoints were reachable with no credential.** Every Phase 1–4 route. The dashboard was behind a login and the API was not — and a gated single-page app is not an access control. Anyone who could reach NODE A's port could read a patient record or discharge a patient. Fixed by applying `require_role` at `include_router`, which also covers routes added later. **`tests/test_phase_5_rbac_coverage.py` now sends a real unauthenticated request to every published endpoint and fails if any answers with anything but 401**, behind a five-entry allow-list that each has to justify itself in writing. An earlier version of that test inspected the dependency graph and *passed while the endpoints were still open*; the black-box version cannot be fooled that way.
- **🔴 A real concurrency bug in the audit chain, found by a test rather than by reading.** `append()` read `rg_audit_next_seq()` and the head `prev_hash` in one statement. Under READ COMMITTED the statement's snapshot is taken *before* the function inside it acquires the advisory lock, so a waiting transaction allocated a correct `seq` but read a `prev_hash` from before its predecessor committed — **the chain forked, with no duplicate `seq` to give it away.** Fixed by issuing the lock and the head read as two statements. Proven: reverting the fix makes `test_concurrent_appends_do_not_fork_the_chain` fail with `prev_hash does not match`; restoring it makes it pass.
- **5.5 The hash chain ★.** Canonical JSON is sorted-keys / no-whitespace / UTC ISO-8601, with `Decimal` as its string form and `ensure_ascii=False` so a Hindi note hashes the same either way. Tampering is detected and the three kinds are distinguished: a changed row (`row_hash` mismatch), a deleted row (`seq` gap), and a deletion with the numbering repaired to hide it (`prev_hash` mismatch — the attack that defeats the naive check). All three are tested by actually defeating the append-only trigger with `session_replication_role = replica` to set the tamper up, which is itself proof the trigger bites.
- **⚠️ Honest negative result: the `REVOKE` half of 5.5's DB rules is currently inert.** The grant applied correctly — the ACL reads `arxt`, with **no `w` and no `d`** — but `rg_app` is the bootstrap **superuser**, and superusers bypass every privilege check. The trigger (layer 1) and the hash chain (layer 3) are unaffected. `test_revoke_is_ineffective_against_a_superuser` asserts the *situation*, so the day the app stops running as a superuser the test fails and somebody re-reads the reasoning. A non-superuser application role is Phase 10 security hardening.
- **5.2 / 5.3 Dashboard and closure.** Worklist sorted CRITICAL-first then oldest **in the database**, keyset-paginated on `(severity_rank, opened_at, id)` — an OFFSET page 2 can skip a row whenever page 1 shrinks, and the row it skips is a case nobody then sees. Rule output is rendered into plain language by **plain code, never AI** (RULE 1): the plan's own worked example, *"Amoxicillin-clavulanate (discharge medication) is Resistant for E. coli"*, is asserted verbatim. Closure requires one of the five reasons **and** a real note; bulk close **refuses the entire batch if any case is CRITICAL** rather than closing the rest, because a half-succeeded batch invites a re-run without reading what was skipped.
- **5.4 / 5.6 Admin and reports.** The NODE B kill switch lives in `system_settings`, not the environment — an admin flips it in one click with a mandatory reason, no restart, effective within 10 seconds, audited. Panic thresholds **supersede rather than edit**, so a decision made last March is still explicable with the number that was in force last March. The NABH monthly PDF is written by ~200 lines in `app/services/pdf.py` with **no PDF library**, and carries a provenance block stating that the rule engine has **not** been clinically validated — an accreditation reviewer must not be able to read it as evidence of validation that has not happened.
- **5.7 Hardening.** Cursor pagination, `RG_AUTH_RATE_LIMIT_PER_MINUTE` (the number was hardcoded first — exactly what CLAUDE.md's *"configuration lives in tables, never in code"* guards against), input size caps on every auth field, empty / loading / error states on every screen, and **[`docs/runbook.md`](docs/runbook.md)** — whose commands were each run against the live stack, which caught one naming a function that does not exist (`rg_enqueue_due_timers` → `rg_sweep_overdue_sla_timers`).
- **Frontend.** Login, forced password change, worklist, case detail, audit viewer, admin and reports. The access token is held **in memory only** — never `localStorage`, which is readable by any script on the page and survives the tab being closed on a shared ward computer. Simultaneous 401s share **one** refresh rotation, because independent rotations present the same refresh token twice and the server correctly reads that as theft and revokes every session the user has.
- **Verification.** Backend **986 tests**, coverage **85%** (gate 70). Frontend **178 vitest**, typecheck and build clean. **37 Playwright E2E across all five phases**, including the full Phase 5 journey — login → worklist → case → close — asserted in `pending_cases`, `sla_timers` and `audit_log`, and the audit chain verifying clean after real application traffic. ruff, black and **mypy --strict** all clean.
- **THE ONE RULE held, and was tested rather than assumed.** The Phase 5 `closure_reason` CHECK initially broke Phase 2's `close_case`, which the full regression caught; `close_case` now validates before writing anything. Phase 4's *"acknowledging at any rung stops everything"* is re-asserted against the new Phase 5 endpoint. The delivery-receipt webhook keeps its Phase 4 behaviour by making the new shared secret **optional** — making it mandatory would break a working SMS integration on upgrade.
- **🔴 EXIT GATE 5 IS OPEN and cannot be closed by writing code.** Two of its five clauses are ticked (the audit chain verifies; NODE B was never switched on). The other three — two weeks of ward shadow-running, zero missed cases, and **clinician sign-off on flag quality** — need a ward, a fortnight and a clinician. That sign-off is the same ≥95% agreement requirement Phase 3 is still blocked on. **No clinician has reviewed the rule engine.**

### 2026-09-13 — Phase 4.1 → 4.7 and Exit Gate 4 (implemented + audited, **not pushed**)

- **⚠️ Started with Exit Gate 3 open**, on the project owner's instruction. Recorded as a knowing exception above, per CLAUDE.md's rule. Phase 4 routes whatever severity Phase 3 produces and does not depend on those severities being clinically correct — **Exit Gate 4 passing does not close Exit Gate 3.**
- **Migrations `0008` and `0009`.** 0008: `duty_roster`, `user_absences`, `escalation_chain`, `notifications`, `patient_contacts`, plus `sla_timers.escalation_level` and a sixth `timer_type`. 0009: the two `pg_cron` jobs that make 4.5's digest and ADR 0004's weekly roster reminder actual schedules rather than functions nobody calls. Round trip 33 → 28 → 33, drift 0, 0 PG enum types, no historical migration touched.
- **The ladder rides on Phase 2's timers rather than a new mechanism.** Every rung is scheduled up front at `flagged_at + delay`, so a worker that dies at rung 1 still has rungs 2–4 as durable rows and the Phase 2.1 sweep re-enqueues them. **PostgreSQL is the truth; pgmq is only a doorbell** — proven again by destroying every wake-up and finding all five rungs intact.
- **`case_escalation` is a new timer type, deliberately.** Phase 2.3 already uses `owner_reminder` and `unit_head_escalation` for the *lab* re-check chain. Sharing them would have left the fire handler running the wrong chain.
- **Six defects, every one found by running the code.**
  1. **P1 — a deadlock.** The fire path locked `sla_timers` → `pending_cases`; `acknowledge_case` locked them the other way. `DeadlockDetectedError`, measured on two real connections. The rest of the codebase locks the case first; the escalation fire path now joins that order.
  2. **P1 — two rungs at the same instant collapsed into one timer.** The idempotency key had no rung in it, so the ladder silently lost its upper rungs. A hospital can configure two rungs together, and a compressed clock guarantees it.
  3. **P1 — the ladder ignored a manual reassignment.** Measured: after `POST /reassign`, rung 1 notified the *contract doctor*, not the new owner. That made the endpoint cosmetic for the one thing it exists to do.
  4. **P2 — patient SMS bypassed the fatigue controls.** A FOLLOW_UP patient text went out at 03:00 IST. Worse for a patient than a doctor: they can neither act on it nor tell whether it is urgent. CRITICAL still bypasses; 4.6's absolute rules still run first.
  5. **P2 — the Phase 3 → Phase 4 seam was untested.** Every ladder test started the ladder by hand; nothing proved classification actually starts one.
  6. **P3 — the E2E cleanup orphaned Phase 4 rows.** Measured 8 orphaned notifications per suite run. `session_replication_role = replica` bypasses FK enforcement as well as the append-only trigger, so it failed silently rather than loudly. Same defect class the Phase 3 audit found.
- **Every fix has a regression test proven to fail when reverted** — each was reverted in turn and the matching test observed to fail, then restored.
- **🏁 Exit Gate 4 PASSED.** A critical flag left untouched walks all five rungs and produces a patient SMS through the adapter, with five `escalation_rung_fired` events carrying target and channel. Acknowledging at rung 0, 1, 2 *or* 3 cancels every remaining rung, and a rung firing afterwards is a no-op.
- **Found sound under probing:** owner resolution is deterministic across repeated calls; a roster row for an absent doctor is never honoured (ADR 0004's named failure); four workers racing one rung produce one effect; three concurrent ladder starts produce five timers, not fifteen; reassignment leaves every timer's `fire_at` byte-identical.
- **Verification:** 778 backend tests (was 658) · 89% coverage (gate 70) · ruff + black + mypy-strict clean on 79 source files · Phase 1/2/3 suites all green · 118 vitest · `tsc` clean · production build · 30 Playwright against the real stack · runtime Docker image builds and still ships no pytest · NODE B unreachable throughout.

### 2026-09-13 — Phase 3 comprehensive audit (6 defects found and fixed, **not pushed**)

- **Audited adversarially, not by re-reading the implementation report.** Every finding below came from probing the running system — driving real sentences through Rule C, fuzzing the colony-count parser, counting pending timers after a result arrives, running four workers on four independent connections.
- **A1 · P1 — negation resolution depended on PostgreSQL row order.** *"Malignancy is unlikely; ruled out on the prior imaging."* puts a hedge and a denial in scope of the same term; `is_negated` returned on whichever matched first. Measured: stored order gave `ruled out` (**finding discarded**), reversed order gave `unlikely` (**FOLLOW_UP, finding kept**). An unordered `SELECT` is not stable across vacuums or plan changes, so the same report could classify differently on different days — against the architecture doc's *"same input always gives same output"*. Fixed by collecting every in-scope pattern and letting a hedge outrank a denial.
- **A2 · P1 — two more colony-count under-reads.** `1.5e5` → **1.5** and `>10⁵` → **10**. Same class as the bug fixed during implementation, surviving in different notations, and failing the same way: under the contaminant threshold, and the contaminant branch returns *before* the resistance comparison. Ranges now take the upper bound.
- **A3 · P1 — a failing rule silenced the case.** Measured directly: after a final result arrives, the case has **0 pending timers** (intake superseded `result_due`). If classification then fails permanently the message reaches the DLQ — safe, but unread — and nothing ever wakes anybody. Each rule now runs behind `_safely()`, degrading to FOLLOW_UP; `DBAPIError` is re-raised so real infrastructure faults still retry.
- **A4 · P2 — a non-finite value made Rule A raise** rather than answer. Unreachable through the API (Pydantic refuses NaN/Infinity), fixed as the second lock for a row arriving by import or a future feed.
- **A5 · P2 — two clinical lists were never seeded.** `culture_no_growth_patterns` and `culture_contaminant_organisms` were read from `rule_config` and written nowhere, so the engine always fell through to Python constants. An admin could not change which organisms count as skin flora without editing source — the exact thing §3.2 forbids.
- **A6 · P3 — the recorded MDRO code was not deterministic.** Two rules match "MRSA (methicillin-resistant Staphylococcus aureus)" with an oxacillin R. Severity was never in doubt; the stored explanation was.
- **Every fix has a regression test proven to bite.** Each was reverted in turn and the matching test observed to fail, then restored: A1 (2 tests), A2 (8), A3 (2), A4 (3), A5 (4), A6 (1).
- **Found sound under probing:** idempotency with four concurrent workers on independent connections (1 classification, 1 clinical event) and three concurrent engine versions (3 classifications, as intended); Rule C matching against case, punctuation, newlines, double spaces and multi-word terms; preview writes nothing; no historical migration edited; zero NODE B references in any Phase 3 code path.
- **Accepted, not a defect:** a low-count urine growth resistant to a discharge antibiotic reports FOLLOW_UP rather than CRITICAL, because the plan orders contaminant detection (step 2) before the drug comparison (step 3). It never auto-closes. Whether that is right for a given hospital is a threshold question for 3.8's clinician walkthrough.
- **⚠️ Exit Gate 3 remains 🔴 OPEN.** The audit changed nothing about it: **54 synthetic cases, 0 real anonymised results, 0 clinician reviews, no agreement rate.** The two honesty guards still hold and were re-verified.
- **Verification after the fixes:** 655 backend tests (was 626) · 91% coverage (gate 70) · ruff + black + mypy-strict clean · Phase 2 suite 142 passed · Phase 1 suite 236 passed · 118 vitest · `tsc --noEmit` clean · production build · 30 Playwright against the real stack · migration round trip 28→16→28, drift 0, 0 PG enum types.

### 2026-09-13 — Phase 3.1 → 3.8: the clinical rule engine (implemented, **not pushed**)

- **Migration `0007_rule_engine_schema`** — 3.1's four result-detail tables and 3.2's six configuration tables, plus `unit_conversions` (named by Rule A's algorithm, missing from 3.2's schema list) and 3.6's `classifications`. Applied; autogenerate drift 0; round trip 28 → 16 → 28 tables; 0 PG enum types.
- **All three rules read their configuration from tables, never from code.** Change `slight_abnormal_factor` in `rule_config` and Rule A's answer changes with no deploy; expire a `panic_thresholds` row and it stops being applied. Both are asserted by tests rather than claimed.
- **No AI, no NODE B, no network.** Every classification is a table lookup and a comparison. A Playwright test asserts `/api/health` reports the inference node unreachable and then drives the whole flow to a CRITICAL anyway — RULE 1 and RULE 2 in one test.
- **Five real defects, every one found by running the code, not reading it.**
  1. `_set_case_severity` used one bind parameter in two differently-typed positions, so asyncpg refused the statement and **every** classification with a case raised. Total breakage, invisible until a test staged a real case.
  2. `_panic_threshold` raised whenever the patient's age was unknown — `$3 IS NULL` is untypeable — so Rule A failed on exactly the patients whose date of birth the hospital never recorded.
  3. `1.5 x 10^5` parsed as **1.5**. That put a heavy growth under the contaminant threshold, and the contaminant branch returns *before* the resistance comparison — so a resistant organism would have been reported as likely contamination instead of CRITICAL. The product's central failure, reintroduced by a regex.
  4. `10^5` parsed as 1,000,000, an order of magnitude out.
  5. `uq_unit_conversions_triple` constrained nothing for generic rows: `test_code` is null for the common case and PostgreSQL's default `NULLS DISTINCT` lets two identical rows coexist. Now `NULLS NOT DISTINCT`, which also made the seed re-runnable.
- **Contaminant detection is checked *after* MDRO, and panic bounds *before* the reference range** — both deliberate departures from the printed step order, both recorded in the phase doc's deviations table with the failure each one prevents.
- **A hedge is not a denial.** *"Cannot exclude malignancy"* downgrades CRITICAL to FOLLOW_UP rather than being suppressed as a negation. Stored as `negation_patterns.is_hedge`.
- **Narrative reports cannot auto-close, structurally.** `_all_rules_permit_auto_close` takes an AND across rules with a default of *no*, and Rule C emits a literal `False`. A test parses the source and fails if a second `auto_close` value ever appears. One line of prose on a report whose culture is fully covered keeps the case open — asserted in Python and again in a browser.
- **3.7's preview writes nothing, and that is counted rather than assumed.** A test snapshots nine tables before and after a preview call and asserts equality. A second test previews a payload, saves the same payload, classifies it, and asserts the prediction and the decision agree — the property that makes the panel worth showing.
- **The E2E cleanup needed extending, and the need was proven.** Phase 3 hangs five child tables off `results`; `session_replication_role = replica` bypasses FK enforcement as well as the append-only trigger, so the old cleanup would have **silently orphaned** a classification per run rather than failing loudly. Demonstrated with a staged delete (`orphan_left=1`) before fixing. Same defect class as the Phase 2 audit's P3.
- **⚠️ 3.8 IS NOT DONE AND EXIT GATE 3 IS OPEN.** The harness exists and 51 synthetic cases pass; **no clinician has reviewed any of it**, there are **0 real anonymised results**, and there is **no agreement rate**. Two tests exist purely to keep that honest: one fails if `clinician_validated` is flipped without a named reviewer and a date, the other parses this suite's own AST and fails if anything computes a percentage while the set is synthetic. Both were proven to fail when deliberately tripped. See [docs/clinical-validation.md](docs/clinical-validation.md).
- **A sixth defect, found by running the suites in the wrong order.** `test_migration_round_trip.py` downgrades to base and back, which drops and recreates the Phase 3 configuration tables — so running the Python suite between two Playwright runs emptied them and the second E2E run failed with "element not found". An empty rule configuration does not error; it grades everything as unclassifiable, which looks nothing like the cause. The E2E global setup now seeds the configuration itself. Proven by emptying the tables (`panic_after_roundtrip=0`) and re-running the suite green.
- **Verification:** 626 backend tests, 90% coverage (gate 70); ruff + black + mypy-strict clean on 67 source files; 118 frontend vitest; `tsc --noEmit` clean; production bundle builds; **30 Playwright tests including 8 new Phase 3 ones**, all against the real stack; runtime Docker image builds and still ships no pytest.

### 2026-09-12 — Phase 2.2 → 2.5 and Exit Gate 2 (implemented, **not pushed**)

- **Read the whole of Phase 2 before writing anything, and the task boundary decided the shape.** "Create on discharge" is a 2.2 bullet, so Phase 1.3's discharge is now wired to a durable timer; the `pg_cron` sweep was already 2.1's and stayed. 2.3's "Notify" is enqueued as an intent and stops there — channel, template and the escalation ladder are Phase 4.3/4.6.
- **Migration `0006_phase_2_lifecycle`** — `lab_flags` (2.3), `results` (2.4), `sla_timers.paused_at`/`pause_reason` (2.2), and the `classify` queue. ⚠️ **`results` is specified in Phase 3.1, not Phase 2**, and is pulled forward only because 2.4's endpoint cannot function without it and the plan says that endpoint *"stays forever"*. 3.1's analyte/organism/sensitivity/narrative tables are deliberately **not** created — those exist to be read by the rule engine.
- **Pause is not a fifth status.** The plan enumerates exactly `pending|fired|cancelled|superseded`, so a paused timer stays `pending` with its deadline intact and is skipped by the handler and the sweep. Resume is one `UPDATE` that restores the *same* deadline rather than deriving a new one. The reason is read from the encounter and cannot be supplied by a caller — a client able to assert "this patient died" could otherwise silence any case's timers.
- **Caught a real defect by running it, not reading it.** The stub consumers Phase 0.7 started on every queue log the message and then **delete** it. That was harmless while nothing produced messages; it became destructive the moment 2.3 began enqueueing notification intents, which were being erased within two seconds. Only `sla_timers` now has a consumer; an unconsumed queue is the correct state for an unbuilt phase. A regression test asserts the consumer list.
- **Both Phase 2.1 provisional decisions confirmed rather than changed.** The idempotency key `case_id:timer_type:fire_at` held once 2.2 created real timers, and the `fired_at`/`status` biconditional refused no transition 2.2 needed.
- **The four races tested on two real connections**, not simulated sequentially: two workers on one timer, closure vs firing, result arrival vs firing, and three concurrent creations of the same timer. Plus recovery: a lost message, a two-hour outage, a visibility-timeout redelivery, repeated sweeps, and a sweep racing a worker.
- **🏁 Exit Gate 2 chaos test passes** — `scripts/chaos_exit_gate_2.sh`, run from the host because it restarts the containers the suite runs inside. 50 real discharges through Caddy, deadlines two minutes out, **restarted twice** (the second restart taking Postgres too). Result: **50 flags, 50 fired timers, 50 events, max 1 flag on any case.** The count comes from `lab_flags` in PostgreSQL — a duplicate queue message is not a second clinical event.
- **415 backend tests** (was 330) · 102 frontend · 22 Playwright · coverage **87.94%** · ruff/black/mypy-strict/tsc/build clean · Docker rebuild + startup + health `ok` with **`llm.reachable: false`** · `alembic check` drift 0 · migration round trip re-verified across **both** Phase 2 migrations · all six queues, six extensions and the append-only trigger intact.
- 🟡 **Marked implemented, not done.** The comprehensive Phase 2 audit has not run, and the work is **deliberately unpushed** pending it.

### 2026-09-12 — Phase 2.1, the SLA timer model

- **Read the task boundary before writing anything, and it mattered.** The build plan puts *"Create on discharge: `result_due` at `contract.expected_by`"* under **2.2 Timer lifecycle**, not 2.1 — so Phase 1.3's discharge is deliberately left untouched and writes no `sla_timers` row. The `pg_cron` sweep, by contrast, **is** a 2.1 bullet, so it is implemented. Doing the discharge coupling here would have been implementing 2.2 under 2.1's name.
- **Migration `0005_sla_timers`** — 14 columns, 4 CHECK constraints, 3 foreign keys, 5 indexes, **0 PostgreSQL enum types**. `pgmq_msg_id` is **BIGINT** to match `pgmq.q_sla_timers.msg_id` (an int4 column would overflow silently on a long-lived queue) and **nullable**, because a timer whose message was consumed, archived or lost is still a timer — finding exactly those is what the sweep is for.
- **`case_id → pending_cases ON DELETE RESTRICT`**, matching every other clinical FK in the schema. Cases close, they do not vanish; a cascade would only ever fire on a mistake, and silently taking the escalation clock with it is the worst possible response to one.
- **The sweep** `rg_sweep_overdue_sla_timers()` — pending timers more than 10 minutes overdue whose queue message is gone, re-enqueued with the same payload shape Phase 1.3 uses so a swept wake-up is indistinguishable from an original. Written as a function rather than inline in the cron command, because *a scheduled job that can only be observed by waiting five minutes is a job nobody verifies*. Scheduled `*/5 * * * *` as `rg-sla-timer-sweep`.
- ⚠️ **Reported a genuine spec ambiguity rather than quietly choosing.** The plan says `idempotency_key (unique)` and never defines its construction. Used `case_id:timer_type:fire_at` (UTC, microsecond) — the only reading that satisfies both 2.2's "one `result_due` per case, and a replayed discharge must not create a second" and 2.3's "re-check every 24h", which is a different instant each time. Deliberately did **not** add a second `UNIQUE(case_id, timer_type, fire_at)`: the plan specifies one unique column. `(status = 'fired') = (fired_at IS NOT NULL)` is likewise flagged as derived, with a note that 2.2 should revisit it if a lifecycle transition is refused.
- **Migration round trip run for real** — head → 0004 → head, asserting at each stop that downgrade removes *only* Phase 2.1's additions and leaves all 12 Phase 1 tables, all 6 extensions, all 5 queues and the `case_events` append-only trigger untouched. The test restores head in a `finally`, so a failure cannot strand the database at 0004 and fail every other integration test for unrelated reasons.
- **36 new tests** covering every valid status and type, every refusal (unknown type, unknown status, negative attempts, missing case, duplicate key, fired-without-fired_at, fired_at-without-fired), TIMESTAMPTZ on all five timestamp columns, bigint round-trip at max value, index definitions, and six sweep behaviours including the one that matters most — **it must not re-send a timer that still has a live message**.
- **Regression:** 330 backend (was 294) · 102 frontend · **22 Playwright** · coverage 87.43% · ruff/black/mypy-strict/tsc/build clean · Docker rebuild + startup + health `ok` with **`llm.reachable: false`** · `alembic check` drift 0 at head `0005_sla_timers`. Phase 1 fully intact.

### 2026-09-12 — Phase 1.8 E2E, and Exit Gate 1

- **Installed Chromium only** (`npx playwright install chromium`, build 1243, 114 MB) and ran the suite that had been sitting 🟡 since Phase 1.4. First run: **7 passed, 3 failed**, plus one flaky — the honest result of code that had never been executed in a browser.
- 🐞 **One real product defect, found only because a browser ran it.** A draft restoring a responsible doctor who was *not in the first page of `/api/users?role=doctor&limit=20`* rendered the field **completely empty**, placeholder and all — while the form still held the id and the gate still accepted it. A field that reads as unassigned and behaves as assigned. Root cause: `DoctorSelect` was handed a name only when the selection happened to be the attending doctor; every other doctor depended on being inside the current search page. Fixed by letting the page resolve any id it already knows (picked doctors + directory), plus a fallback label so a selection can never render as empty. **Three regression tests**, reproduced in jsdom first.
- 🐞 **Five E2E defects, all in the tests, none in the product:** an ambiguous `getByText("HbA1c")` that also matched the code `HBA1C`; a keyboard test typing a bare first name that matched doctors left behind by *previous runs*; the same test then racing the async search and pressing Enter against a list that was about to be replaced; a row targeted by position when readiness legitimately sorts by `ordered_at`; and an append-only check reading psql **stdout** when `RAISE NOTICE` goes to **stderr** — the same trap this project was caught by during the Phase 1 audit.
- **Made the suite repeatable.** It had left every run's rows behind — eighteen "Ravi Kulkarni" doctors had accumulated, which is what made the keyboard test ambiguous. Added `cleanupE2EData()` plus Playwright `globalSetup`/`globalTeardown`, so the suite cleans on the way in *and* out. Uses the connection-scoped `session_replication_role` the Python tests already use — never `DISABLE TRIGGER`, which would survive a crash.
- **Grew the suite from 10 to 22 tests** to cover the journey the Phase 1.5 screens made possible and the invariants only a real stack can threaten: patient search → encounter → gate; a manual order changing the gate's answer live; case owner, `case_events` and `pgmq.q_sla_timers` all asserted in the database; a **stale page** unable to talk the server into a discharge; a **replayed** discharge refused without opening a second set of cases; a **concurrent** order-vs-discharge over real HTTP; and the append-only trigger refusing to be rewritten.
- **🏁 EXIT GATE 1 PASSED.** All three clauses verified as one uninterrupted browser journey, using the build plan's own wording. The seeder now creates **3 orders — 1 resulted, 2 pending** exactly as the gate specifies; the resulted one is the control, and it correctly blocks nothing and opens no case. Final state read from Postgres: `discharged`, 2 pending cases, 0 cases from the resulted order, 2 SLA timers queued.
- **Full regression:** 294 backend · 102 frontend (was 99) · **22 Playwright** · coverage 87% · ruff/black/mypy-strict/tsc/build clean · Docker build + startup + health `ok` · `alembic check` drift 0. Every E2E test ran with **`llm.reachable: false`** and `degraded_features == ["llm_generation"]`.
- **Database left clean:** 0 E2E rows, 0 orphans of any kind, `trg_case_events_append_only` enabled, `session_replication_role = origin`, all six extensions present. `python -m scripts.seed_dev` repopulates dev data on demand.

### 2026-09-12 — Phase 1.5, supporting screens

- **Patient search** — `GET /api/patients?q=`, one box serving MRN, name and phone, ranked so an unambiguous identifier always beats a fuzzy name. Name matching runs on the **Phase 1.2 GIN `gin_trgm_ops` index** (verified: "Sunta Rao" finds "Sunita Rao", so a clerk who mistypes does not create a duplicate record). Phone compares digits to digits because the column is E.164 and the clerk types what is written on the file.
- **Closed a patient-index dump before it existed:** LIKE wildcards inside `q` are escaped, so a one-character search of `%` returns nothing instead of every patient in the hospital. Four probes (`%`, `_`, `%%`, `\`) are asserted to return zero rows.
- **Encounter detail** — `GET /api/encounters/{id}/detail`: orders with status, their contracts and owners, and discharge medications, assembled in **one transaction** so the three cannot disagree. It reports the gate's answer by calling the same `get_discharge_readiness` the discharge action re-runs, rather than re-implementing the blocking rule — a second copy is a second thing that can drift. The Phase 1.4 gate's own endpoint is untouched.
- 🔒 **Closed the order-creation race documented in the Phase 1.3 audit.** `create_manual_order` takes `SELECT … FOR UPDATE` on the **same encounter row the discharge action locks**, so the two serialise: discharge first → the order is refused 409; order first → the discharge re-derives readiness inside its own lock and is blocked 409. The fix needed **no change to the discharge algorithm**, no advisory lock, no SERIALIZABLE, no retry loop.
- **Proved the race test is not vacuous.** With `.with_for_update()` temporarily removed, `test_order_creation_cannot_race_a_discharge` failed **6 of 6 runs** with exactly the unsafe outcome `['discharged', 'order_created']` — a discharged encounter holding a new order nobody owned. Lock restored: 6 of 6 pass. The test asserts the invariant out of the database, not off the screen.
- **Discharge medication entry** — capture only, for Phase 3's Rule B. No interaction checking, no dose validation, no AI. Accepted **after** discharge as well as before, deliberately: a summary is often typed up once the patient has left, and a medication row changes nothing the gate reads.
- **Seed script** `api/scripts/seed_dev.py` — 20 patients, 60 orders across all seven statuses, 10 doctors, 1 department with a unit head. Deterministic: ids are stable **UUIDv7** values derived from a fixed namespace, so a second run upserts the same twenty people instead of creating forty. Obviously synthetic — `(SEED)` suffix, `SEED-` MRNs, 555-range phone numbers — and it refuses to run when `RG_ENV=prod`.
- **Two real bugs found by running the code, not by reading it:** (1) order and medication creation flushed but never committed, so an order vanished the moment the request ended — caught by a live curl, not by a test; (2) the seed gave every encounter three *identical* orders, because 60 orders over 20 encounters with 10 tests made `i % 10` line up, defeating the build plan's own "varied states" requirement.
- **Hardened the runtime image.** Adding `scripts/` meant `COPY . /app` was shipping a fake-patient writer into the production image. `scripts/` and `tests/` are now stripped from the `runtime` stage and copied back in `dev`. Verified both ways: absent from runtime, present in dev, `app` and `alembic` still intact.
- **Exit check run end to end against real data:** find patient → open encounter → see orders → add a manual order → **gate blocks (409)** → assign contracts → discharge → 3 cases + 3 SLA timers opened → enter discharge medications → a post-discharge order is **refused**. Final database state: `discharged, 3 contracts, 3 cases, 3 events, 0 unowned outstanding`.
- **Regression:** 294 backend tests (was 220) and 99 frontend tests (was 59) pass; coverage **87%**, raised from 79% with tests rather than by lowering the gate. ruff · black · mypy-strict · tsc · production build all clean. **`alembic check` → no new upgrade operations: Phase 1.5 needed no migration.** All six extensions and the `case_events` append-only trigger intact. `/api/health` returns `ok` with **`llm.reachable: false`** — NODE B stays irrelevant.

### 2026-09-12 — Phase 1.4, discharge gate UI

- **Unblocked 1.4 first.** The gate could not be built as specified: nothing exposed a list of doctors to search, and nothing exposed `encounters.attending_doctor_id`. Reported rather than inventing a later phase; the user approved **two minimal read-only endpoints**. Added `GET /api/users` (role + `q` search, inactive hidden by default, hard cap 100) and `GET /api/encounters/{id}` (patient + attending doctor). No new tables, no migration, no write path. **13 tests, all passing**; ruff/black/mypy clean.
- **Scanned before installing** (CLAUDE.md rule): Node **v24.14.0** and npm **11.9.0** already on NODE A at `C:\Program Files
odejs` — nothing to install. `npm install` pulled 286 packages into `web/node_modules` (gitignored).
- **Scaffolded `web/`** — Vite 6 + React 18 + TypeScript strict + Tailwind 3, dev proxy `/api` → `localhost:8000`. **No build-time API host**: Caddy already serves `web/dist` and proxies `/api`, so the app is always same-origin.
- **Built the three-step gate** at `/encounters/:id/discharge`: step 1 pending investigations with a red banner, step 2 per-order owner + expected-by with "apply first row to all", step 3 plain-language review, then a success screen carrying the contract references.
- **No skip control, and it is enforced by a test**, not by inspection: the suite walks every button and link and fails on `skip|dismiss|ignore|discharge anyway|not required|remind me later|mark as done`.
- **Override path** is a separate red button → Radix dialog → reason code + 20 trimmed characters + an overriding doctor, all three required before Confirm enables. The dialog states plainly that an override is not a dismissal.
- **Draft persistence** in `sessionStorage` (not `localStorage` — a ward terminal is shared), Zod-validated on read. A corrupt, version-stale or **foreign-encounter** draft is deleted rather than half-read; an assignment whose order stopped blocking is dropped before submit, since the batch is all-or-nothing.
- **Found and fixed a real ordering bug while writing the tests:** if contract creation succeeded but the discharge POST then failed, pressing Confirm again re-posted the contracts and 409'd on `UNIQUE (order_id)`, stranding the doctor on an error they could not clear. The created contracts are now held in state and creation is skipped on retry. Covered by `does not create the contracts twice when only the discharge failed`.
- **59 tests pass** (37 gate flow + 11 draft + 11 datetime), `tsc --noEmit` clean, `npm run build` green (396 kB / 122 kB gzip). Verified served live through Caddy: route 200, JS 200, CSS 200.
- **Playwright E2E written against the real stack** — `web/e2e/discharge-gate.spec.ts`, 10 tests that read `discharge_contracts`, `pending_cases`, `discharge_overrides` and `encounters.status` **out of Postgres** rather than trusting the screen. Seeder `web/e2e/seed.mjs` generates UUIDv7 PKs and is ✅ **verified working** — it created a live gated encounter and the readiness endpoint returned it correctly through Caddy.
- 🟡 **The E2E spec has never run.** Playwright wants Chromium build 1243; only 1228 is cached and the download was declined. **Marked 🟡, not ✅** — written is not done.
- **Closed a CI gap the new code exposed:** CI built and tested `api/` and never looked at `web/`, so the 59 frontend tests — including the one that fails the build if a skip control reappears — would only ever have run on NODE A. Added a `web` job (npm ci → tsc → vitest → production build). **All four jobs green** (run `34689079594`).
- Left the E2E smoke-test rows in the dev database (`MRN E2E-*`, one discharged encounter). Clinical rows are never hard-deleted in this project and the seeder makes a fresh encounter per run, so nothing needed cleaning up.
- **Deviations recorded** in the phase doc: availability badge 🚫 deferred to Phase 4.1 (needs `duty_roster`/`user_absences`; showing `is_active` as availability would say "on duty" about someone on leave), contract reference = the `contract_id` itself (no human-readable column exists to prettify), and the override dialog collects an overriding doctor because auth is Phase 5.1.

### 2026-09-11

- Read both source PDFs in full (architecture 24pp, build plan 48pp) via `pypdf` text extraction.
- Created `docs/architecture/` — 6 chunks covering Steps 0–11, degradation ladder, security, AI boundary.
- Created `docs/build/` — 16 chunks: governing rules, locked stack + repo layout, Phases 0–10 as checklists with exit gates, gap traceability + timeline.
- Created `docs/README.md` index with reading order and cross-refs.
- Created `CLAUDE.md` — governing rules, mandatory work-logging protocol, locked decisions, doc map.
- Created `PROGRESS.md` (this file) — phase table, long-lead items, open decisions, session log.
- Wrote the local (untracked) memory base — 5 notes + index.
- Ran the pre-install scan (see table above). **Nothing installed** — everything needed was already present.
- Decided the two forced decisions (OPD scope, roster owner) → ADRs 0003, 0004. Recorded node roles + model → ADR 0005.
- `git init` + initial commit; created **private** repo `AshmitThakur23/result-guardian` and pushed. Switched the active `gh` account first — `abhinendra9792` was the default and would have received the repo.
- Caught a `.gitignore` bug before the first push: the Python `build/` rule was swallowing all of `docs/build/` (13 phase docs). Root-anchored to `/build/`.
- Wrote the Phase 0 scaffold: compose ×3, custom Postgres image (pgvector + pg_cron + pgmq), API skeleton (config, db, logging, RFC 7807 errors, `/api/health`, `/api/version`, cached non-blocking NODE B probe), worker (pgmq consumer + backoff + DLQ + heartbeat), Alembic, tests, CI, Caddy, both NODE B provisioning scripts, README, network runbook, Makefile + `tasks.ps1`.
- Ticked Phase 0.4 and 0.6 as genuinely complete. Everything else is **written but unexecuted** and left unticked.
- **Phase-wise tracking protocol installed** (user request): moved it to the **top** of `CLAUDE.md` as the first thing read every session. Added a 📍 **STATUS SUMMARY** table to **all 11 phase docs** with a shared marker legend (✅ / 🟡 / 🔵 / ⬜ / 🔴 / 🚫), linked every phase row here to its own doc, and wrote in the two honesty rules: *written ≠ done*, and *unlogged work counts as not done*.
- **Removed all assistant attribution from the repo and its history** (user request). Stripped the `Co-Authored-By` trailers from all 5 commits via `filter-branch`, purged the backup refs, force-pushed. Dropped the "Decided by" byline from ADR 0003/0004 and the machine-local path from this log. Verified against the GitHub API, not just locally: **0 commits carry attribution**, sole contributor is `AshmitThakur23`. `CLAUDE.md` stays tracked under its own name — both sides work from the same rules — and now carries a **commit-hygiene rule** forbidding attribution bylines. The product's own `qwen3`/`Ollama`/`LLM` references were deliberately left alone: that is NODE B's actual stack, not a byline.
  ⚠️ **History was rewritten — every commit hash changed.** Anyone who already cloned must delete their copy and re-clone; `git pull` will conflict.
- **Read the entire knowledge base and the whole Phase 0 scaffold**, then audited the code that had never been executed. **Found 8 defects** — see the table in [`docs/build/phase-00-foundation.md`](docs/build/phase-00-foundation.md). Two were load-bearing: `/api/health` returned **503** whenever `worker_health` was missing (both queries shared one `try`), which fails Exit Gate 0 and the RULE 2 assertion over a table unrelated to database reachability; and `cron.database_name` was baked into the image while `POSTGRES_DB` stayed configurable, so a renamed database would boot **silently without pg_cron** — deleting Phase 2's "NODE A reboots, no timer is lost" guarantee. Also: **no CI job could have gone green at all** (no `[build-system]` in `api/pyproject.toml`), and a production `docker compose build` shipped pytest/ruff/mypy because `dev` is the last stage in `api/Dockerfile`.
- **Fixed all 8.** Added a regression test for the health-probe split. Moved `worker_health` into the Alembic baseline, so `alembic upgrade head` is now part of documented first boot. CI now builds and runs the real NODE A database image, asserts all six extensions are present, and applies migrations before pytest.
- ⚠️ **Nothing was executed.** Per the user's instruction, no local run: the "no Phase 0 execution on NODE B until NODE A is up" agreement stands. ruff, black and mypy were **not installed** on NODE A at that point and were **not installed** to check — so `black --check` and `mypy --strict` outcomes are predictions, not results. What *was* verified locally is only what needs no install: every Python file parses (`ast.parse`), all four YAML files parse, and no line exceeds 88 characters. **All eight fixes are 🟡, not ✅.**
- **🔄 NODE ROLES CORRECTED BY THE USER — they were recorded backwards.** **This development laptop is NODE A** (core: Postgres, API, worker, dashboard, all patient data). **NODE B is Ashmit's machine** (Ollama, GPU, stateless). Written into `CLAUDE.md` as the authoritative table, and swept through `PROGRESS.md`, `README.md`, `docs/network-runbook.md`, `docs/build/phase-00-foundation.md` and `docs/adr/0002`. **ADR 0005 is marked SUPERSEDED and deliberately left in place** — its wrong assignment is kept as the record that the error happened, not edited away. [ADR 0006](docs/adr/0006-node-roles-corrected.md) carries the correction.
  - ~~⚠️ **Decision #4 (`qwen3:4b`) is reopened** — derived from 4 GB VRAM thought to be NODE A's.~~
    **↳ WITHDRAWN 2026-09-12.** That was wrong: `nvidia-smi` on Ashmit's machine proves the RTX 3050 is **NODE B's**, and NODE A has no NVIDIA GPU at all. `qwen3:4b` was correctly derived all along. Decision #4 stands.
  - ⚠️ **The critical path moved.** "Wait for NODE A" assumed NODE A was the *other* machine. It is this one, and Docker is on it. **Phase 0 is executable here now.**
- **Install rule relaxed by the user:** scan both drives first; if absent, install without asking. Recorded in `CLAUDE.md`.
- **Direct-to-`main` workflow adopted** (user decision). No branches, no PRs. The short-lived `phase-0-verification` branch was fast-forwarded into `main` and deleted, local and remote. Removed the `no-commit-to-branch --branch main` pre-commit hook — dormant only because `pre-commit install` had never been run, it would have blocked every commit the moment it was. Phase 0.1's "main protected, PRs required" is now 🚫 out of scope.
- **CI ran for the first time ever.** `lint` ✅ and `build` ✅ — confirming **D1, D8, D6 and D2** on a clean Ubuntu runner (the build job proves the runtime image ships no pytest/ruff/mypy). The 5 prior runs on `main` had all failed at `pip install -e ".[dev]"` with *"Multiple top-level packages discovered in a flat-layout"* — D1, verbatim, exactly as diagnosed.
- 🐞 **D9 found by CI and fixed — `infra/postgres/Dockerfile` shipped a broken pgmq.** `test` failed with *"extension pgmq has no installation script nor update path for version 1.4.4"*, killing the Postgres container at init. **Root cause:** pgmq's tarball contains only *upgrade* scripts (`pgmq--X--Y.sql`); the base `pgmq--1.4.4.sql` is **generated** by the Makefile's `all` target from `sql/pgmq.sql`. PGXS's `install` does not depend on `all`, and `DATA = $(wildcard sql/*--*.sql)` is expanded at parse time — so the base script was neither generated nor listed. The image built cleanly while being unusable. Fixed by generating the base script before `make install`, deriving the version from `pgmq.control` (not the git tag), and **asserting both files exist at build time** so the build fails instead of the first boot.
- ✅ **D9 verified on NODE A with real Docker** — image builds, container starts in ~6 s and stays healthy, and `pg_extension` contains all seven: **pgmq 1.4.4**, **pg_cron 1.6**, vector 0.8.6, pg_trgm, unaccent, pgcrypto, plpgsql. All five queues created; `pgmq.send`/`pgmq.read` round-trip works. `cron.database_name` correctly resolved to the database — **which also verifies the D3 guard**.
- 🏁 **CI IS GREEN — all three jobs, for the first time in this repo's history** (run `34670777455`). `lint` ✅ · `build` ✅ · `test` ✅. The test job builds the real NODE A image, asserts all six extensions, applies Alembic migrations to an empty database, runs 25 tests and clears the 70% coverage gate. **Every one of the 7 previous runs had failed.**
- ✅ **All nine defects (D1–D9) are now verified**, not merely fixed. D2, D5, D6 and D7 were confirmed by this CI run; D3 and D9 both locally and in CI; D1, D8 locally; D4 at unit level. Phase 0.8 moves 🟡 → ✅.
- **Recorded NODE A's real machine specs** (see the scan table above) — `LAPTOP-06ER0HBM`, i5-12450H, 15.7 GB RAM, C: 39.5 GB free / D: 683 GB free. **NODE A has no NVIDIA GPU at all** (Intel UHD only, no `nvidia-smi`), independently confirming Ashmit's `nvidia-smi` finding that the RTX 3050 is on **NODE B**. Also found the native `postgresql-x64-18` service holding **port 5432 on NODE A too**, so the dev 5433 mapping is required on both machines — not a NODE-B-only quirk as previously recorded.
- **Installed the lint/test toolchain and actually ran it** (new install rule — scanned first, all absent, installed without asking). `ruff 0.16.7`, `black 26.5.1`, `mypy 2.3.1`, plus `pip install -e ".[dev]"`. Python here is **3.11.9**, matching the project pin. **`C:` now has 43 GB free, not the 17 GB in the old scan** — that figure was stale.
- **Running it found 5 more defects that reading had missed** — the clearest possible evidence for *written ≠ done*. Notably `alembic/env.py` had **pre-existing unsorted imports**, so `ruff check .` would have failed CI even after the D8 fix. Also 2 × `RUF100` unused pragmas, 2 × `SIM105`, and 2 mypy-strict errors (`2 ** n` types as `Any`; an unused `type: ignore`). All fixed.
- **✅ All five CI gates now pass on NODE A:** `ruff` clean · `black --check` 27 files unchanged · `mypy --strict` clean across 23 files · **25 tests pass** · coverage **79.17%**.
- **The coverage gate did trip, at 50.78%** — exactly the risk flagged. Closed it **with tests, not by lowering the number**, per the standing rule. Added 19 tests: `worker/consumer.py` was at **0%** despite holding the pgmq retry/backoff/DLQ logic that Phase 2's durability rests on; the heartbeat and its failure path were untested; the Phase 0.6 conventions were unasserted. Now 79%.
- **Three of the eight defects are now genuinely ✅** (D1 packaging, D4 health-probe split at unit level, D8 lint). **Five stay 🟡** — D2, D3, D5, D6, D7 all need Docker.
- **Next:** run Phase 0 on NODE A — `docker compose up -d --build` → `alembic upgrade head` → `curl localhost/api/health`. That closes 6 of the 8 first-boot checks and the remaining five defects. Only the two cross-node clauses need Ashmit's machine.

---

## ✅ First-boot checklist for NODE A — this is what turns 🟡 into ✅

Eight defects were fixed by inspection on 2026-09-11 and **none has run**. Work this list on NODE A's first boot and tick the phase doc only from results.

| # | Step | Proves |
|---|---|---|
| 1 | `cp .env.example .env`, set `POSTGRES_PASSWORD` / `RG_JWT_SECRET` / `RG_LLM_BASE_URL`, `docker compose up -d --build` | 0.2 |
| 2 | `docker compose exec postgres psql -U rg_app -d result_guardian -c '\dx'` → `vector`, `pgmq`, **`pg_cron`**, `pg_trgm`, `unaccent`, `pgcrypto` | D3 — pg_cron present means Phase 2 recovery is real |
| 3 | **Before migrating:** `curl localhost/api/health` → **200**, `db: "ok"`, `degraded_features` contains `worker` | **D4 — this returned 503 before** |
| 4 | `docker compose exec api alembic upgrade head`, re-curl → `worker_heartbeat_age_s` is a small number | D5 + 0.7 heartbeat |
| 5 | `llm.reachable: false`, `status: "ok"`, no error, NODE B untouched | **RULE 2 at the HTTP boundary** |
| 6 | `docker compose -f docker-compose.yml build api` then `docker run --rm result-guardian/api:dev pip show pytest` → **not found** | D2 |
| 7 | On a *fresh volume*, set `POSTGRES_DB=rg_test` → boot **fails loudly** with the guard message | D3 guard |
| 8 | Open a PR → all three CI jobs green | D6, D7 (D1 and D8 already ✅ locally) |

✅ **Coverage resolved.** The gate tripped at 50.78% and was closed **with tests** — 19 added, now **79.17%**, 25 passing. The number was not lowered.

**Still cannot close Exit Gate 0** after all of the above: its "fresh machine" clause and the NODE A → NODE B `curl http://<NODE_B_IP>:11434/api/tags` check both need the second machine on one LAN, and open decision #5 below is still undecided.

- **Phase 0 commits D10 + D11 pushed**; CI green on `141c08b` — **32 passed, 0 skipped**, all three jobs ✓.
- 🟢 **PHASE 1 STARTED — 1.1 core schema, migration `0002_core_schema`.** Six tables: `departments`, `users`, `patients`, `encounters`, `orders`, `discharge_contracts`. Applied on NODE A and **round-tripped** (downgrade to baseline → upgrade), with pgmq/pg_cron/queues intact afterwards. All Phase 0.6 conventions enforced and asserted by tests: UUIDv7 PKs, TIMESTAMPTZ everywhere, audit + soft-delete columns on every table, text+CHECK (zero PG enum types), NUMERIC for `expected_tat_hours`. `UNIQUE (order_id)` on `discharge_contracts` is the database enforcing one-contract-per-order.
  - ⚠️ **1.1 is NOT complete — 5 of 11 tables remain**: `discharge_contract_revisions`, `pending_cases`, `case_events`, `discharge_medications`, `discharge_overrides`.
  - **Autogenerate drift check caught three real mismatches** before they shipped, the worst being that `worker_health` had no model — so the next autogenerate run would have emitted `op.drop_table('worker_health')`, silently deleting the worker heartbeat. Added `WorkerHealth`; drift is now zero.
  - **Open question for the hospital:** `patients.sex` is deliberately left without a CHECK. The build plan specifies value sets for every other enum-like column but not this one, and the coding scheme (M/F/O vs male/female/other) is theirs to decide.
- **Long-lead items 1.6 and 1.7 opened** (build plan: week one of Phase 1). Both are **human-blocked and honestly recorded as such** — no fabricated progress. 1.6: 0 of 200+ reports, 5 named blockers, manifest headers only, reports stay out of git. 1.7: full discovery questionnaire, every answer `— UNANSWERED —`, 4 blockers starting with "which HIS/LIS is it".
- Tests: **52 passing** (25 unit + 27 integration), coverage **84.73%**.

- ✅ **PHASE 1.1 COMPLETE — migration `0003_core_schema_remaining`** adds the final five tables: `discharge_contract_revisions`, `pending_cases`, `case_events`, `discharge_medications`, `discharge_overrides`. Full downgrade-to-baseline → upgrade round-trip passes; pgmq, pg_cron and all five queues intact afterwards; **autogenerate drift = 0**.
  - **`case_events` append-only is enforced, not documented.** A `BEFORE UPDATE OR DELETE` trigger calls `rg_reject_mutation()`, which raises with SQLSTATE `restrict_violation`. Tests issue a real UPDATE and a real DELETE and require the database to refuse both. The function is deliberately generic so Phase 5.5's `audit_log` can reuse it.
  - `case_events` and `discharge_contract_revisions` have **no `deleted_at`** — a soft delete is an UPDATE, and neither table may be mutated. A revision history whose rows can be removed is not a history.
  - **Drift check earned its keep again**: five indexes existed in the database but not in the models, so the next autogenerate would have dropped and recreated them. Declared on the models; drift back to 0.
  - Hit the `::` bind-parameter trap a second time — `:t::regclass` in a test query silently matched nothing. Same root cause as the D10 `CAST` decision. Joined by name instead.
  - **Left deliberately unconstrained, and flagged:** `pending_cases.closure_reason` (values are enumerated in Phase 5.3, not 1.1 — a CHECK now would block Phase 5 if the list is refined) and `patients.sex` (still the hospital's data-standards decision, unchanged).
  - `discharge_overrides` carries both constraints from the plan's 1.3 override spec: `reason_code` CHECK over the five permitted codes, and `char_length(trim(reason_text)) >= 20` — a one-word excuse is not an audit trail.
- Tests: **102 passing** (25 unit + 77 integration), coverage **86.53%**.

- ✅ **PHASE 1.2 COMPLETE — migration `0004_phase_1_2_indexes`.** All six of the plan's indexes verified against **PostgreSQL's own catalogue** (`pg_indexes.indexdef`, `pg_opclass`), not merely SQLAlchemy metadata — 1.1 already produced one case where the two disagreed.
  - **Three new:** `orders(encounter_id, status)`, `pending_cases(state, severity, opened_at)`, `patients USING gin (name gin_trgm_ops)`.
  - **Two plain indexes replaced by their partial forms**, rather than added beside: `ix_orders_external_order_id` (now `WHERE external_order_id IS NOT NULL`) and `ix_pending_cases_current_owner_id` (now `WHERE state IN ('flagged','result_received')`). A partial index still serves every equality lookup, so keeping both would cost writes for no extra reads. The second of those plain indexes was my own speculative addition in 0003, which the plan never asked for.
  - ⚠️ **Narrower than what they replaced:** "all cases for a doctor regardless of state" and `external_order_id IS NULL` are no longer index-assisted. Neither is a query the plan describes — add one back with evidence if a later phase needs it.
  - **`case_events(case_id, occurred_at)` reused from 0003, not duplicated.** A test asserts exactly one index exists on that column pair.
  - Round-trip passes (0004→0003→baseline→head, 3 upgrades / 2 downgrades); downgrade correctly restores the two plain indexes. Drift = 0. pg_trgm similarity proven live, not just parsed.
- Tests: **118 passing** (25 unit + 93 integration), coverage **85.86%**.

- ✅ **PHASE 1.3, first endpoint — `GET /api/encounters/{id}/discharge-readiness`.** Server-side only, derived from PostgreSQL on every call; no cache, no client-supplied `can_discharge`. Two SELECTs, scoped to the one encounter. Verified live through Caddy on :80 and cleaned up after — the dev database is back to 0 patients / 0 encounters / 0 orders.
  - **Two readings of the spec I had to settle, both from the source documents rather than invented:**
    1. **A contracted order stops blocking.** The plan defines blocking as `status NOT IN (final, cancelled, rejected)`, but Exit Gate 1 requires *"blocked → assign owners and dates → discharge succeeds"*. If contracted orders kept blocking, the gate could never be passed and the whole contract mechanism would be inert. So outstanding orders partition into `blocking_orders` (no contract) and `already_contracted` (has one); `can_discharge` is true when the first list is empty.
    2. **OPD is not gated.** ADR 0003 line 43 names this endpoint as checking `encounter.type IN ('ipd','emergency','daycare')`. For OPD the gate does not bind, so `can_discharge` is true — but the outstanding orders are **still reported**, because the data is true whether or not the gate applies. A `gate_applies` flag makes that explicit rather than leaving a self-contradictory response.
  - Response carries three extra fields beyond the plan's `{can_discharge, blocking_orders, already_contracted}`: `encounter_id`, `encounter_type`, `gate_applies`. A superset, not a change — the three specified keys are present and correct.
  - Soft-deleted orders and contracts are excluded. Orders on other encounters cannot influence the result — tested.
- Tests: **144 passing** (25 unit + 119 integration), coverage **86.98%**. 26 new, covering every order status the schema defines, both directions of the contract transition, cross-encounter isolation, 404/422 handling, and an explicit no-side-effects check (no contract, no pending case, no order or encounter mutated across repeated calls).

- ✅ **PHASE 1.3, second endpoint — `POST /api/encounters/{id}/discharge-contracts`.** Bulk, atomic, all-or-nothing. Every requested contract is validated before anything is written, and the 422 lists **all** violations rather than only the first — a doctor fixing the gate screen should see every problem at once.
  - **Duplicate protection is two-layered, deliberately.** A pre-flight SELECT gives a clean 409 for the ordinary case; `UNIQUE (order_id)` is what actually holds when two requests race, and an `IntegrityError` rolls the whole batch back to zero rows. A check-then-insert cannot be safe alone, and there is a test that inserts a contract behind the service's back to exercise exactly that path.
  - **Validations are only the three the plan names** — doctor active, `expected_by` future, `expected_by` ≤ 30 days (inclusive boundary) — plus structural checks: the order exists, is on this encounter, and is still outstanding. ⚠️ **The plan does not require a role check, so there is none: a `lab_tech` or `auditor` can currently be named responsible doctor.** Flagged rather than invented.
  - `expected_by` must be timezone-aware; a naive value is rejected. It becomes the Phase 2 SLA deadline, and a silent locale shift there is a deadline that fires at the wrong hour.
  - **No pending_case, no revision row, no order-status change.** The plan creates pending cases in the *discharge action* (line 79), and `discharge_contract_revisions` records *changes* to a contract — a creation is not a change. All three asserted by test.
  - ADR 0003: contracting an **OPD** encounter is refused (`encounter_not_gated`) — it would be an accountability record nothing ever acts on.
  - `app/errors.py` extended so a dict `detail` becomes RFC 7807 extension members instead of being stringified into the title. Without it the violation list arrived as a Python repr.
- Tests: **167 passing** (25 unit + 142 integration), coverage **86.41%**. 23 new. The concurrency test commits for real against two connections and cleans up after itself; the rest run inside a savepoint-scoped transaction. Dev database verified empty afterwards (0 rows in every clinical table).

- ✅ **PHASE 1.3, third endpoint — `POST /api/encounters/{id}/discharge`.** The action the product exists for. **Takes no body at all** — there is deliberately nothing a caller can send that influences the decision.
  - **Concurrency:** the encounter row is taken `SELECT ... FOR UPDATE` *before* anything is read, and the readiness re-check runs inside that lock. Two simultaneous discharges serialise; the loser wakes to `status='discharged'` and is refused. Proven with two independent connections via `asyncio.gather` — exactly one case, one event, one timer.
  - **One transaction, five writes**, including the pgmq enqueue. ADR 0001 chose a single Postgres precisely so this is possible: *"there is no window where the case exists and the timer does not."* There is **no** "commit then best-effort enqueue" anywhere. A forced failure at the enqueue rolls back the encounter, the cases and the events together — tested.
  - **Idempotency:** a second POST returns **409**, not a duplicate case set.
  - **Stale readiness cannot authorise a discharge** — a test fetches readiness showing `can_discharge: true`, inserts a new uncontracted order behind it, then posts and gets 409.
  - **Exit Gate 1 flow verified live through Caddy:** discharge → **409** with the blocking list → assign owner + deadline → discharge → **200**, one case, one event, one queued timer → repeat → **409**.
- 🔶 **Phase boundary, stated explicitly.** 1.3 enqueues the `result_due` wake-up on `pgmq` at `contract.expected_by`, using pgmq's own delay so nothing consumes it early — that is precisely what Phase 2.1 says to *"create on discharge"*. **Deferred to Phase 2:** the `sla_timers` table that carries the truth, the pg_cron overdue sweep, idempotent firing, cancellation/supersession, the missing-result lab flow, and the restart/chaos guarantees.
  - ⚠️ **Known residual race, for Phase 1.5.** The encounter lock serialises discharges and the order rows are locked, but an *entirely new* order inserted between the readiness check and the commit is not covered by row locks. Order creation must take the same encounter lock, or refuse to add orders to a non-active encounter. Not reachable today — manual order creation does not exist yet.
- Tests: **182 passing** (25 unit + 157 integration), coverage **87.44%**, drift 0. Dev database verified empty after every run. Note the cleanup in the committing tests must bypass the append-only trigger via session-scoped `session_replication_role` — `ALTER TABLE ... DISABLE TRIGGER` would persist if a test crashed.

- ✅ **PHASE 1.3 COMPLETE — fourth endpoint, `POST /api/encounters/{id}/discharge-overrides`.** The emergency path, and deliberately **not** an invisible bypass: every overridden investigation still gets a pending case, **flagged immediately**, **owned by the unit head**, with `flagged_at` set so the Phase 4 escalation clock starts.
  - **The schema answered two design questions rather than me inventing them.** `discharge_overrides.order_id` is NOT NULL → **one override row per uncontracted order**, not one per encounter. `approved_by` is nullable and the plan names no approval step → **no approval workflow**.
  - **Contracted orders on the same encounter are untouched** — they keep their own owner and their `result_due` timer. An override covers only what was bypassed; an investigation somebody already accepted must not lose its deadline because a different one was overridden.
  - **Refused when the department has no unit head** (409). *"Flagged to unit head immediately"* cannot be honoured without one, and an unowned flagged case is exactly the silent failure this product exists to prevent. ⚠️ **Operational consequence: a department must have `unit_head_user_id` set before its gate can be overridden.** Consistent with ADR 0004 making the unit head load-bearing.
  - **Refused when nothing is actually blocked** (409, "use POST /discharge"). Keeps the Phase 5.4 overrides report meaningful — every row in it is a real bypass.
  - Distinct event type `case_opened_via_override` carrying reason_code, reason_text, who overrode and which unit head it went to, so Phase 5 can tell a bypassed gate from a normal one at a glance.
  - Same locking and idempotency discipline as the discharge action: encounter `FOR UPDATE` first, repeat request → 409, rollback proven to leave the encounter active with zero overrides and zero cases.
- 🔶 **Deferred, and stated plainly: no notification is dispatched.** The plan says *"flagged to unit head immediately"*; 1.3 delivers that as **state** (case flagged + owned by the unit head), which is what the Phase 5 dashboard reads. Actual dispatch is Phase 4.3. I deliberately did **not** enqueue to the `notifications` queue — its consumer is still `_todo_handler`, a stub that logs and **deletes**, so a message posted now would be destroyed. Enqueueing would have been worse than not.
- Tests: **207 passing** (25 unit + 182 integration), coverage **87.50%**, drift 0. Live through Caddy: blocked 409 → short reason 422 → bad code 422 → override 201 → repeat 409, with the case flagged to the unit head. Dev database verified empty afterwards.

- 🔍 **FULL PHASE 0 + PHASE 1 VERIFICATION PASS (2026-09-12).** Not just pytest: infrastructure, migration integrity, schema catalogue, constraints, indexes, immutability, all four endpoints, seven end-to-end scenarios, thirteen bypass attempts, the error contract, worker/pgmq regression and NODE B degradation — driven through the **real API over Caddy on :80** against real PostgreSQL, then cleaned up.
  - **Result: 63/63 live checks pass. ZERO current defects.** 207 automated tests green, ruff/black/mypy-strict clean, coverage 87.50%, migration round-trip 3↓/3↑ with Phase 0 extensions, queues and the append-only trigger all surviving, autogenerate drift 0.
  - Bypass testing: `can_discharge:true`, `force:true`, unknown orders, cross-encounter orders, past/31-day deadlines, naive datetimes, extra JSON fields, empty batches, short override reasons, bad reason codes — **every one refused, and none created any state.**
  - Concurrency: two simultaneous discharges over HTTP → exactly one 200, one 409, one case, one event, one timer.
  - **One tracking defect found and fixed:** section 1.8's six integration tests were all written and passing but every checkbox was unticked — the exact "unlogged work counts as not done" failure CLAUDE.md warns about. Now ticked with the test name against each. The seventh (Playwright E2E) is genuinely blocked on 1.4.
  - One apparent failure turned out to be **test-harness**, not product: the audit script read psql's stdout while errors go to stderr, so the append-only checks looked like they passed when they had in fact been correctly refused. Re-verified with stderr captured — UPDATE and DELETE both rejected, row untouched.
- ⚠️ **Phase 1 is NOT complete.** 1.1/1.2/1.3 are done and audited; **1.4 (gate UI) and 1.5 (supporting screens) have not been started**, and 1.6/1.7 remain human-blocked. Exit Gate 1 cannot close on backend work alone.