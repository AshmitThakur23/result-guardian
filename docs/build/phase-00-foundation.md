# Phase 0 — Foundation

**Goal:** `git clone` + `docker compose up` on a blank Ubuntu box gives a running API, DB and worker on NODE A, and a reachable Ollama on NODE B.
**Duration:** 5–7 days · **Nodes:** A and B · **Depends on NODE B?** No

---

## 📍 STATUS SUMMARY — updated 2026-09-14

**Machines:** **NODE A = Abhinendra's laptop** (`LAPTOP-06ER0HBM`) — full stack running. **NODE B = Ashmit's machine** (`LAPTOP-5JCGN9SJ`), **172.25.54.48** — provisioned, RTX 3050 / 4 GB VRAM, Ollama `v0.15.2` serving `qwen3:4b` + `mistral:7b`. See [`../adr/0006-node-roles-corrected.md`](../adr/0006-node-roles-corrected.md); [ADR 0005](../adr/0005-node-roles-and-model.md) had these backwards and is superseded.

| § | Runs on | State | Note |
|---|---|---|---|
| 0.1 Repository | — | ✅ **done** | `AshmitThakur23/result-guardian` (private). Branch protection **🚫 out of scope** for the hackathon — recorded in CLAUDE.md |
| 0.2 Containers — NODE A | A | ✅ **done** | Full stack up: `postgres`, `api`, `worker`, `caddy`. All six extensions present; resource limits set per service |
| 0.3 NODE B provisioning | **B** | ✅ **done** | `172.25.54.48`, both models, keep-alive pinned, bound `0.0.0.0:11434`, firewall scoped to NODE A only. Reachability **verified from NODE A** and from inside the api container |
| 0.4 Network runbook | — | ✅ **done** | Both nodes on `172.25.48.0/20`. Decision #5 settled — shared Wi-Fi, no hotspot needed |
| 0.5 App skeleton | A | ✅ **done** | 67 routes live; `/api/health` and `/api/version` answer; probe cached and non-blocking; structlog, RFC 7807, CORS all in place |
| 0.6 Base conventions | A | ✅ **done** | Encoded as mixins in `api/app/db/types.py`, not just prose |
| 0.7 Worker skeleton | A | ✅ **done** | 6 pgmq queues drained live, 4 `pg_cron` jobs active, heartbeat under 10 s, `SIGTERM` handled, retry/backoff/DLQ proven in Phase 6 |
| 0.8 CI | — | ✅ **green** | All four jobs pass (`0ce8a10`): lint, test (**1052 passed, 1 xfailed**), web, build. ⚠️ **A timer/closure deadlock remains open** — it hit once in ~11 runs (≈9 %), so green is not evidence it is gone. [Register §1](../phase-6-outstanding.md) |
| **Exit Gate 0** | A + B | ✅ **PASSED 2026-09-14** | All four clauses verified. **RULE 2 closed by the deliberate test** — Ollama stopped on NODE B with both machines on one LAN: health stayed **200**, only `llm_generation` degraded, the whole suite stayed green, and SLA timers plus classification kept firing throughout. Recovery automatic in ~6 s |

**🟡 written, never run** means the code is committed and pushed but has not been executed even once. **Nothing below is ticked on the strength of having been typed.**

⚠️ **The gate is closed; CI is not.** Those are different claims and both are true. Exit Gate 0's four clauses were each measured. The red CI run is a **Phase 2 concurrency defect** surfaced by CI timing — real, open, and tracked — but it is not one of this gate's clauses.

### 🐞 Defects found by inspection on 2026-09-11 — all fixed, all now verified

A full read of the scaffold before NODE A pulls it. All eight were in code that had never executed, which is why they survived.

**All eight are now ✅ — verified by actually running them**, on NODE A and on GitHub Actions. D4 is verified at unit level only; everything else ran end to end.

| # | Where | Defect | Fix | Verified? |
|---|---|---|---|---|
| D1 | `api/pyproject.toml` | No `[build-system]`; `api/` is a flat layout with 3 top-level packages → `pip install -e ".[dev]"` fails on discovery. **No CI job could go green.** | hatchling backend + explicit `packages = ["app", "worker"]` | ✅ **ran** — `pip install -e ".[dev]"` succeeds; `import app, worker` OK |
| D2 | `docker-compose.yml` | No `build.target`, and `dev` is the last stage in `api/Dockerfile` → a production build ships pytest/ruff/mypy to a hospital server | `target: runtime` on `api` and `worker`; CI asserts the runtime image is clean | ✅ **ran in CI** — `build` job's "runtime image carries no dev tooling" check passes |
| D3 | `infra/postgres/Dockerfile` | `cron.database_name` hardcoded while `POSTGRES_DB` is configurable → renaming the DB silently boots **without pg_cron**, removing Phase 2's reboot-recovery guarantee | Driven from `POSTGRES_DB` via compose `command:`; boot-time guard in `init/01-extensions.sql` raises on mismatch | ✅ **ran** — pg_cron present both locally and in CI with `cron.database_name` driven from `POSTGRES_DB`; the guard did not fire |
| D4 | `api/app/routers/health.py` | `SELECT 1` and the `worker_health` lookup shared one `try` → a missing table reported **`db: error` and 503**, failing Exit Gate 0 and the RULE 2 assertion | Probes split; an unknown worker degrades to `worker`, never to `database`. Regression test added | ✅ **ran** at unit level — `test_missing_worker_health_does_not_report_the_database_as_down` passes. Still unproven against a real Postgres |
| D5 | `infra/postgres/init/02-queues.sql` | `worker_health` created by an init script, which only runs on an empty data dir → absent in CI, testcontainers, restored volumes | Moved into the Alembic baseline revision | ✅ **ran in CI** — "Migrations apply to an empty database" passes, creating `worker_health` |
| D6 | `api/Dockerfile` | Builder `pip wheel`s a hand-copied duplicate of the `pyproject.toml` dependency list → drifts silently | Installs from `pyproject.toml` | ✅ **ran in CI** — `build` job green |
| D7 | `.github/workflows/ci.yml` | Test job used stock `pgvector/pgvector:pg16` — no pgmq, no pg_cron, no init scripts. Phase 1.8's integration tests break on contact | Builds and runs the real NODE A image, asserts all six extensions, applies migrations | ✅ **ran in CI** — real NODE A image built and started, all six extensions asserted |
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

> ### 🔍 Bulk verification, 2026-09-14 — why these are ticked now
>
> Phase 0's task boxes had sat unticked while the stack itself had been running
> for weeks: **26 items of tracking debt, not 26 items of work.** CLAUDE.md's
> rule is that unlogged work counts as not done, so each was checked against
> something that actually runs before being ticked, in one pass:
>
> * **services, extensions, volumes** — `docker compose ps` (4 up) and `\dx`
>   (`vector 0.8.6`, `pgmq 1.4.4`, `pg_cron 1.6`, `pg_trgm`, `unaccent`, `pgcrypto`)
> * **API surface** — the live OpenAPI schema: **67 routes**, `/api/health` and
>   `/api/version` among them; 23 of 24 GET endpoints answer 200 (the 24th is
>   `/api/patients`, which correctly **422s** without a query)
> * **queues and worker** — 6 pgmq queues, 4 active `pg_cron` jobs, heartbeat
>   under 10 s, `SIGTERM` handled in `worker/main.py` and `worker/consumer.py`
> * **CI** — `ruff`, `black`, `mypy`, `pytest`, `coverage --fail-under=70` and
>   the image build all exist as jobs, and **RULE 2 was proven by stopping
>   Ollama** rather than by assertion
> * **limits** — `deploy.resources` set per service in `docker-compose.yml`
>
> One item is **🚫 superseded** rather than done, and is marked as such below.

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

- [x] `postgres` service: image `pgvector/pgvector:pg16`, named volume, healthcheck `pg_isready`
- [x] `infra/postgres/init/01-extensions.sql`: `CREATE EXTENSION vector; pgmq; pg_cron; pg_trgm; unaccent;`
- [x] `api` service: multi-stage Dockerfile (builder + slim runtime), non-root user, uvicorn
- [x] `worker` service: same image, different entrypoint
- [x] `caddy` service: reverse proxy, `:80` → api `/api/*`, static web build elsewhere
- [x] `docker-compose.override.yml` for dev: bind mounts, `--reload`, exposed DB port
- [x] Resource limits per service in compose (`deploy.resources`)

## 0.3 NODE B provisioning ★ new

> **Ran on NODE B (Ashmit's, `LAPTOP-5JCGN9SJ`) 2026-09-14.** LAN IP **192.168.0.168**.
> Ollama `v0.15.2` was already installed — configured, **not** reinstalled.
> Command: `.\infra\nodeb\setup-windows.ps1 -ModelsPath "D:\Nexus AI\.ollama\models" -SkipFirewall`

- [x] `infra/nodeb/setup-windows.ps1` — configures Ollama and pulls the model (the Windows counterpart of `setup.sh`; NODE B is Windows, not Ubuntu)
- [x] `OLLAMA_HOST=0.0.0.0:11434` — verified: `http://192.168.0.168:11434/api/tags` → HTTP 200
- [x] `OLLAMA_KEEP_ALIVE=-1` — verified by `ollama ps` reporting **`UNTIL: Forever`**
- [x] `OLLAMA_NUM_PARALLEL=2`, `OLLAMA_MAX_LOADED_MODELS=1`
- [x] ✅ **Firewall: TCP 11434 from NODE A's IP only** — applied on NODE B 2026-09-14 18:33. Two auto-created `ollama.exe` **Block** rules removed first (Block beats Allow in Windows Firewall), then `Result Guardian NODE B` created: Enabled, Inbound, Allow, TCP 11434, **RemoteAddress `172.25.52.148` only**
- [x] Model pulled and cached at provisioning time, never at first request — `qwen3:4b` (2.33 GB) cached to `D:`
- [x] No volumes mounted, no database, no logs containing prompt content — Ollama only
- [x] ✅ **Verified from NODE A, 2026-09-14** — `curl http://172.25.54.48:11434/api/tags` → 200, `qwen3:4b` + `mistral:7b`. Also proven **from inside the api container**, which is the only path that counts. (The address here was NODE B's old home-network one.)

**A pre-existing break was found and repaired.** `OLLAMA_MODELS` was already set to `D:\Nexus AI\.ollama\models` at User *and* Machine scope, but the server never received it — it ran on the `C:` default with `total blobs: 0`, so **`mistral:7b` (used by the separate `D:\Nexus AI` project) had been invisible since at least 2026-09-11.** Cause: `ollama app.exe --hide --fast-startup` hands its child a sanitised environment. `setup-windows.ps1` starts `ollama serve` directly from a shell carrying the vars, which fixed it. `ollama list` now shows **both** models. Proof by construction: `C:` blobs = **0**, `D:` blobs = **10**.

⚠️ **Durability untested.** The fix depends on how `ollama serve` is launched. If Windows relaunches it via the tray app at next login it may revert to the `C:` default and hide `mistral:7b` again. **Re-check `ollama list` after a reboot.**

⚠️ **For Phase 8.4.** `qwen3:4b` is a *reasoning* model: by default `response` is empty while it emits `thinking` (854 chars in testing, hitting the token cap mid-thought). It loads at **29% CPU / 71% GPU** — a 4.2 GB footprint against 4 GB VRAM, so not fully resident. The strict-JSON contract will need `think=false` + `format: json` and a token budget covering the preamble. Generation answered in 1.7–7 s. Re-benchmark before relying on it.

## 0.4 Network — NODE A ⇄ NODE B ★ new

Write `docs/network-runbook.md` covering:

- [x] Static IPs or DHCP reservations; **never rely on hostnames resolving**
- [x] Fallback chain, in order: managed switch/router → dedicated router → phone hotspot → direct Ethernet with static `10.0.0.1` / `10.0.0.2` → cloud API (last resort, breaks the on-prem claim, must be approved in writing)
- [x] Client isolation warning: campus, hotel and guest wifi block node-to-node traffic even when both machines show "connected". **Always test with `ping` before trusting a network.**
- [x] The link does not need internet access. It only joins the two nodes.
- [x] Docker gotcha: `localhost` and `host.docker.internal` inside the API container **do not reach NODE B**. Always use the literal LAN IP.

## 0.5 App skeleton

- [x] `config.py` with pydantic-settings: `DATABASE_URL`, `JWT_SECRET`, `ENV`, `LOG_LEVEL`, `TZ=Asia/Kolkata`, `LLM_BASE_URL`, `LLM_TIMEOUT_S=30`, `LLM_ENABLED=true`, `LLM_MODEL`
- [x] Async SQLAlchemy engine + session dependency
- [x] Alembic configured for async, first empty revision
- [x] `GET /api/health` → includes the NODE B block from day one
- [x] The llm probe is **cached for 30s and must never block the response**
- [x] `GET /api/version`
- [x] structlog JSON logging, request-ID middleware (`X-Request-ID`, generate if absent)
- [x] Global exception handler → RFC 7807 `problem+json`, never leak stack traces in prod
- [x] CORS config, locked to known origins

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

- [x] pgmq queue creation script: `sla_timers`, `notifications`, `ingest`, `extract`, `dlq`
- [x] Generic consumer loop: `pgmq.read` → handle → `pgmq.delete`, with `pgmq.archive` on permanent failure
- [x] Retry with exponential backoff, max 5 attempts, then DLQ
- [x] Graceful shutdown on SIGTERM (finish in-flight message)
- [x] Heartbeat row in `worker_health` table, alert if stale > 2 min

## 0.8 CI

- [x] GitHub Actions: ruff → `black --check` → mypy → pytest → docker build
- [x] 🚫 ~~testcontainers spins real Postgres for integration tests~~ — **superseded.** CI builds and runs the **real** `infra/postgres` image with the real init scripts, which tests the image actually shipped rather than a stock one. `testcontainers` stays declared in `pyproject.toml` and is unused.
- [x] **CI must pass with NODE B unreachable — no test may require the LLM**
- [x] Coverage report, fail under 70%

---

## ✅ EXIT GATE 0

- [x] ✅ Fresh machine → `docker compose up -d` → `/api/health` returns 200 with `db: ok` and an `llm` block — **verified on NODE A, 2026-09-14**
- [x] ✅ Worker logs a heartbeat — `worker_heartbeat_age_s: 1`
- [x] ✅ CI green on a dummy PR — run `34670777455`, 2026-09-12, all three jobs
- [x] ✅ NODE B unreachable → `/api/health` still 200, `llm.reachable: false`, only `llm_generation` degraded — **CLOSED PROPERLY 2026-09-14 18:44, by the literal scripted step**

**The measured response, 2026-09-14, `curl localhost/api/health` through Caddy:**

```json
{"status":"ok","db":"ok","version":"0.1.0","git_sha":"unknown",
 "worker_heartbeat_age_s":1,
 "llm":{"reachable":false,"host":"192.168.0.168:11434","model":"qwen3:4b",
        "error":"ConnectTimeout"},
 "degraded_features":["llm_generation"]}
```

⚠️ **Two honest qualifications on the last clause.**

**1 · "and no error" means the endpoint does not error — not that the field is
absent.** `llm.error` carries `ConnectTimeout`, and that is deliberate:
`tests/test_health.py` asserts the reason is populated, commented *"the reason
is reported, not swallowed"*. A health endpoint that hides why NODE B is
missing is worse than one that says. What the clause forbids is a 503, and the
response is 200 with `status: "ok"`.

**2 · NODE B was unreachable because the two machines were on different
networks, not because Ollama was powered off.** At the HTTP boundary these are
indistinguishable — `ConnectTimeout` either way — and losing the whole network
path is the stronger test, since RULE 2 is precisely about *the network
between the nodes*. But it is not the literal scripted step. **Re-run the
deliberate version — stop Ollama on NODE B with both machines on one LAN —
before treating this clause as closed by intent as well as by outcome.**

### 🟢 The two nodes are CONNECTED — measured on NODE A, 2026-09-14

The blocker recorded here previously (different networks) is resolved, and so is
the one that replaced it (NODE B's `ollama.exe` inbound Block rules, cleared in
`e523ee8`). **The full path now works, proven at all three layers:**

```
Test-NetConnection 172.25.54.48 -Port 11434        <- NODE A host
  PingSucceeded    : False    (expected - Windows blocks inbound ICMP)
  TcpTestSucceeded : True
  SourceAddress    : 172.25.52.148

docker compose exec api python -c "httpx.get(...)"  <- INSIDE the container,
  HTTP 200  ->  qwen3:4b 2.5 GB, mistral:7b 4.37 GB     the only path that counts

curl localhost/api/health                           <- the application's own view
  {"status":"ok","db":"ok","worker_heartbeat_age_s":0,
   "llm":{"reachable":true,"host":"172.25.54.48:11434",
          "model":"qwen3:4b","latency_ms":36},
   "degraded_features":[]}
```

**36 ms across the two nodes, and `degraded_features` empty for the first time.**

⚠️ **A stale-config defect was found and fixed in the process.** `.env` still
carried `RG_LLM_BASE_URL=http://192.168.0.168:11434` — NODE B's old home-network
address — so the first health check after the firewall fix still reported
`reachable: false` with a `ConnectTimeout`, and looked exactly like a firewall
that had not worked. The runbook asserted `.env` was "already set"; it was not.
**A config file is not verified until something has read it.**

### ✅ RULE 2 PROVEN BY THE DELIBERATE TEST — 2026-09-14 18:44

**Clause 4 is now closed by intent as well as by outcome.** Ollama was deliberately
stopped on NODE B **with both machines on one LAN and a path proven working seconds
earlier** — so this is the scripted step, not a network outage standing in for it.

NODE B killed **three** processes, tray app first: `ollama app.exe` (20244), then
servers 2064 and 25640. **Killing the tray first matters** — otherwise it relaunches
the server within seconds and you are testing a live NODE B while believing it is
dead. Zero processes remained; port 11434 stopped listening.

**Measured on NODE A with NODE B confirmed dead:**

| Check | Result |
|---|---|
| `/api/health` HTTP status | **200** — not 503 |
| `status` | **`"ok"`** — a missing NODE B is not an outage |
| `llm.reachable` | `false`, with `error: "ConnectTimeout"` — **the reason is reported, not swallowed** |
| `degraded_features` | **`["llm_generation"]` and nothing else** |
| `worker_heartbeat_age_s` | **3–5 s** — still ticking |
| **Full backend test suite** | **exit code 0 — entirely green with NODE B dead** |

**The strongest evidence is in the worker log during the outage:**

```
{"queue": "sla_timers", "msg_id": 32220, "event": "message_handled"}
{"queue": "classify",   "msg_id": 15,    "event": "message_handled"}
```

**Phase 2's SLA timers fired and Phase 3's classification ran while NODE B was
dead.** That is RULE 2 stated as a measurement rather than an intention: the
safety property does not depend on the network between the nodes, and it does not
depend on AI.

⚠️ **One observation worth keeping:** the error is `ConnectTimeout`, not
`ConnectionRefused` — with no listener behind an allow rule, Windows drops rather
than resetting. Either way the condition is detected and reported; but it means
**"NODE B is off" and "NODE B is unreachable" look identical at this boundary**,
which is the same ambiguity noted when the two machines were on different networks.

### ✅ And it recovered on its own — the gate closes on both edges

NODE B restarted Ollama; **nothing was done on NODE A.** Within ~6 s:

```json
{"status":"ok","db":"ok","worker_heartbeat_age_s":6,
 "llm":{"reachable":true,"host":"172.25.54.48:11434",
        "model":"qwen3:4b","latency_ms":32},
 "degraded_features":[]}
```

`qwen3:4b` and `mistral:7b` both served again, confirmed **from inside the API
container**. **Degradation and recovery are both automatic** — no restart, no
config change, no manual re-enable. A transient NODE B outage costs prose
generation for its duration and nothing else, which is precisely what
[the degradation ladder](99-gaps-timeline-degradation.md) promises.

---

**Cross-ref:** [01-tech-stack-and-repo-layout.md](01-tech-stack-and-repo-layout.md) · [architecture/00-two-node-topology.md](../architecture/00-two-node-topology.md)
