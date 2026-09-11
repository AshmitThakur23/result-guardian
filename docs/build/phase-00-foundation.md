# Phase 0 — Foundation

**Goal:** `git clone` + `docker compose up` on a blank Ubuntu box gives a running API, DB and worker on NODE A, and a reachable Ollama on NODE B.
**Duration:** 5–7 days · **Nodes:** A and B · **Depends on NODE B?** No

---

## 0.1 Repository

- [ ] Create repo, `main` protected, PRs required
- [ ] `.gitignore` — Python, Node, `.env`, `uploads/`, `*.pdf`
- [ ] `pyproject.toml` with ruff + black + mypy config
- [ ] pre-commit hooks: ruff, black, trailing whitespace, no-commit-to-main
- [ ] `README.md` with local setup in under 10 commands
- [ ] `docs/adr/0001-single-postgres.md` — why one DB
- [ ] `docs/adr/0002-two-node-split.md` — why the GPU is a separate node, why embeddings stay local

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

- [ ] Static IPs or DHCP reservations; **never rely on hostnames resolving**
- [ ] Fallback chain, in order: managed switch/router → dedicated router → phone hotspot → direct Ethernet with static `10.0.0.1` / `10.0.0.2` → cloud API (last resort, breaks the on-prem claim, must be approved in writing)
- [ ] Client isolation warning: campus, hotel and guest wifi block node-to-node traffic even when both machines show "connected". **Always test with `ping` before trusting a network.**
- [ ] The link does not need internet access. It only joins the two nodes.
- [ ] Docker gotcha: `localhost` and `host.docker.internal` inside the API container **do not reach NODE B**. Always use the literal LAN IP.

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

- [ ] All PKs = UUIDv7 (time-sortable). Add helper `uuid7()`
- [ ] All timestamps `TIMESTAMPTZ`, stored UTC, displayed IST
- [ ] Soft delete via `deleted_at` — never hard delete clinical rows
- [ ] Every table gets `created_at`, `updated_at`, `created_by`, `updated_by`
- [ ] Enum values stored as text with `CHECK` constraints, not PG enums (easier to alter)
- [ ] Money/values as `NUMERIC`, never float

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
