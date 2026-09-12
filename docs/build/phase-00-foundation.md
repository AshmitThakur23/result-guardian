# Phase 0 — Foundation

**Goal:** `git clone` + `docker compose up` on a blank Ubuntu box gives a running API, DB and worker on NODE A, and a reachable Ollama on NODE B.
**Duration:** 5–7 days · **Nodes:** A and B · **Depends on NODE B?** No

---

## 📍 STATUS SUMMARY — updated 2026-09-11

**Machines:** **NODE A = Abhinendra's laptop** — Docker present, stack never run. **NODE B = Ashmit's machine** (`LAPTOP-5JCGN9SJ`) — **not yet provisioned**. Its GPU is an **RTX 3050, 4 GB VRAM**, verified with `nvidia-smi`. See [`../adr/0006-node-roles-corrected.md`](../adr/0006-node-roles-corrected.md) — [ADR 0005](../adr/0005-node-roles-and-model.md) had these backwards and is superseded.

| § | Runs on | State | Note |
|---|---|---|---|
| 0.1 Repository | — | 🟡 **written, mostly done** | Repo live at `AshmitThakur23/result-guardian` (private). Branch protection not set. |
| 0.2 Containers — NODE A | A | 🟡 **written, never run** | **Docker is present on NODE A** — this is runnable there now. Defects D2/D3/D7 repaired 2026-09-11, still unrun |
| 0.3 NODE B provisioning | **B** | 🔴 **blocked** | Must run on **Ashmit's machine**, which is not in hand. Ollama v0.15.2 is already installed on Ashmit's machine, so provisioning only has to *run*, not install |
| 0.4 Network runbook | — | ✅ **done** | Real IPs still to be filled in |
| 0.5 App skeleton | A | 🟡 **partly verified** | **25 tests pass, mypy strict clean.** Never served a request against a real Postgres — that still needs Docker |
| 0.6 Base conventions | A | ✅ **done** | Encoded as mixins in `api/app/db/types.py`, not just prose |
| 0.7 Worker skeleton | A | 🟡 **partly verified** | Retry/backoff/DLQ and heartbeat now covered by tests (were **0%**). Handlers stay stubs until Phase 2. Never run against real pgmq |
| 0.8 CI | — | 🟡 **gates pass locally, workflow never run** | **All five gates verified on NODE A:** ruff ✅ black ✅ mypy ✅ pytest ✅ coverage 79% ✅. **Could not pass at all before D1.** The Actions workflow itself is still unrun |
| **Exit Gate 0** | A + B | 🔴 **OPEN** | Cannot close until NODE A exists |

**🟡 written, never run** means the code is committed and pushed but has not been executed even once. **Nothing below is ticked on the strength of having been typed.**

**Next step:** NODE A is in hand with Docker present, so 0.2/0.5/0.7 are runnable there now. **0.3 still needs `infra/nodeb/setup-windows.ps1` run on Ashmit's machine (NODE B).** Exit Gate 0's cross-node clause needs both on one LAN — open decision #5.

### 🐞 Defects found by inspection on 2026-09-11 — fixed, all still unrun

A full read of the scaffold before NODE A pulls it. All eight were in code that had never executed, which is why they survived.

**Three of the eight were verified on NODE A by actually running them.** The rest need Docker and stay 🟡. The ✅/🟡 in the last column is the honest split.

| # | Where | Defect | Fix | Verified? |
|---|---|---|---|---|
| D1 | `api/pyproject.toml` | No `[build-system]`; `api/` is a flat layout with 3 top-level packages → `pip install -e ".[dev]"` fails on discovery. **No CI job could go green.** | hatchling backend + explicit `packages = ["app", "worker"]` | ✅ **ran** — `pip install -e ".[dev]"` succeeds; `import app, worker` OK |
| D2 | `docker-compose.yml` | No `build.target`, and `dev` is the last stage in `api/Dockerfile` → a production build ships pytest/ruff/mypy to a hospital server | `target: runtime` on `api` and `worker`; CI asserts the runtime image is clean | 🟡 needs Docker |
| D3 | `infra/postgres/Dockerfile` | `cron.database_name` hardcoded while `POSTGRES_DB` is configurable → renaming the DB silently boots **without pg_cron**, removing Phase 2's reboot-recovery guarantee | Driven from `POSTGRES_DB` via compose `command:`; boot-time guard in `init/01-extensions.sql` raises on mismatch | 🟡 needs Docker |
| D4 | `api/app/routers/health.py` | `SELECT 1` and the `worker_health` lookup shared one `try` → a missing table reported **`db: error` and 503**, failing Exit Gate 0 and the RULE 2 assertion | Probes split; an unknown worker degrades to `worker`, never to `database`. Regression test added | ✅ **ran** at unit level — `test_missing_worker_health_does_not_report_the_database_as_down` passes. Still unproven against a real Postgres |
| D5 | `infra/postgres/init/02-queues.sql` | `worker_health` created by an init script, which only runs on an empty data dir → absent in CI, testcontainers, restored volumes | Moved into the Alembic baseline revision | 🟡 needs Docker — migration never applied |
| D6 | `api/Dockerfile` | Builder `pip wheel`s a hand-copied duplicate of the `pyproject.toml` dependency list → drifts silently | Installs from `pyproject.toml` | 🟡 needs Docker |
| D7 | `.github/workflows/ci.yml` | Test job used stock `pgvector/pgvector:pg16` — no pgmq, no pg_cron, no init scripts. Phase 1.8's integration tests break on contact | Builds and runs the real NODE A image, asserts all six extensions, applies migrations | 🟡 needs Actions |
| D8 | `api/pyproject.toml` + 7 files | `line-length = 100` but code written at 88 → `black --check` reformats on sight; `E501` at `app/db/session.py:7` | Set 88 (black's default, matching the code); 7 long lines wrapped | ✅ **ran** — ruff, black and mypy `--strict` all clean |

### 🐞 D9 — found by CI, fixed and verified on NODE A

| | |
|---|---|
| **Where** | `infra/postgres/Dockerfile` |
| **Symptom** | `CREATE EXTENSION pgmq` → *"extension pgmq has no installation script nor update path for version 1.4.4"*. Init aborted, container died, **no queues and no timers**. |
| **Root cause** | pgmq's tarball ships only *upgrade* scripts (`sql/pgmq--X--Y.sql`). The base install script `pgmq--1.4.4.sql` does not exist — upstream's Makefile **generates** it in the `all` target from `sql/pgmq.sql`. Two compounding faults: PGXS's `install` target does not depend on `all`, and `DATA = $(wildcard sql/*--*.sql)` is expanded when make *parses* the Makefile. So `make install` alone neither generates nor installs it. The control file lands, the install script does not, **and the image builds cleanly while being unusable**. |
| **Fix** | Generate the base script before `make install`; take the version from `pgmq.control` (`default_version`) rather than the git tag, since that is what Postgres actually looks for; then **assert both `pgmq.control` and `pgmq--<version>.sql` exist in `pg_config --sharedir`** — failing the *build* instead of the first boot if upstream's layout changes again. |
| **Verified** | ✅ **ran on NODE A** — see below. pgmq **1.4.4** preserved; no version change was needed. |

Verification on NODE A, real Docker, fresh container:

```
extname  | extversion          queues:  dlq, extract, ingest, notifications, sla_timers
---------+-----------          pgmq round-trip: send -> msg_id=1, read -> case_id=d9
 pg_cron  | 1.6                cron.database_name = result_guardian_test  (D3 guard OK)
 pg_trgm  | 1.6                container: ready in ~6s, stays Up after init
 pgcrypto | 1.3
 pgmq     | 1.4.4
 plpgsql  | 1.0
 unaccent | 1.1
 vector   | 0.8.6
```

> **Why CI caught this and nothing else did.** The old CI used stock `pgvector/pgvector:pg16` and died earlier at `pip install`, masking it completely. D7 — building and running the *real* image — is what exposed it. This is the strongest argument in the repo for testing against the actual artefact.

### Five more defects, found only by running the linters

Reading the code did not surface these; **running it did.** Recorded because it is the clearest evidence in this repo that *written ≠ done*:

| Where | Defect | Fix |
|---|---|---|
| `alembic/env.py` | `I001` unsorted imports — **pre-existing**, so `ruff check .` would have failed CI even after D8 | `known-third-party = ["alembic"]`: `api/alembic/` is a directory, not the library |
| `app/db/base.py` | `RUF100` unused `noqa` inside a comment | Reworded the example |
| `app/services/llm_probe.py` | `RUF100` — `# noqa: BLE001` for a rule not in `select` | Removed the pragma, kept the reasoning as prose |
| `worker/consumer.py`, `worker/main.py` | `SIM105` try/except/pass ×2 | `contextlib.suppress(TimeoutError)` |
| `worker/consumer.py:40` | mypy strict: `2 ** n` is typed `Any`, so `backoff_seconds` returned `Any` from an `int` function | Bit-shift, which is typed `int` |
| `app/db/types.py:20` | mypy strict: unused `type: ignore` | Removed |

### Test coverage: 51% → 79%

The `--fail-under=70` gate **did trip**, at 50.78%. Per the standing rule it was closed with tests, not by lowering the number. Added 19 tests across `tests/test_worker_consumer.py`, `tests/test_worker_heartbeat.py` and `tests/test_db_conventions.py`, covering the pgmq retry/DLQ branches (`worker/consumer.py` was at **0%** — the logic Phase 2's durability rests on), the heartbeat upsert and its failure path, and the Phase 0.6 conventions. **25 tests pass; coverage 79.17%.**

**Deviation logged:** `api` and `worker` still share the image tag `result-guardian/api:dev` while now building different targets in dev vs prod. Pre-existing; a prod build and a dev build overwrite each other under one tag. Not fixed here — it needs its own decision about tagging. **Do not treat `:dev` as proof of stage.**

### Deviations from the build plan, and why

1. **Custom Postgres image.** `pgvector/pgvector:pg16` ships *only* pgvector, but 0.2 also needs `pgmq` and `pg_cron`. `infra/postgres/Dockerfile` adds them. Without this the stack fails on first boot.
2. **NODE B is Windows, not Ubuntu.** Both `infra/nodeb/setup.sh` and `setup-windows.ps1` ship; env vars are identical.
3. **`qwen3:4b`, not `8b`.** 4 GB VRAM. `LLM_MODEL` is an env var.
4. **Worker lives at `api/worker/`,** not root `worker/`. The plan says "same image, different entrypoint", which requires it inside the API build context.
5. **Dev Postgres on host port 5433.** A native PostgreSQL install already holds 5432 on Ashmit's machine (NODE B); keep the mapping so either machine can run the dev stack.
6. **`cron.database_name` set from compose, not baked into the image** (defect D3). An exec-form `CMD` cannot expand a variable, so the image default and `POSTGRES_DB` could diverge silently. `docker-compose.yml` now passes it via `command:`, and `init/01-extensions.sql` refuses to boot on a mismatch.
7. **`worker_health` lives in the Alembic baseline, not in `init/02-queues.sql`** (defect D5). The plan puts queue creation in an init script; that is right for queues, wrong for an application table, because init scripts only run against an empty data directory. Extensions and pgmq queues stay in init — they must exist before Alembic connects.

---

## 0.1 Repository

- [x] Create repo — ✅ `AshmitThakur23/result-guardian`, private
- 🚫 ~~`main` protected, PRs required~~ — **out of scope for the hackathon** (user decision 2026-09-11). Work commits **straight to `main`**, no branches, no PRs. The `no-commit-to-branch` pre-commit hook was removed to match. Revisit if this becomes a multi-contributor product.
- [x] `.gitignore` — Python, Node, `.env`, `uploads/`, `*.pdf`
- [x] `pyproject.toml` with ruff + black + mypy config
- [x] pre-commit hooks: ruff, black, trailing whitespace, no-commit-to-main
- [x] `README.md` with local setup in under 10 commands
- [x] `docs/adr/0001-single-postgres.md` — why one DB
- [x] `docs/adr/0002-two-node-split.md` — why the GPU is a separate node, why embeddings stay local

## 0.2 Containers — NODE A

- [ ] `postgres` service: image `pgvector/pgvector:pg16`, named volume, healthcheck `pg_isready`
- [ ] `infra/postgres/init/01-extensions.sql`: `CREATE EXTENSION vector; pgmq; pg_cron; pg_trgm; unaccent;`
- [ ] `api` service: multi-stage Dockerfile (builder + slim runtime), non-root user, uvicorn
- [ ] `worker` service: same image, different entrypoint
- [ ] `caddy` service: reverse proxy, `:80` → api `/api/*`, static web build elsewhere
- [ ] `docker-compose.override.yml` for dev: bind mounts, `--reload`, exposed DB port
- [ ] Resource limits per service in compose (`deploy.resources`)

## 0.3 NODE B provisioning ★ new

- [ ] `infra/nodeb/setup.sh` — installs Ollama, pulls the model, writes the systemd override
- [ ] `OLLAMA_HOST=0.0.0.0:11434` — bind to LAN, not just localhost
- [ ] `OLLAMA_KEEP_ALIVE=-1` — pin model in VRAM; without this the first request after ~5 min idle stalls 20+ seconds while the model reloads
- [ ] `OLLAMA_NUM_PARALLEL=2`, `OLLAMA_MAX_LOADED_MODELS=1`
- [ ] Firewall: allow inbound TCP 11434 **from NODE A's IP only**, not `0.0.0.0/0`
- [ ] Model pulled and cached at provisioning time, never at first request
- [ ] No volumes mounted, no database, no logs containing prompt content
- [ ] Verify from NODE A: `curl http://192.168.1.50:11434/api/tags`

## 0.4 Network — NODE A ⇄ NODE B ★ new

Write `docs/network-runbook.md` covering:

- [x] Static IPs or DHCP reservations; **never rely on hostnames resolving**
- [x] Fallback chain, in order: managed switch/router → dedicated router → phone hotspot → direct Ethernet with static `10.0.0.1` / `10.0.0.2` → cloud API (last resort, breaks the on-prem claim, must be approved in writing)
- [x] Client isolation warning: campus, hotel and guest wifi block node-to-node traffic even when both machines show "connected". **Always test with `ping` before trusting a network.**
- [x] The link does not need internet access. It only joins the two nodes.
- [x] Docker gotcha: `localhost` and `host.docker.internal` inside the API container **do not reach NODE B**. Always use the literal LAN IP.

## 0.5 App skeleton

- [ ] `config.py` with pydantic-settings: `DATABASE_URL`, `JWT_SECRET`, `ENV`, `LOG_LEVEL`, `TZ=Asia/Kolkata`, `LLM_BASE_URL`, `LLM_TIMEOUT_S=30`, `LLM_ENABLED=true`, `LLM_MODEL`
- [ ] Async SQLAlchemy engine + session dependency
- [ ] Alembic configured for async, first empty revision
- [ ] `GET /api/health` → includes the NODE B block from day one
- [ ] The llm probe is **cached for 30s and must never block the response**
- [ ] `GET /api/version`
- [ ] structlog JSON logging, request-ID middleware (`X-Request-ID`, generate if absent)
- [ ] Global exception handler → RFC 7807 `problem+json`, never leak stack traces in prod
- [ ] CORS config, locked to known origins

```json
{
  "status": "ok",
  "db": "ok",
  "version": "0.1.0",
  "git_sha": "abc1234",
  "worker_heartbeat_age_s": 4,
  "llm": {
    "reachable": true,
    "host": "192.168.1.50:11434",
    "model": "qwen3:8b",
    "latency_ms": 780
  },
  "degraded_features": []
}
```

## 0.6 Base conventions (decide now, costly later)

- [x] All PKs = UUIDv7 (time-sortable). Add helper `uuid7()`
- [x] All timestamps `TIMESTAMPTZ`, stored UTC, displayed IST
- [x] Soft delete via `deleted_at` — never hard delete clinical rows
- [x] Every table gets `created_at`, `updated_at`, `created_by`, `updated_by`
- [x] Enum values stored as text with `CHECK` constraints, not PG enums (easier to alter)
- [x] Money/values as `NUMERIC`, never float

## 0.7 Worker skeleton

- [ ] pgmq queue creation script: `sla_timers`, `notifications`, `ingest`, `extract`, `dlq`
- [ ] Generic consumer loop: `pgmq.read` → handle → `pgmq.delete`, with `pgmq.archive` on permanent failure
- [ ] Retry with exponential backoff, max 5 attempts, then DLQ
- [ ] Graceful shutdown on SIGTERM (finish in-flight message)
- [ ] Heartbeat row in `worker_health` table, alert if stale > 2 min

## 0.8 CI

- [ ] GitHub Actions: ruff → `black --check` → mypy → pytest → docker build
- [ ] testcontainers spins real Postgres for integration tests
- [ ] **CI must pass with NODE B unreachable — no test may require the LLM**
- [ ] Coverage report, fail under 70%

---

## ✅ EXIT GATE 0

- [ ] Fresh machine → `docker compose up -d` → `/api/health` returns 200 with `db: ok` and an `llm` block
- [ ] Worker logs a heartbeat
- [ ] CI green on a dummy PR
- [ ] Power off NODE B and confirm `/api/health` still returns 200 with `llm.reachable: false` and **no error**

---

**Cross-ref:** [01-tech-stack-and-repo-layout.md](01-tech-stack-and-repo-layout.md) · [architecture/00-two-node-topology.md](../architecture/00-two-node-topology.md)
