# Result Guardian — Progress Ledger

> **Read this first, every session.** It is the resume point.
> Updating it after every unit of work is mandatory — see the protocol in [`CLAUDE.md`](CLAUDE.md).

**Last updated:** 2026-09-12

> ## ▶ RESUME HERE
>
> **Repo:** https://github.com/AshmitThakur23/result-guardian (private, `main`)
>
> **Machines** — never write "this machine"; name the owner.
> **NODE A = Abhinendra's laptop**, `LAPTOP-06ER0HBM`. Docker present. No NVIDIA GPU.
> **NODE B = Ashmit's machine**, `LAPTOP-5JCGN9SJ` — **not yet provisioned**. RTX 3050, 4 GB VRAM.
> See [ADR 0006](docs/adr/0006-node-roles-corrected.md), which supersedes ADR 0005.
>
> **🏁 CI is GREEN — all 3 jobs (2026-09-12, run `34670777455`).** All nine defects D1–D9
> are **verified**, not merely fixed. The NODE A Postgres image builds and runs with
> pgmq 1.4.4 + pg_cron 1.6 + all six extensions, and migrations apply cleanly.
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
> ⚠️ **Phase 1 as a whole is NOT complete:** 1.5 (supporting screens) is not started,
> 1.8's E2E is 🟡, and Exit Gate 1 stays ⬜ until they are.
> **Next: 1.5 Supporting screens** — patient search, encounter detail, manual order
> creation, discharge medication entry, and the 20-patient seed script.
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
| [0 · Foundation](docs/build/phase-00-foundation.md) | A + B | 🔵 **in progress** | 🔴 **OPEN** | **CI green; D1–D9 all verified.** Full compose stack still unrun; NODE B unprovisioned. [Status table](docs/build/phase-00-foundation.md) |
| [1 · Data model + discharge gate ★](docs/build/phase-01-data-model-discharge-gate.md) | A | 🔵 **in progress — 1.1–1.4 done** | ⬜ | 2–3 wks. **This is the product.** Backend ✅ + gate UI ✅. Remaining: 1.5 screens, 1.8 E2E 🟡, 1.6 corpus + 1.7 vendor 🔴 |
| [2 · Durable timers](docs/build/phase-02-durable-timers.md) | A | ⬜ not started | ⬜ | 1–1.5 wks |
| [3 · Clinical rule engine](docs/build/phase-03-clinical-rule-engine.md) | A | ⬜ not started | ⬜ | 2–3 wks. **Book clinician time now** |
| [4 · Ownership + escalation ★](docs/build/phase-04-ownership-escalation.md) | A | ⬜ not started | ⬜ | 2–3 wks. Alert fatigue controls ship in the same sprint |
| [5 · Dashboard, closure, audit](docs/build/phase-05-dashboard-audit-mvp.md) | A | ⬜ not started | ⬜ | 2–3 wks → 🏁 **MVP, pilot ready** |
| [6 · Document ingestion](docs/build/phase-06-document-ingestion.md) | A | 🔴 blocked | ⬜ | 2 wks. ⛔ blocked by 1.6 corpus |
| [7 · Extraction + matching](docs/build/phase-07-extraction-matching.md) | A (+B) | ⬜ not started | ⬜ | 3 wks |
| [8 · RAG explanation](docs/build/phase-08-rag-explanation.md) | A + B | ⬜ not started | ⬜ | 3 wks. First phase that uses NODE B |
| [9 · Hospital integration](docs/build/phase-09-hospital-integration.md) | A | 🔴 blocked | ⬜ | 3–5 wks. ⛔ gated by hospital IT / HIS vendor |
| [10 · Security + production](docs/build/phase-10-security-production.md) | A + B | ⬜ not started | ⬜ | 3 wks + external test turnaround |

**Timeline reference:** MVP at 11 weeks (team of 3) / 21 weeks (solo). Production at 25 / 46 weeks.

---

## ⏳ Long-lead items — calendar-gated, start regardless of phase

These are not blocked by code. They are blocked by other people, and they take months. **The build plan says to start the top two in week one of Phase 1.**

| Item | Source | Blocks | Status | Started |
|---|---|---|---|---|
| De-identified corpus, 200+ real reports | 1.6 | **Phase 6 cannot start without it** | 🔴 **opened, 0/200** → [manifest](docs/test-corpus-manifest.md) | 2026-09-12 |
| HIS/LIS vendor conversation | 1.7 | **Phase 9** | 🔴 **opened, all unanswered** → [spec](docs/integration-spec.md) | 2026-09-12 |
| Clinician time — gold set, 100+ results | 3.8 | **Exit Gate 3** | `not started` | — |
| Clinician time — 100-question AI eval set | 8.7 | **Exit Gate 8** | `not started` | — |
| Medico-legal liability policy, signed | 10.3 | **Go-live** | `not started` | — |
| Hospital's own critical-value list (for `panic_thresholds`) | 3.2 | Phase 3 seeding | `not started` | — |

---

## ❓ Open decisions

Record the decision, the date, and the reasoning. A decision that lives only in chat gets re-litigated.

| # | Decision | Forced by | Status |
|---|---|---|---|
| 1 | **OPD scope** → **OUT for v1, schema stays ready.** `encounters.type` keeps `opd` so no migration is needed later; the gate binds to `ipd\|emergency\|daycare`. No reliable visit-closure trigger exists, and OPD volume would blow the 15% alert-fatigue ceiling before thresholds are tuned. Safe because Phase 7.5 routes caseless results to an orphan queue. | Phase 1 scope note | ✅ **decided 2026-09-11** → [ADR 0003](docs/adr/0003-opd-out-of-scope-v1.md) |
| 2 | **`duty_roster` owner** → **the unit head, weekly.** HR sync is Phase 9.4 (after MVP) so it cannot be the v1 answer. The unit head is rung 2 of the ladder, so a stale roster escalates to the person maintaining it. Ships with a weekly reminder, a stale badge, and fallthrough to unit head. | Phase 4.1 | ✅ **decided 2026-09-11** → [ADR 0004](docs/adr/0004-duty-roster-ownership.md) |
| 3 | **Node roles** → ~~this laptop is NODE B~~ **CORRECTED: Abhinendra's laptop (`LAPTOP-06ER0HBM`) is NODE A; Ashmit's (`LAPTOP-5JCGN9SJ`) is NODE B.** The original entry was backwards. | Phase 0.4 | ✅ **re-decided 2026-09-11** → [ADR 0006](docs/adr/0006-node-roles-corrected.md), superseding [ADR 0005](docs/adr/0005-node-roles-and-model.md) |
| 4 | **Model** → **`qwen3:4b`. A sound decision, not a placeholder.** It was derived from **4 GB of VRAM on the machine that actually runs inference** — `nvidia-smi` on Ashmit's `LAPTOP-5JCGN9SJ` confirms the RTX 3050 is on **NODE B**, and NODE A has no NVIDIA GPU at all (Intel UHD only). 8B q4 (~5–6 GB) genuinely does not fit. Re-benchmark before Phase 8 as the build plan says; `RG_LLM_MODEL` is an env var. | Phase 8.4 | ✅ **decided 2026-09-11, re-confirmed 2026-09-12** → [ADR 0006 correction](docs/adr/0006-node-roles-corrected.md). *Was briefly reopened on a wrong GPU attribution; that is now resolved.* |
| 5 | Real LAN IPs for NODE A / NODE B, and which network method (router vs hotspot vs direct ethernet) | Phase 0.4 | ⬜ **undecided** — fill into [`docs/network-runbook.md`](docs/network-runbook.md) once both machines are on one network |

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