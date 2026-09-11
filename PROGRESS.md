# Result Guardian — Progress Ledger

> **Read this first, every session.** It is the resume point.
> Updating it after every unit of work is mandatory — see the protocol in [`CLAUDE.md`](CLAUDE.md).

**Last updated:** 2026-09-11

> ## ▶ RESUME HERE
>
> **Repo:** https://github.com/AshmitThakur23/result-guardian (private, `main`)
> **NODE B** = the RTX 3050 laptop. **NODE A** = the other machine — **not yet provisioned**.
>
> **Next step, agreed with the user:** stand up **NODE A on the other machine first**
> (`git clone` → `cp .env.example .env` → `docker compose up -d --build` → `curl localhost/api/health`),
> *then* coordinate the two machines. **Do not run further Phase 0 tasks on NODE B until NODE A is up.**
>
> Phase 0 code is **written and pushed but has never been executed**. See the status table
> in [`docs/build/phase-00-foundation.md`](docs/build/phase-00-foundation.md).

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
| 3 | **Node roles** → this RTX 3050 laptop is **NODE B**; the other machine is **NODE A**. | Phase 0.4 | ✅ **decided 2026-09-11** → [ADR 0005](docs/adr/0005-node-roles-and-model.md) |
| 4 | **Model** → **`qwen3:4b`**, not 8b. 4 GB VRAM cannot hold 8B q4 (~5–6 GB); it would spill to CPU and trip the 30 s timeout. `RG_LLM_MODEL` is an env var — re-benchmark on the hospital GPU box at Phase 8. | Phase 8.4 | ✅ **decided 2026-09-11** → [ADR 0005](docs/adr/0005-node-roles-and-model.md) |
| 5 | Real LAN IPs for NODE A / NODE B, and which network method (router vs hotspot vs direct ethernet) | Phase 0.4 | ⬜ **undecided** — fill into [`docs/network-runbook.md`](docs/network-runbook.md) once both machines are on one network |

---

## 🔍 Pre-install scan — 2026-09-11

Per the standing rule in `CLAUDE.md`: check both drives before installing anything. **Nothing needed installing.**

| Tool | Found | Note |
|---|---|---|
| Ollama | ✅ `v0.15.2`, `%LOCALAPPDATA%\Programs\Ollama` | not running; configure only |
| Docker + Compose | ✅ `29.7.2` / `v5.4.0` | daemon stopped |
| Git / gh CLI | ✅ `2.49.0` / `2.78.0` | `AshmitThakur23` was inactive — switched |
| Python / Node | ✅ `3.13.7` / `v22.18.0` | API runs 3.11 **in the container** |
| PostgreSQL | ⚠️ native install present | dev compose uses host port **5433** to avoid the clash |
| `make` | ❌ absent | `tasks.ps1` mirrors the `Makefile` |
| GPU | RTX 3050, **4 GB VRAM** | drives decision #4 |
| Disk | ⚠️ **C: 93% full (17 GB)**, D: 178 GB | Ollama models → `D:\ollama-models`; move Docker disk image to D: too |

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
- **Next:** stand up **NODE A on the other machine**, then verify across both. Per the user: no further Phase 0 execution on NODE B until then.
