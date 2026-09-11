# Result Guardian — Project Instructions

## What this is

An on-premise hospital system that guarantees **no post-discharge investigation result is ever lost**. A doctor physically cannot complete a discharge while a test has no responsible owner and no expected-by date. Pending results are tracked by database timers, classified by a deterministic Python rule engine, and escalated through a five-rung ladder that ends at the patient if nobody acts. It runs on two machines on a private LAN with no internet: **NODE A** (core — Postgres, FastAPI, worker, dashboard, all patient data) and **NODE B** (a stateless GPU box running Qwen 3 via Ollama, used only when a doctor clicks "Explain").

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

## 🖥 Machine roles — DO NOT GET THESE BACKWARDS

| Role | Machine | What runs there |
|---|---|---|
| **NODE B — inference** | **This laptop** (the one with the NVIDIA RTX 3050, 4 GB VRAM) | **Ollama only.** `qwen3:4b`. Stateless. Never holds patient data. |
| **NODE A — core** | **The other laptop** | Postgres, FastAPI, worker, Caddy, React dashboard. All patient data, all safety logic. |

- This laptop is **NODE B**. Ollama is already installed here (`v0.15.2`, `%LOCALAPPDATA%\Programs\Ollama`).
- The GPU is 4 GB, so the model is **`qwen3:4b`**, not the 8B in the build plan — 8B does not fit. `LLM_MODEL` is an env var; swap it on a real GPU box later.
- NODE A code is authored here and pushed to GitHub; the other laptop pulls and runs it.
- Repo: `AshmitThakur23/result-guardian` (private).

## 🔍 Before installing anything — MANDATORY

Standing instruction from the user:

1. **Check whether it already exists first** — on both `C:` and `D:`. (Ollama, Docker, Postgres, Python, Node, git, gh were all already installed; nothing needed installing.)
2. **Report what was found, then ask once** before installing, downloading or pulling anything. The user may verify manually and confirm.
3. **Record the scan result in `PROGRESS.md`** so the next session does not repeat it.

Also note: `C:` is ~93% full (17 GB free), `D:` has 178 GB. Large data — Docker images, Ollama models — goes on `D:`.

## 📋 Work-logging protocol — MANDATORY

The context window resets. These files are the only memory that survives.

**Before starting any work:**
1. Read [`PROGRESS.md`](PROGRESS.md) — it says what is done and what is next.
2. Read the relevant `docs/build/phase-NN-*.md` — it is the task list.
3. Never start a phase whose predecessor's `## ✅ EXIT GATE` still has unticked boxes.

**After every completed unit of work — not at end of session, not "later":**
1. Tick the `- [ ]` → `- [x]` checkbox in the phase doc.
2. Append a dated line to the **Session log** in `PROGRESS.md`.
3. Update the phase's row in the `PROGRESS.md` status table if its status changed.

**When a decision is made** (e.g. OPD scope, who maintains the duty roster), record it in the **Open decisions** section of `PROGRESS.md` with the date and the reasoning. A decision that lives only in chat is a decision that will be re-litigated next session.

**If work was done but not logged, treat it as not done** — verify before claiming it.

---

## 🔒 Locked decisions — do not re-litigate

The tech stack is decided in [`docs/build/01-tech-stack-and-repo-layout.md`](docs/build/01-tech-stack-and-repo-layout.md). Do not propose alternatives mid-build. In particular:

- **One Postgres instance** carries everything: data, vectors (pgvector), queue + timers (pgmq), sweeps (pg_cron), fuzzy text (pg_trgm/unaccent), keyword search (tsvector). **No Redis, no RabbitMQ, no Elasticsearch.**
- Python 3.11 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 async · Alembic · structlog
- React 18 + TS · Vite · Tailwind · shadcn/ui · TanStack Query
- Ollama + Qwen3-Instruct on NODE B only. Embeddings (bge-m3) and reranking stay on NODE A's CPU.

**Base conventions — costly to change later, so honour them in every migration:**

- All PKs are **UUIDv7** (time-sortable)
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
| What's done / what's next | [`PROGRESS.md`](PROGRESS.md) |
| Full doc index | [`docs/README.md`](docs/README.md) |
| Two-node topology, LAN setup, env vars | [`docs/architecture/00-two-node-topology.md`](docs/architecture/00-two-node-topology.md) |
| What happens at runtime, step by step | `docs/architecture/01`–`05` |
| What to build, phase by phase | `docs/build/phase-00` … `phase-10` |
| Locked stack + repo layout | [`docs/build/01-tech-stack-and-repo-layout.md`](docs/build/01-tech-stack-and-repo-layout.md) |
| Degradation ladder | [`docs/build/99-gaps-timeline-degradation.md`](docs/build/99-gaps-timeline-degradation.md) |

The two root PDFs are **archive**. Everything in them is in `docs/`. Don't re-extract them for routine work.

---

## Current state

**Knowledge base complete. No code written yet.** Phase 0 has not started. See [`PROGRESS.md`](PROGRESS.md).
