# Result Guardian

**No post-discharge investigation result is ever lost.**

A doctor physically cannot complete a discharge while a test has no responsible owner and no expected-by date. Pending results are tracked by database timers, classified by a deterministic Python rule engine, and escalated through a five-rung ladder that ends at the patient if nobody acts.

Runs entirely on-premise, on two machines, on a private LAN, with **no internet required**.

---

## The two machines

| | Machine | Runs | Holds patient data |
|---|---|---|---|
| **NODE A** — core | Any office PC, CPU only | Postgres, FastAPI, worker, Caddy, React dashboard | **Yes — all of it** |
| **NODE B** — inference | One shared GPU box | Ollama + Qwen3 | **Never** |

> **Turn NODE B off and the patient is still tracked, still flagged, still escalated.** That is the design, not a fallback. Only the "Explain" button degrades — and it degrades to showing retrieved guideline text, not to silence.

**Current deployment:** the development laptop is **NODE A**. **NODE B** is the repo owner's machine and is not yet provisioned. See [`docs/adr/0006-node-roles-corrected.md`](docs/adr/0006-node-roles-corrected.md) — an earlier ADR had these backwards.

---

## Setup — NODE A

Needs Docker and Docker Compose v2. Nothing else.

```bash
git clone https://github.com/AshmitThakur23/result-guardian.git
cd result-guardian
cp .env.example .env
#  edit .env: set POSTGRES_PASSWORD, RG_JWT_SECRET, and RG_LLM_BASE_URL
docker compose up -d --build
docker compose exec api alembic upgrade head
curl http://localhost/api/health
```

That is the whole setup. The migrate step is not optional — the schema,
including the worker's heartbeat table, is owned by Alembic. Between `up` and
`upgrade head` the API is already serving and returns 200 with
`degraded_features: ["worker"]`, which is correct rather than broken.

Expected response:

```json
{
  "status": "ok",
  "db": "ok",
  "worker_heartbeat_age_s": 4,
  "llm": { "reachable": false, "host": "192.168.1.50:11434", "model": "qwen3:4b" },
  "degraded_features": ["llm_generation"]
}
```

**`"status": "ok"` with `"reachable": false` is correct, not a failure.** NODE B is optional. If that ever returns a non-200 because NODE B is down, something has broken the core guarantee — see [RULE 2](docs/build/00-governing-rules-and-topology.md).

Useful commands:

```bash
docker compose logs -f api worker
docker compose exec postgres psql -U rg_app -d result_guardian -c '\dx'
docker compose down            # keeps data;  add -v to wipe it
```

> ⚠️ If you change `POSTGRES_DB`, the database must be recreated from an empty
> volume — `pg_cron` can only be created in the one database it is pinned to,
> and it is pinned from `POSTGRES_DB`. A mismatch fails the boot loudly on
> purpose: without `pg_cron` there is no sweep to re-fire overdue timers after
> a restart, which is the guarantee Phase 2 rests on.

`make` is not installed on the Windows dev box, so `tasks.ps1` mirrors the `Makefile`: `./tasks.ps1 up`, `./tasks.ps1 test`, `./tasks.ps1 lint`.

## Setup — NODE B

Ollama only. No database, no volumes, no patient data.

```powershell
# Windows
./infra/nodeb/setup-windows.ps1 -NodeAIp 192.168.1.10 -Model qwen3:4b
```

```bash
# Ubuntu
sudo ./infra/nodeb/setup.sh --node-a-ip 192.168.1.10 --model qwen3:4b
```

Then, **from NODE A**, verify before trusting anything:

```bash
curl http://<NODE_B_IP>:11434/api/tags
```

⚠️ Inside NODE A's API container, `localhost` and `host.docker.internal` do **not** reach NODE B. Always use the literal LAN IP. See [`docs/network-runbook.md`](docs/network-runbook.md).

---

## Where things are

| | |
|---|---|
| **Read this first** | [`CLAUDE.md`](CLAUDE.md) — rules, machine roles, working protocol |
| **What's done / next** | [`PROGRESS.md`](PROGRESS.md) |
| **Full doc index** | [`docs/README.md`](docs/README.md) |
| **Architecture (runtime)** | [`docs/architecture/`](docs/architecture/) — Steps 0–11 |
| **Build plan (phases)** | [`docs/build/`](docs/build/) — Phases 0–10, 425 tickable tasks |
| **Decisions** | [`docs/adr/`](docs/adr/) |

```
api/          FastAPI app + worker (same image, different entrypoint)
  app/        config, db, routers, services  (+ rules/ rag/ extraction/ for later phases)
  worker/     pgmq consumer loop + heartbeat
  alembic/    migrations
infra/
  postgres/   custom image: pgvector + pg_cron + pgmq
  caddy/      reverse proxy
  nodeb/      NODE B provisioning (Windows + Ubuntu)
web/          React dashboard (Phase 1)
docs/         the source of truth
```

---

## The two rules everything obeys

**RULE 1 — The safety property must never depend on AI.** Tracking and escalation are database constraints and a state machine. Phases 0–5 are a complete working product with zero AI.

**RULE 2 — The safety property must never depend on the network between nodes.** Everything that keeps a patient safe runs on NODE A.

**And the one that governs the build order:** a later phase must never be able to break an earlier one. Each phase degrades to the previous phase, never to silence.

---

## Status

**Phase 0 — Foundation, in progress.** See [`PROGRESS.md`](PROGRESS.md) for the live state and [`docs/build/phase-00-foundation.md`](docs/build/phase-00-foundation.md) for the task-level checklist.

MVP (Phases 0–5) is a fully functional, pilot-ready product with **zero AI and a single node**. Everything after that is acceleration.

## Licence

Private. Not for distribution.
