# Result Guardian — Progress Ledger

> **Read this first, every session.** It is the resume point.
> Updating it after every unit of work is mandatory — see the protocol in [`CLAUDE.md`](CLAUDE.md).

**Last updated:** 2026-09-11

> ## ▶ RESUME HERE
>
> **Repo:** https://github.com/AshmitThakur23/result-guardian (private, `main`)
>
> **🔄 ROLES CORRECTED 2026-09-11 — they were recorded backwards.**
> **NODE A = the development laptop (Abhinendra's), the machine in hand.**
> **NODE B = Ashmit's machine** — not provisioned, **GPU unknown**.
> See [ADR 0006](docs/adr/0006-node-roles-corrected.md), which supersedes ADR 0005.
>
> **⚠️ This moves the critical path.** The old plan — *"stand up NODE A on the other
> machine first, run nothing until then"* — was written believing NODE A was elsewhere.
> **NODE A is this machine, and Docker is already on it.** Phase 0 execution
> (`cp .env.example .env` → `docker compose up -d --build` → `alembic upgrade head` →
> `curl localhost/api/health`) is **runnable here, now**, and closes 6 of the 8 items on
> the first-boot checklist below. Only the two cross-node clauses need Ashmit's machine.
>
> **Next step: decide whether to execute Phase 0 on this machine.** Nothing structural
> blocks it any more.
>
> Phase 0 code is **written and pushed but has never been executed**. See the status table
> in [`docs/build/phase-00-foundation.md`](docs/build/phase-00-foundation.md).
>
> **2026-09-11 — 8 defects found by inspection and fixed, all still unrun.** Two were
> load-bearing (`/api/health` 503'd on a missing table; pg_cron vanished silently if the
> database was renamed), and **CI could not have passed at all**. Work the
> **✅ First-boot checklist for NODE A** below — it is what turns 🟡 into ✅.

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
| [0 · Foundation](docs/build/phase-00-foundation.md) | A + B | 🔵 **in progress** | 🔴 **OPEN** | Code written + pushed, **never executed**. Blocked on NODE A existing. [Status table](docs/build/phase-00-foundation.md) |
| [1 · Data model + discharge gate ★](docs/build/phase-01-data-model-discharge-gate.md) | A | ⬜ not started | ⬜ | 2–3 wks. **This is the product.** Also kicks off 1.6 corpus + 1.7 vendor |
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
| De-identified corpus, 200+ real reports | 1.6 | **Phase 6 cannot start without it** | `not started` | — |
| HIS/LIS vendor conversation | 1.7 | **Phase 9** | `not started` | — |
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
| 3 | **Node roles** → ~~this laptop is NODE B~~ **CORRECTED: this development laptop is NODE A; Ashmit's machine is NODE B.** The original entry was backwards. | Phase 0.4 | ✅ **re-decided 2026-09-11** → [ADR 0006](docs/adr/0006-node-roles-corrected.md), superseding [ADR 0005](docs/adr/0005-node-roles-and-model.md) |
| 4 | **Model** → `qwen3:4b` is now a **PLACEHOLDER, not a decision.** It was derived from the RTX 3050's 4 GB VRAM — hardware that belongs to **NODE A**, where inference never runs. **NODE B's GPU is unknown.** Re-derive from Ashmit's machine before Phase 8. Costs nothing now: `RG_LLM_MODEL` is an env var and nothing touches NODE B until Phase 8. | Phase 8.4 | 🔴 **REOPENED 2026-09-11** — needs NODE B's specs → [ADR 0006](docs/adr/0006-node-roles-corrected.md) |
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
- ⚠️ **Nothing was executed.** Per the user's instruction, no local run: the "no Phase 0 execution on NODE B until NODE A is up" agreement stands. ruff, black and mypy are **not installed** on this laptop and were **not installed** to check — so `black --check` and `mypy --strict` outcomes are predictions, not results. What *was* verified locally is only what needs no install: every Python file parses (`ast.parse`), all four YAML files parse, and no line exceeds 88 characters. **All eight fixes are 🟡, not ✅.**
- **🔄 NODE ROLES CORRECTED BY THE USER — they were recorded backwards.** **This development laptop is NODE A** (core: Postgres, API, worker, dashboard, all patient data). **NODE B is Ashmit's machine** (Ollama, GPU, stateless). Written into `CLAUDE.md` as the authoritative table, and swept through `PROGRESS.md`, `README.md`, `docs/network-runbook.md`, `docs/build/phase-00-foundation.md` and `docs/adr/0002`. **ADR 0005 is marked SUPERSEDED and deliberately left in place** — its wrong assignment is kept as the record that the error happened, not edited away. [ADR 0006](docs/adr/0006-node-roles-corrected.md) carries the correction.
  - ⚠️ **Decision #4 (`qwen3:4b`) is reopened.** It was derived from this machine's 4 GB VRAM — NODE A's hardware, where inference never runs. NODE B's GPU is unknown. Placeholder until re-derived; costs nothing before Phase 8.
  - ⚠️ **The critical path moved.** "Wait for NODE A" assumed NODE A was the *other* machine. It is this one, and Docker is on it. **Phase 0 is executable here now.**
- **Install rule relaxed by the user:** scan both drives first; if absent, install without asking. Recorded in `CLAUDE.md`.
- **Direct-to-`main` workflow adopted** (user decision). No branches, no PRs. The short-lived `phase-0-verification` branch was fast-forwarded into `main` and deleted, local and remote. Removed the `no-commit-to-branch --branch main` pre-commit hook — dormant only because `pre-commit install` had never been run, it would have blocked every commit the moment it was. Phase 0.1's "main protected, PRs required" is now 🚫 out of scope.
- **CI ran for the first time ever.** `lint` ✅ and `build` ✅ — confirming **D1, D8, D6 and D2** on a clean Ubuntu runner (the build job proves the runtime image ships no pytest/ruff/mypy). The 5 prior runs on `main` had all failed at `pip install -e ".[dev]"` with *"Multiple top-level packages discovered in a flat-layout"* — D1, verbatim, exactly as diagnosed.
- 🐞 **D9 found by CI and fixed — `infra/postgres/Dockerfile` shipped a broken pgmq.** `test` failed with *"extension pgmq has no installation script nor update path for version 1.4.4"*, killing the Postgres container at init. **Root cause:** pgmq's tarball contains only *upgrade* scripts (`pgmq--X--Y.sql`); the base `pgmq--1.4.4.sql` is **generated** by the Makefile's `all` target from `sql/pgmq.sql`. PGXS's `install` does not depend on `all`, and `DATA = $(wildcard sql/*--*.sql)` is expanded at parse time — so the base script was neither generated nor listed. The image built cleanly while being unusable. Fixed by generating the base script before `make install`, deriving the version from `pgmq.control` (not the git tag), and **asserting both files exist at build time** so the build fails instead of the first boot.
- ✅ **D9 verified on NODE A with real Docker** — image builds, container starts in ~6 s and stays healthy, and `pg_extension` contains all seven: **pgmq 1.4.4**, **pg_cron 1.6**, vector 0.8.6, pg_trgm, unaccent, pgcrypto, plpgsql. All five queues created; `pgmq.send`/`pgmq.read` round-trip works. `cron.database_name` correctly resolved to the database — **which also verifies the D3 guard**.
- **Recorded NODE A's real machine specs** (see the scan table above) — `LAPTOP-06ER0HBM`, i5-12450H, 15.7 GB RAM, C: 39.5 GB free / D: 683 GB free. **NODE A has no NVIDIA GPU at all** (Intel UHD only, no `nvidia-smi`), independently confirming Ashmit's `nvidia-smi` finding that the RTX 3050 is on **NODE B**. Also found the native `postgresql-x64-18` service holding **port 5432 on NODE A too**, so the dev 5433 mapping is required on both machines — not a NODE-B-only quirk as previously recorded.
- **Installed the lint/test toolchain and actually ran it** (new install rule — scanned first, all absent, installed without asking). `ruff 0.16.7`, `black 26.5.1`, `mypy 2.3.1`, plus `pip install -e ".[dev]"`. Python here is **3.11.9**, matching the project pin. **`C:` now has 43 GB free, not the 17 GB in the old scan** — that figure was stale.
- **Running it found 5 more defects that reading had missed** — the clearest possible evidence for *written ≠ done*. Notably `alembic/env.py` had **pre-existing unsorted imports**, so `ruff check .` would have failed CI even after the D8 fix. Also 2 × `RUF100` unused pragmas, 2 × `SIM105`, and 2 mypy-strict errors (`2 ** n` types as `Any`; an unused `type: ignore`). All fixed.
- **✅ All five CI gates now pass on this machine:** `ruff` clean · `black --check` 27 files unchanged · `mypy --strict` clean across 23 files · **25 tests pass** · coverage **79.17%**.
- **The coverage gate did trip, at 50.78%** — exactly the risk flagged. Closed it **with tests, not by lowering the number**, per the standing rule. Added 19 tests: `worker/consumer.py` was at **0%** despite holding the pgmq retry/backoff/DLQ logic that Phase 2's durability rests on; the heartbeat and its failure path were untested; the Phase 0.6 conventions were unasserted. Now 79%.
- **Three of the eight defects are now genuinely ✅** (D1 packaging, D4 health-probe split at unit level, D8 lint). **Five stay 🟡** — D2, D3, D5, D6, D7 all need Docker.
- **Next:** run Phase 0 on this machine (NODE A) — `docker compose up -d --build` → `alembic upgrade head` → `curl localhost/api/health`. That closes 6 of the 8 first-boot checks and the remaining five defects. Only the two cross-node clauses need Ashmit's machine.

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
