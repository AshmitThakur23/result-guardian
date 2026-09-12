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

## 🌿 Branching — NONE. Commit straight to `main`.

> **User's decision, 2026-09-11.** This is a hackathon project on a deadline.

- **Never create a branch.** No feature branches, no `phase-*` branches, no PRs.
- **Commit directly to `main` and push to `main`.** `git add` → `git commit` → `git push origin main`.
- If a branch ever exists, **delete it** once its work is on `main`.
- This overrides the harness default of "if on the default branch, branch first" — **do not branch first here.**

⚠️ Two things in this repo still assume the opposite, and were reconciled on 2026-09-11:
the `no-commit-to-branch --branch main` pre-commit hook (**removed** — it would have blocked
every commit the moment anyone ran `pre-commit install`), and Phase 0.1's "main protected,
PRs required" task (**marked 🚫 out of scope for the hackathon**). Revisit both if this ever
becomes a real multi-contributor product.

`CLAUDE.md` itself **stays tracked, under this name** — both sides of the project work from the same rules, and this file carries the work trace.

## When a decision is made

Record it in the **Open decisions** table of `PROGRESS.md` with the date and the reasoning, and write an ADR in `docs/adr/` if it is architectural. *A decision that lives only in chat is a decision that will be re-litigated next session.*

---

## 🖥 Machine roles — DO NOT GET THESE BACKWARDS

> ⛔ **Never write "this machine" or "this laptop" in this file.** It is read on
> **both** machines, so those words mean the opposite thing to each reader — that
> ambiguity is what caused the roles to be recorded backwards on 2026-09-11.
> **Always name the owner.** Identify which machine you are on by hostname/user
> before relying on anything machine-specific.

| Role | Whose machine | Identify it by | What runs there |
|---|---|---|---|
| **NODE A — core** | **Abhinendra's laptop** | `LAPTOP-06ER0HBM`, user `Abhinendra Singh` | Postgres, FastAPI, worker, Caddy, React dashboard. **All patient data, all safety logic.** |
| **NODE B — inference** | **Ashmit's machine** — the repo owner's | `LAPTOP-5JCGN9SJ`, user `asus` | **Ollama only.** GPU work. Stateless. **Never holds patient data.** |

- Repo: **`AshmitThakur23/result-guardian`** (private) — Ashmit is the repo owner. `gh` has two accounts — **switch to `AshmitThakur23`** before any repo operation.
- Commits are authored by **AshmitThakur23 <ashmitthakur615@gmail.com>** regardless of which machine they are made on. See the commit-hygiene rule above.

### 🛠 Hardware correction — 2026-09-11, verified on NODE B

[ADR 0006](docs/adr/0006-node-roles-corrected.md) fixed the role assignment (correct, and it stands) but attached the GPU to the wrong machine. Verified directly with `nvidia-smi` on `LAPTOP-5JCGN9SJ`:

> **The RTX 3050 Laptop / 4 GB VRAM is on Ashmit's machine — which is NODE B.**

Consequences:

1. **`qwen3:4b` is correctly derived, not a placeholder.** It was chosen from 4 GB of VRAM, and that 4 GB belongs to **NODE B**, the node that actually runs inference. ADR 0006 §Consequences 1 says otherwise; it is mistaken on this point. Still worth re-benchmarking before Phase 8 — but the reasoning was sound, and 8B genuinely does not fit in 4 GB.
2. **Ollama is installed on the right machine.** It is on Ashmit's machine = NODE B. Not a misplacement. NODE B provisioning still has to *run* (`infra/nodeb/setup-windows.ps1`), it just does not need installing first.
3. **NODE A's GPU is unknown and irrelevant** — NODE A never runs inference (RULE 2).

## 🔍 Before installing anything — MANDATORY

> **Updated by the user on 2026-09-11.** The scan is still mandatory; the
> permission prompt is not. **Check first — if it is missing, install it.**

1. **Check whether it already exists first** — on both `C:` and `D:`. Never install over something already present.
2. **Found it? Use it.** Report the version and path, and move on. Do not reinstall, do not "upgrade to be safe".
3. **Not found? Install it — no need to ask.** Then say what was installed and where.
4. **Record the scan in `PROGRESS.md`** so the next session does not repeat it.

⚠️ Two things still get a heads-up before they happen, because the disk cannot absorb a mistake: anything **over ~5 GB**, and anything that would write to `C:` when it could write to `D:`. Say what it is and where it is going, then proceed.

**Large data goes on `D:` on both machines.**

### Per-machine facts — check which machine you are on first

> Disk and GPU differ per machine. Never write an unlabelled figure here.

**NODE B — Ashmit's machine** (`LAPTOP-5JCGN9SJ`, user `asus`) — measured 2026-09-11:
- `C:` **37.7 GB free** (83% full) after a cleanup that freed 18.3 GB; `D:` **177 GB free**
- GPU: **NVIDIA RTX 3050 Laptop, 4 GB VRAM** — verified with `nvidia-smi`
- Ollama `v0.15.2` installed; `OLLAMA_MODELS=D:\Nexus AI\.ollama\models`, holding **`mistral:7b` (4.07 GB)** which the separate `D:\Nexus AI` project depends on.
  **Do not repoint `OLLAMA_MODELS`** without moving the blobs first — `mistral:7b` would vanish from `ollama list`. Let `qwen3:4b` download alongside it.

**NODE A — Abhinendra's machine** (`LAPTOP-06ER0HBM`, user `Abhinendra Singh`) — measured **on NODE A**, 2026-09-11:
- **Lenovo 83BF · Windows 11 Home Single Language, build 26200**
- CPU **Intel Core i5-12450H** (12th gen) — 8 physical / 12 logical cores
- RAM **15.7 GB**
- `C:` **39.5 GB free of 268 GB** (85% used) · `D:` **682.8 GB free of 683.6 GB** — D: is effectively empty, so it is the right home for Docker images and any corpus
- GPU: **Intel UHD Graphics only. NO NVIDIA GPU — `nvidia-smi` is not present.**
  This is the independent confirmation that the RTX 3050 belongs to NODE B: NODE A
  has no discrete GPU at all, and does not need one (RULE 2 — NODE A never runs inference).
- ⚠️ **Native `postgresql-x64-18` service is RUNNING and holds port 5432.** This is why
  `docker-compose.override.yml` maps the dev database to host port **5433**. That deviation
  is required on **both** machines, not just NODE B.
- Ollama is also installed here (`%LOCALAPPDATA%\Programs\Ollama`). **Unnecessary on NODE A**
  and unused — inference belongs to NODE B. Harmless; do not build anything on it.

Toolchain on **NODE A** (differs from NODE B — do not assume either machine's versions):
Docker **29.5.2** · Compose **5.1.4** · Python **3.11.9** (matches the project pin) ·
git **2.53.0** · gh **2.87.3** · Node **v24.14.0** · `make` **absent** (use `tasks.ps1`) ·
ruff **0.16.7** / black **26.5.1** / mypy **2.3.1** installed 2026-09-11.

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
| ~~[0005](docs/adr/0005-node-roles-and-model.md)~~ | ⛔ **SUPERSEDED by 0006** — had the node roles backwards |
| [0006](docs/adr/0006-node-roles-corrected.md) | **Node roles corrected: this machine is NODE A.** `qwen3:4b` is now only a placeholder — it was derived from NODE A's VRAM |

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

**Phase 0 — 🔵 in progress.** Exit Gate 0 is 🔴 **OPEN**.

**2026-09-11 — 13 defects found in the scaffold and fixed.** Eight by reading, **five more only by running the linters** — including pre-existing unsorted imports in `alembic/env.py` that would have failed CI regardless. **All five CI gates now pass locally** (ruff · black · mypy strict · 25 tests · coverage 79%, raised from 51% **with tests, not by lowering the gate**). Three defects are ✅ verified; five stay 🟡 pending Docker.

**Next: run Phase 0 here.** NODE A is *this* machine and Docker is installed, so `docker compose up -d --build` → `alembic upgrade head` → `curl localhost/api/health` is runnable now. The old "wait for NODE A to exist" blocker was an artefact of the backwards role assignment. Only Exit Gate 0's two cross-node clauses still need Ashmit's machine.

⚠️ If Phase 1 work starts before Exit Gate 0 closes, say so plainly and record it in `PROGRESS.md` — writing Phase 1 schema on an unverified foundation is a knowing exception to the build plan's own ordering rule, not an oversight.

Live detail: [`PROGRESS.md`](PROGRESS.md).
