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

## ⚙️ A config value is not verified until something has READ it

> **Added 2026-09-14 after the same defect appeared three times in one day.**
> It is "written ≠ done" one layer down, and it is harder to spot, because the
> value *is* there when you look at it.

**Setting a value proves nothing. Only the consuming process proves it.** Ask the
thing that reads the config what it actually has — never the file, never the
environment you set it in.

The three instances, all on 2026-09-14:

| # | Where | What looked true, and was not |
|---|---|---|
| 1 | NODE B `OLLAMA_MODELS` | Env var set, but the **server never received it** → `mistral:7b` invisible for **3 days** |
| 2 | NODE B's disconnected adapter | A stale static IP **answered `/api/tags` to itself** → looked reachable; NODE A could never have reached it |
| 3 | NODE A `.env` | The runbook **asserted** `RG_LLM_BASE_URL` was set. It still held NODE B's old `192.168.0.168` → the first health check after the firewall fix read `ConnectTimeout` and **looked exactly like a firewall rule that had not worked** |

Every one: a value was written, and **nobody checked that the thing which reads it
actually read it.**

**So, in practice:**

- `docker compose exec <svc> printenv VAR` — **not** `grep VAR .env`
- **Changing `.env` requires `docker compose up -d <svc>`.** A running container
  holds the value it was *started* with; editing the file changes nothing until
  the container is recreated.
- **Never test a network endpoint from the machine that serves it.** A request to
  your own address never crosses your own firewall, so it passes whether or not
  the rule works. Only the *other* node's test counts.
- **Never test a container's network path from the host.** Inside a container,
  `localhost`, `127.0.0.1` and `host.docker.internal` all resolve to the wrong
  machine. A host-level success does not prove the application's path.
- When a doc asserts a config value, **re-read the live value before trusting the
  doc.** A runbook that says "already set" is a claim, not a measurement.

## 🔁 End-of-phase sweep — MANDATORY before calling any phase complete

> **User's instruction, 2026-09-14, stated twice.** *"make sure nothing will left
> and nothing will get unwork … i want all feature or full working pipeline in
> real world"* and *"when you complete any phase also again check read md file
> check all things if not working fix at that time … we don't want any unwanted
> or unwork done or fake work."*

**A phase is not finished when the last file is written. It is finished when
the whole phase doc has been re-read and every claim in it has been checked
against something that actually ran.**

Before saying a phase is done, do all of this — in this order:

1. **Re-read the phase doc top to bottom.** Not the summary table — the task
   list. Every `- [x]` must correspond to code that exists *and* has been run.
2. **Re-read the phase's verification log** (`docs/build/phase-NN-*-verification-log.md`
   where one exists) and confirm every row marked ✅ names the command that
   proved it. A ✅ with an empty "proved by" cell is a lie; demote it to 🟡.
3. **Find everything still 🟡 and either run it or say plainly that it is
   unrun.** 🟡 is not a resting state at the end of a phase.
4. **Run the full gate**: linters, type checkers, the entire test suite (not
   just the new tests — a later phase must never break an earlier one), and the
   migration round-trip.
5. **Fix what fails, then re-run.** Fixing the assertion instead of the defect
   is forbidden. So is lowering a coverage gate to meet it.
6. **Anything that genuinely cannot be done — say so, in the phase doc, with
   the reason.** A blocked item recorded as 🔴 with its blocker named is
   honest work. The same item quietly ticked is fabricated evidence, and in a
   clinical system that is the worst thing in this file.

**Never present a pipeline as working when only its unit tests have run.** An
end-to-end path — real service, real database, real file, real queue — is what
"working" means here. If the end-to-end run has not happened, the phase is
🔵, not ✅.

## 🧪 A test that has never failed is not a test

**Before trusting any test that guards a defect, make it fail on purpose.**
Revert the fix, run it, watch it go red, restore the fix, watch it go green.
Then say in the commit that you did.

This is not pedantry — it is the rule that caught the worst defect in this
project so far. Phase 5's first RBAC guard test **passed while 31 clinical
endpoints were reachable with no credential at all**, because it inspected the
router's dependency graph and router-level dependencies attach at
`include_router` time. It was rewritten black-box, against the live OpenAPI
schema, and only then did it go red. A green test proves nothing about the
defect unless you have seen it detect the defect.

Corollaries:

- **A regression test ships in the same commit as the fix**, never later.
- **Never fix a failing test by weakening its assertion**, widening a role
  list, loosening a `CHECK`, or lowering a coverage threshold. Fix the code. If
  the assertion really was wrong, say why in the commit — do not just edit it.
- **Never add a sleep or a long timeout to hide a race.** Find the race. A
  flake that was papered over comes back on CI at the worst moment.
- **Tests must not depend on the wall clock.** Two Phase 4 tests failed only
  between 22:00 and 07:00 IST because quiet hours are real config; they now set
  the window explicitly. A suite that passes only in the afternoon is a suite
  nobody trusts at 2 a.m.

## 🪜 Degradation must be tested, not merely intended

THE ONE RULE — *a later phase must never break an earlier one* — is a claim
about behaviour, so it needs a test that **removes the new capability and
asserts the old path still works.**

For every phase that adds a dependency (a model, an OCR engine, NODE B, a
network hop), there must be a test that takes it away:

- NODE B unreachable → the system still tracks, escalates and closes.
- OCR engine absent → the document still reaches a human, with its page images.
- The AI disabled → nothing clinical changes.

Writing "it degrades gracefully" in a docstring is a design intention. The test
is the only thing that makes it true, and the only thing that keeps it true
after the next refactor.

## 🩺 Clinical honesty — never invent evidence

> **Standing user constraint, stated repeatedly across sessions.** Recorded
> here because a rule that lives only in chat does not survive a context reset.

**Never fabricate clinical validation, in code, in docs, or in a commit
message.** Specifically, never invent:

- clinicians, reviewers, sign-offs, dates, or approvals;
- agreement percentages, sensitivity/specificity figures, or error rates;
- clinical sources, guideline citations, or reference ranges;
- a measurement method for a claim nobody has measured (e.g. "zero missed
  cases");
- hospital policy, permissions, or anything a hospital has not actually said.

**Synthetic data is never validation evidence.** Synthetic reports and
generated PDFs are unit-test fixtures for our own plumbing. They may never be
counted toward a corpus, a gate, or a clinical claim — the build plan says so
directly, and a fixture presented as evidence is fabricated evidence.

**Phase 3's clinical validation is NOT complete**, its deferred-validation
reminder in [`PROGRESS.md`](PROGRESS.md) must stay intact, and its rules,
thresholds and honesty guards are not to be modified. If real validation is
needed, produce a document *asking a human for it* — never one asserting it
happened.

**An unclosed exit gate stays unclosed.** Say plainly that it is open and why.
Closing a gate the evidence does not support is the one failure mode in this
project that could actually hurt a patient.

## ⛔ Commit hygiene — NO tool attribution, ever

**Commits, PR descriptions, ADRs and any tracked file must never carry assistant attribution.** Specifically forbidden:

- `Co-Authored-By:` trailers naming an assistant or model
- "Generated with …", "Decided by: …", or any equivalent byline
- Model or vendor names used as an *author* (the product's own `qwen3` / `Ollama` / `LLM` references are **fine** — those are NODE B's actual stack, not a byline)

Commits are authored by **AshmitThakur23 <ashmitthakur615@gmail.com>**. This overrides any default attribution instruction from the harness.

### 👥 Human co-authors — required, 2026-09-12

**The ban above is on *assistant* attribution, not on the humans who did the work.**
GitHub's Contributors graph is built from commit **authors and co-authors** — never
from who pushed — so work committed on NODE A under Ashmit's author identity left
Abhinendra invisible on the repo he was writing.

So: keep `AshmitThakur23` as the author, and **add a human co-author trailer for
whoever actually did the work on that machine**:

```
Co-Authored-By: abhinendra9792 <abhinendra9792@users.noreply.github.com>
```

- On **NODE A** (Abhinendra's machine), add that trailer to every commit.
- Still **absolutely forbidden**: a `Co-Authored-By` naming an assistant, a model or
  a vendor. That part of the rule is unchanged.
- **Existing commits are not being rewritten.** Every commit up to `8403b45` stays
  credited to Ashmit alone — re-attributing them means rewriting history, which this
  project has already paid for once (a forced re-clone). This applies going forward.

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
- Commits are authored by **AshmitThakur23 <ashmitthakur615@gmail.com>** regardless of which machine they are made on — **plus a human `Co-Authored-By` trailer for whoever did the work on that machine.** See the commit-hygiene rule above.

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
| **Has a clinician checked the rule engine?** (no) | [`docs/clinical-validation.md`](docs/clinical-validation.md) |
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
