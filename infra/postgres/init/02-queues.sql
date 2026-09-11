-- Result Guardian — pgmq queue creation
-- Phase 0.7. Idempotent: pgmq.create() is safe to re-run.

SELECT pgmq.create('sla_timers');      -- Phase 2: result_due, reminders, escalation rungs
SELECT pgmq.create('notifications');   -- Phase 4: in_app / email / sms dispatch
SELECT pgmq.create('ingest');          -- Phase 6: document intake -> text + spans
SELECT pgmq.create('extract');         -- Phase 7: text -> structured JSON -> matching
SELECT pgmq.create('dlq');             -- terminal failures after max attempts

-- Worker liveness. /api/health reports worker_heartbeat_age_s from this table,
-- and Phase 10.4 alerts when the worker is down more than 5 minutes.
CREATE TABLE IF NOT EXISTS worker_health (
    worker_name   TEXT PRIMARY KEY,
    last_beat_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    pid           INTEGER,
    version       TEXT
);
