-- Result Guardian — NODE A extensions
-- Phase 0.2. Runs once, on first container start, against POSTGRES_DB.
--
-- One Postgres carries everything: data, vectors, queue, timers, sweeps,
-- fuzzy text and keyword search. See docs/adr/0001-single-postgres.md.

-- Vector store for the Phase 8 guideline knowledge base (bge-m3, 1024-dim).
CREATE EXTENSION IF NOT EXISTS vector;

-- Durable queue. Carries the wake-up for SLA timers; the sla_timers table
-- carries the truth. Phase 2.1.
CREATE EXTENSION IF NOT EXISTS pgmq CASCADE;

-- Scheduled sweeps. This is what makes "NODE A reboots -> nothing is lost"
-- true: every overdue timer is re-enqueued on restart. Phase 2.1.
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- Fuzzy patient-name matching (Phase 1.2 GIN index, Phase 7.4 synonym cascade).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Accent/diacritic folding for test-name normalisation. Phase 7.4.
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Digest functions; column-level encryption needs in Phase 10.1.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
