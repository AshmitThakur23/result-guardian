-- Result Guardian — NODE A extensions
-- Phase 0.2. Runs once, on first container start, against POSTGRES_DB.
--
-- One Postgres carries everything: data, vectors, queue, timers, sweeps,
-- fuzzy text and keyword search. See docs/adr/0001-single-postgres.md.

-- ── Guard: pg_cron must be pinned to THIS database ────────────────────
-- pg_cron can only be created in the single database named by
-- cron.database_name, which is fixed at server start. If POSTGRES_DB and
-- cron.database_name disagree, CREATE EXTENSION pg_cron below fails and the
-- sweep that re-fires overdue timers after a reboot is simply absent --
-- taking Phase 2's core durability guarantee with it, silently.
--
-- Fail the boot loudly instead. Degrade to a refused start, never to silence.
DO $$
DECLARE
    pinned text := current_setting('cron.database_name', true);
BEGIN
    IF pinned IS DISTINCT FROM current_database() THEN
        RAISE EXCEPTION
            'pg_cron is pinned to database "%" but this database is "%". %',
            coalesce(pinned, '<unset>'), current_database(),
            'Set POSTGRES_DB and cron.database_name to the same value '
            '(docker-compose.yml passes POSTGRES_DB through automatically), '
            'then recreate the volume. Phase 2 timer recovery depends on it.';
    END IF;
END $$;

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
