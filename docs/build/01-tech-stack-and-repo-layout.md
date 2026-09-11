# Build 01 — LOCKED Tech Stack & Repo Layout

> Source: build plan PDF, pages 4–10.
> **Decide once here. Do not change mid-build.** Re-litigating any row below is out of scope for any session.

## Backend — NODE A

| Concern | Choice | Version |
|---|---|---|
| Language | Python | 3.11 |
| Web framework | FastAPI | 0.115+ |
| Validation / schemas | Pydantic | v2 |
| ORM | SQLAlchemy (async) | 2.0 |
| DB driver | asyncpg | latest |
| Migrations | Alembic | latest |
| Background worker | Custom Python consumer on pgmq | — |
| Task scheduling | pgmq visibility timeout + pg_cron sweeps | — |
| Auth | JWT (PyJWT) + Argon2 (`passlib[argon2]`) | — |
| HTTP client | httpx | — |
| Config | pydantic-settings + `.env` | — |
| Logging | structlog (JSON output) | — |

## Database — NODE A

| Concern | Choice |
|---|---|
| Primary DB | PostgreSQL 16 |
| Vector store | pgvector 0.7+ (HNSW index) |
| Queue / timers | pgmq 1.4+ |
| Keyword search | Postgres native `tsvector` + `ts_rank_cd` (BM25-style) |
| Fuzzy text | `pg_trgm`, `unaccent` |
| Scheduled sweeps | `pg_cron` |

> Everything lives in **one Postgres instance**. No Redis, no RabbitMQ, no Elasticsearch. Deliberate — on-prem hospital servers should run one database, not five services.

## Frontend — served from NODE A

| Concern | Choice |
|---|---|
| Framework | React 18 + TypeScript |
| Build | Vite |
| Styling | TailwindCSS |
| Components | shadcn/ui (Radix primitives) |
| Server state | TanStack Query v5 |
| Routing | React Router v6 |
| Forms | react-hook-form + zod |
| Tables | TanStack Table |
| Dates | date-fns |
| Charts (Phase 5) | Recharts |

## Document / AI (Phase 6+)

| Concern | Choice | Runs on |
|---|---|---|
| Native PDF text | PyMuPDF (fitz) | NODE A |
| Complex layout / tables | Docling | NODE A |
| OCR | PaddleOCR (English + Hindi models) | NODE A |
| Image preprocessing | OpenCV, Pillow | NODE A |
| Embeddings | bge-m3 (1024-dim) | NODE A, CPU |
| Reranker | bge-reranker-v2-m3 | NODE A, CPU |
| Embedding/reranker serving | TEI or sentence-transformers in worker | NODE A |
| LLM runtime | Ollama | NODE B |
| LLM | Qwen3-Instruct (start 8B q4, benchmark 14B/32B) | NODE B |
| Fuzzy matching | RapidFuzz | NODE A |
| Terminology | LOINC release file + local synonym table | NODE A |

## Integration (Phase 9) — NODE A

| Concern | Choice |
|---|---|
| HL7 v2 parsing | hl7apy |
| MLLP listener | custom asyncio TCP server |
| FHIR models | fhir.resources (R4) |
| FHIR server (test) | HAPI FHIR in Docker |

## Ops / Testing

| Concern | Choice |
|---|---|
| Containers | Docker + docker compose v2 |
| Reverse proxy / TLS | Caddy |
| Unit / integration tests | pytest, pytest-asyncio, httpx.AsyncClient |
| Ephemeral DB in tests | testcontainers-python |
| E2E tests | Playwright |
| Load test | Locust |
| Lint / format | ruff, black, mypy |
| Frontend lint | ESLint + Prettier |
| CI | GitHub Actions (self-hosted runner if hospital network) |
| Metrics | prometheus-fastapi-instrumentator → Prometheus → Grafana |
| Logs | Loki + Promtail (or plain rotated JSON files) |
| Backups | pgBackRest or `pg_dump` + cron + offsite copy |
| Secrets | Docker secrets / `.env` with 600 perms (no Vault needed on-prem) |

---

## Repo layout

```
result-guardian/
├── docker-compose.yml              # NODE A stack
├── docker-compose.override.yml     # dev only
├── docker-compose.nodeb.yml        # NODE B — ollama only
├── .env.example
├── Makefile                        # demo-reset, eval, lint, test
├── api/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db/                     # session, base, models/
│   │   ├── schemas/                # pydantic
│   │   ├── routers/
│   │   ├── services/               # business logic, no HTTP
│   │   ├── rules/                  # Phase 3 rule engine
│   │   ├── extraction/             # Phase 6-7
│   │   ├── rag/                    # Phase 8
│   │   ├── integration/            # Phase 9 hl7/fhir
│   │   └── audit/
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml
├── worker/
│   └── app/consumers/              # sla_timers, notifications, ingest, extract
├── web/                            # React app
├── infra/
│   ├── caddy/Caddyfile
│   ├── postgres/init/              # extensions, pgmq queues
│   ├── nodeb/                      # NODE B provisioning script + systemd unit
│   └── grafana/
└── docs/
    ├── adr/                        # architecture decision records
    ├── runbook.md
    ├── network-runbook.md          # NODE A ⇄ NODE B
    ├── clinical-validation.md
    ├── extraction-accuracy.md
    ├── ai-evaluation.md
    ├── integration-spec.md
    └── nabh-mapping.md
```

---

## Base conventions (Phase 0.6 — decide now, costly later)

- All PKs = **UUIDv7** (time-sortable). Add helper `uuid7()`
- All timestamps **`TIMESTAMPTZ`**, stored UTC, displayed IST
- Soft delete via `deleted_at` — **never hard delete clinical rows**
- Every table gets `created_at`, `updated_at`, `created_by`, `updated_by`
- Enum values stored as **text with `CHECK` constraints**, not PG enums (easier to alter)
- Money/values as **`NUMERIC`**, never float
