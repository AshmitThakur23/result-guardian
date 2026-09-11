-- Result Guardian — pgmq queue creation
-- Phase 0.7. Idempotent: pgmq.create() is safe to re-run.

SELECT pgmq.create('sla_timers');      -- Phase 2: result_due, reminders, escalation rungs
SELECT pgmq.create('notifications');   -- Phase 4: in_app / email / sms dispatch
SELECT pgmq.create('ingest');          -- Phase 6: document intake -> text + spans
SELECT pgmq.create('extract');         -- Phase 7: text -> structured JSON -> matching
SELECT pgmq.create('dlq');             -- terminal failures after max attempts

-- worker_health is NOT here. It is an application table, so it belongs to
-- Alembic (revision 0001_baseline), not to an init script that only ever runs
-- against an empty data directory. Keeping it here would mean CI, test
-- containers and any restored volume silently lack it.
--
-- Queues and extensions stay in this directory because they must exist before
-- Alembic first connects.
