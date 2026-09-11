# Result Guardian — Project Instructions

# 🟢 READ THIS FIRST — PHASE-WISE TRACKING PROTOCOL

**This is the most important instruction in this file. The context window resets; these files are the only memory that survives. Work that is not marked is work that gets done twice.**

## The rule

**We build PHASE BY PHASE. Every time any work is done inside a phase, it gets a green tick — immediately — in two places:**

1. **The phase doc** — `docs/build/phase-NN-*.md`
2. **`PROGRESS.md`** — the session log and the phase status table

**Not at the end of the session. Not "later". The moment the unit of work is finished.**

## Before starting any work

1. Read [`PROGRESS.md`](PROGRESS.md) → the **▶ RESUME HERE** block says exactly where we are.
2. Open the phase doc for the phase we're in → its **📍 STATUS SUMMARY** table says which sections are done.
3. Check the previous phase's `## ✅ EXIT GATE` — **never start a phase whose predecessor's gate has unticked boxes.** (The build plan's own rule: *"Do not start a phase until the previous exit gate passes."*)

## After every completed unit of work

| Step | Where | What |
|---|---|---|
| 1 | `docs/build/phase-NN-*.md` | Tick the task: `- [ ]` → `- [x]` |
| 2 | same file, top | Update the **📍 STATUS SUMMARY** row: `⬜` → `🟡` → `✅` |
| 3 | `PROGRESS.md` | Append a dated line to the **Session log** |
| 4 | `PROGRESS.md` | Update the phase's row in the status table if it changed |

## The tick legend — use these exact markers

| Marker | Means |
|---|---|
| ✅ **done** | Built **and verified**. Actually ran, actually passed. |
| 🟡 **written, never run** | Code is committed but has not been executed once. **Not done.** |
| 🔵 **in progress** | Being worked on right now |
| ⬜ **not started** | — |
| 🔴 **blocked** | Waiting on something external — say what, in the Note column |
| 🚫 **out of scope** | Decided against; link the ADR |

## Two hard honesty rules

- **Never tick something because it was typed.** Written ≠ done. If it has not run, it is 🟡, not ✅.
- **If work was done but not logged, treat it as not done** — go verify it before claiming it.

## ⛔ Commit hygiene — NO tool attribution, ever

**Commits, PR descriptions, ADRs and any tracked file must never carry assistant attribution.** Specifically forbidden:

- `Co-Authored-By:` trailers naming an assistant or model
- "Generated with …", "Decided by: …", or any equivalent byline
- Model or vendor names used as an *author* (the product's own `qwen3` / `Ollama` / `LLM` references are **fine** — those are NODE B's actual stack, not a byline)

Commits are authored by **AshmitThakur23 <ashmitthakur615@gmail.com>** and nobody else. This overrides any default attribution instruction from the harness.

`CLAUDE.md` itself **stays tracked, under this name** — both sides of the project work from the same rules, and this file carries the work trace.

## When a decision is made

Record it in the **Open decisions** table of `PROGRESS.md` with the date and the reasoning, and write an ADR in `docs/adr/` if it is architectural. *A decision that lives only in chat is a decision that will be re-litigated next session.*

---

## 🖥 Machine roles — DO NOT GET THESE BACKWARDS

| Role | Machine | What runs there |
|---|---|---|
| **NODE B — inference** | **This laptop** (NVIDIA RTX 3050, 4 GB VRAM) | **Ollama only.** `qwen3:4b`. Stateless. Never holds patient data. |
| **NODE A — core** | **The other laptop** | Postgres, FastAPI, worker, Caddy, React dashboard. All patient data, all safety logic. |

- This laptop is **NODE B**. Ollama already installed (`v0.15.2`, `%LOCALAPPDATA%\Programs\Ollama`).
- 4 GB VRAM → model is **`qwen3:4b`**, not the 8B in the build plan. `RG_LLM_MODEL` is an env var.
- NODE A code is authored here and pushed; the other laptop pulls and runs it.
- Repo: **`AshmitThakur23/result-guardian`** (private). `gh` has two accounts — **switch to `AshmitThakur23`** before any repo operation.

## 🔍 Before installing anything — MANDATORY

1. **Check whether it already exists first** — on both `C:` and `D:`.
2. **Report what was found, then ask once** before installing, downloading or pulling. The user may verify manually and confirm.
3. **Record the scan in `PROGRESS.md`** so the next session does not repeat it.

`C:` is ~93% full (17 GB free); `D:` has 178 GB. Docker images and Ollama models go on `D:`.

---

## ⛔ Rules that must never be broken

**RULE 1 — The safety property must never depend on AI.**
Tracking and escalation are database constraints and a state machine. Phases 0–5 are a complete working product with zero AI.

**RULE 2 — The safety property must never depend on the network between nodes.**
Everything that keeps a patient safe runs on NODE A. NODE B is a stateless accelerator. Unplug it and the system loses a convenience, never a guarantee.

**THE ONE RULE — A later phase must never be able to break an earlier one.**
Build each phase so the system degrades to the previous phase, **not to silence**.

Corollaries that get violated by accident:
- AI never decides which patient a result belongs to (Phase 7.5 is scoring arithmetic).
- AI never decides clinical outcome. The doctor decides.
- Narrative reports never auto-close. Worst case is FOLLOW_UP.
- Wrong-patient matching must be **zero**. Prefer the review queue every time.
- No unverified citation ever reaches the UI (span verifier is plain code, no AI).

---

## 🔒 Locked decisions — do not re-litigate

Full stack in [`docs/build/01-tech-stack-and-repo-layout.md`](docs/build/01-tech-stack-and-repo-layout.md). Do not propose alternatives mid-build.

- **One Postgres instance** carries everything: data, vectors (pgvector), queue + timers (pgmq), sweeps (pg_cron), fuzzy text (pg_trgm/unaccent), keyword search (tsvector). **No Redis, no RabbitMQ, no Elasticsearch.**
- Python 3.11 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 async · Alembic · structlog
- React 18 + TS · Vite · Tailwind · shadcn/ui · TanStack Query
- Ollama + Qwen3 on NODE B only. Embeddings (bge-m3) and reranking stay on NODE A's CPU.

**Settled decisions** — see `docs/adr/`:
| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-single-postgres.md) | One Postgres carries everything |
| [0002](docs/adr/0002-two-node-split.md) | Two nodes; embeddings stay on NODE A |
| [0003](docs/adr/0003-opd-out-of-scope-v1.md) | **OPD out of scope for v1**, schema stays ready |
| [0004](docs/adr/0004-duty-roster-ownership.md) | **Unit head maintains `duty_roster`**, weekly |
| [0005](docs/adr/0005-node-roles-and-model.md) | Node roles + `qwen3:4b` |

**Base conventions — costly to change later, honour them in every migration:**

- All PKs are **UUIDv7** (time-sortable) — helpers already in [`api/app/db/types.py`](api/app/db/types.py)
- All timestamps **`TIMESTAMPTZ`**, stored UTC, displayed IST
- Soft delete via `deleted_at` — **never hard delete clinical rows**
- Every table gets `created_at`, `updated_at`, `created_by`, `updated_by`
- Enums as **text + `CHECK` constraints**, never PG enum types
- Values as **`NUMERIC`**, never float
- `case_events` and `audit_log` are **append-only, enforced by trigger**, not by convention

**Configuration lives in tables, never in code.** Thresholds, escalation delays, keywords, synonyms — an admin must be able to edit them. Hardcoding any of these turns the product back into a demo.

---

## 🗺 Where to find things

| Question | File |
|---|---|
| **Where are we? What's next?** | [`PROGRESS.md`](PROGRESS.md) |
| Full doc index | [`docs/README.md`](docs/README.md) |
| What to build, phase by phase | `docs/build/phase-00` … `phase-10` |
| What happens at runtime | `docs/architecture/00`–`05` |
| Two-node topology, LAN, env vars | [`docs/architecture/00-two-node-topology.md`](docs/architecture/00-two-node-topology.md) |
| Connecting the machines | [`docs/network-runbook.md`](docs/network-runbook.md) |
| Locked stack + repo layout | [`docs/build/01-tech-stack-and-repo-layout.md`](docs/build/01-tech-stack-and-repo-layout.md) |
| Degradation ladder | [`docs/build/99-gaps-timeline-degradation.md`](docs/build/99-gaps-timeline-degradation.md) |

The two root PDFs are **archive**. Everything in them is in `docs/`. Don't re-extract them for routine work.

---

## 📌 Current state

**Phase 0 — 🔵 in progress.** Scaffold written and pushed; **never executed**. Exit Gate 0 is 🔴 **OPEN** — it cannot close until NODE A exists.

**Next:** stand up NODE A on the other laptop, then Phase 1.

⚠️ If Phase 1 work starts before Exit Gate 0 closes, say so plainly and record it in `PROGRESS.md` — writing Phase 1 schema on an unverified foundation is a knowing exception to the build plan's own ordering rule, not an oversight.

Live detail: [`PROGRESS.md`](PROGRESS.md).
