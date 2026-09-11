# ADR 0001 — One Postgres instance carries everything

**Status:** Accepted · **Date:** 2026-09-11 · **Phase:** 0.1

## Context

Result Guardian needs relational data, a vector store, a durable job queue, SLA timers, scheduled sweeps, fuzzy text matching and keyword search. The conventional answer is a service per concern: Postgres + Redis + RabbitMQ/Celery + Elasticsearch.

This system is deployed on-premise, inside a hospital, on "any office PC" (NODE A), maintained by hospital IT staff who did not write it and will not read its source.

## Decision

**One PostgreSQL 16 instance provides all of it.**

| Concern | Mechanism |
|---|---|
| Relational data | Postgres |
| Vector store | `pgvector` 0.7+ (HNSW) |
| Queue | `pgmq` 1.4+ |
| SLA timers | `sla_timers` table + pgmq visibility timeout |
| Scheduled sweeps | `pg_cron` |
| Keyword search | native `tsvector` + `ts_rank_cd` |
| Fuzzy text | `pg_trgm`, `unaccent` |

No Redis. No RabbitMQ. No Elasticsearch.

## Consequences

**Good**

- One service to install, monitor, back up and restore. An untested backup is not a backup, and a five-service backup will not be tested.
- **Timers become transactional with the data they guard.** Creating a pending case and enqueueing its SLA timer happen in one transaction — there is no window where the case exists and the timer does not. With an external queue that window exists, and a patient falls into it.
- `pg_cron` sweeps let timer state be *derived from the table*, not held only in the queue. NODE A can reboot and every overdue timer re-fires. This is what makes RULE 1 enforceable — see [0002](0002-two-node-split.md).
- Fewer moving parts to explain to hospital IT at handover.

**Bad, accepted**

- Postgres is not a purpose-built queue. At hospital volume (~500 discharges/day, ~2000 results/day, load-tested at 3×) this is far from any limit.
- pgvector HNSW is slower at very large scale than a dedicated vector DB. The knowledge base is guidelines and hospital SOPs — thousands of chunks, not millions.
- The base `pgvector/pgvector:pg16` image ships **only** pgvector. `pg_cron` and `pgmq` must be added, so we build `infra/postgres/Dockerfile` on top of it. This is a real cost of the decision and is paid once, in Phase 0.

## Alternatives rejected

- **Postgres + Redis + Celery** — the standard stack. Rejected because it breaks timer/data transactionality and triples the operational surface for a system whose entire value is that it does not silently forget things.
- **Managed cloud services** — rejected outright; violates the on-prem guarantee. See [0002](0002-two-node-split.md).
