# Phase 0 — Foundation

**Goal:** `git clone` + `docker compose up` on a blank Ubuntu box gives a running API, DB and worker on NODE A, and a reachable Ollama on NODE B.
**Duration:** 5–7 days · **Nodes:** A and B · **Depends on NODE B?** No

---

## 📍 STATUS SUMMARY — updated 2026-09-11

**Machines:** NODE B = the RTX 3050 laptop (this one). NODE A = the other machine, **not yet provisioned**. See [`../adr/0005-node-roles-and-model.md`](../adr/0005-node-roles-and-model.md).

| § | Runs on | State | Note |
|---|---|---|---|
| 0.1 Repository | — | 🟡 **written, mostly done** | Repo live at `AshmitThakur23/result-guardian` (private). Branch protection not set. |
| 0.2 Containers — NODE A | A | 🟡 **written, never run** | Needs NODE A + Docker daemon |
| 0.3 NODE B provisioning | **B** | 🟡 **script written, not yet run** | Can be done on this laptop any time |
| 0.4 Network runbook | — | ✅ **done** | Real IPs still to be filled in |
| 0.5 App skeleton | A | 🟡 **written, never run** | |
| 0.6 Base conventions | A | ✅ **done** | Encoded as mixins in `api/app/db/types.py`, not just prose |
| 0.7 Worker skeleton | A | 🟡 **written, never run** | Handlers are stubs until Phase 2 |
| 0.8 CI | — | 🟡 **written, never run** | Runs on first PR |
| **Exit Gate 0** | A + B | 🔴 **OPEN** | Cannot close until NODE A exists |

**🟡 written, never run** means the code is committed and pushed but has not been executed even once. **Nothing below is ticked on the strength of having been typed.**

**Next step (agreed with the user):** set up **NODE A on the other machine first**, then verify across both. No further Phase 0 execution until then.

### Deviations from the build plan, and why

1. **Custom Postgres image.** `pgvector/pgvector:pg16` ships *only* pgvector, but 0.2 also needs `pgmq` and `pg_cron`. `infra/postgres/Dockerfile` adds them. Without this the stack fails on first boot.
2. **NODE B is Windows, not Ubuntu.** Both `infra/nodeb/setup.sh` and `setup-windows.ps1` ship; env vars are identical.
3. **`qwen3:4b`, not `8b`.** 4 GB VRAM. `LLM_MODEL` is an env var.
4. **Worker lives at `api/worker/`,** not root `worker/`. The plan says "same image, different entrypoint", which requires it inside the API build context.
5. **Dev Postgres on host port 5433.** A native PostgreSQL install already holds 5432 on this machine.

---

## 0.1 Repository

- [ ] Create repo, `main` protected, PRs required — repo ✅ created (`AshmitThakur23/result-guardian`, private); **branch protection + required PRs still to do**
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
