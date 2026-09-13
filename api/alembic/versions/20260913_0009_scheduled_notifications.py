"""Phase 4.5 + ADR 0004 — the two scheduled notification jobs.

Two bullets in Phase 4 are not events; they are *schedules*, and until
something runs them on a clock they are only functions nobody calls:

**4.5's digest** — *"one email per doctor per morning listing all open
FOLLOW_UP flags, not one each."* Without this job, a follow-up that was
quiet-houred or rate-capped is held back and never mentioned again, which
would turn two fatigue controls into two ways of losing a flag.

**ADR 0004 guard 1** — *"Weekly reminder — notification to each unit head to
confirm or update the coming week's roster."* The ADR is explicit that a
decision about who maintains data is worthless without staleness detection,
and requires this in the same sprint as 4.1.

Both are ``pg_cron`` jobs calling SQL functions that **enqueue intents** onto
the existing ``notifications`` queue. That is deliberate:

* the same mechanism Phase 2.1 uses for the timer sweep, so there is one way
  scheduled work happens in this system rather than two;
* the function only *enqueues*, so quiet hours, rate caps, deduplication and
  template rendering all still run in the Phase 4.3 dispatcher — a digest
  cannot bypass the policy that the rest of the notifications obey;
* ``pgmq`` is durable, so a worker that is down at 07:00 sends the digest when
  it comes back rather than skipping the day.

⚠️ **Times are UTC** because that is what ``pg_cron`` uses. 01:30 UTC is
07:00 IST, which is the hour quiet hours end — the digest arrives exactly
when the ward can act on it.

``downgrade()`` unschedules both jobs and drops both functions.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_scheduled_notifications"
down_revision: str | None = "0008_ownership_escalation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIGEST_JOB = "rg-follow-up-digest"
ROSTER_JOB = "rg-roster-weekly-reminder"

# 01:30 UTC = 07:00 IST, the moment quiet hours lift.
DIGEST_CRON = "30 1 * * *"
# 03:30 UTC Friday = 09:00 IST Friday, before the weekend rota is set.
ROSTER_CRON = "30 3 * * 5"


def upgrade() -> None:
    # A digest spans every open follow-up a doctor owns, and the roster
    # reminder belongs to no case at all. Both still have to be recordable,
    # so `notifications.case_id` becomes nullable.
    op.alter_column("notifications", "case_id", nullable=True)

    # ── 4.5 · the morning digest ──────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION rg_enqueue_follow_up_digests()
        RETURNS integer
        LANGUAGE plpgsql
        AS $$
        DECLARE
            owner_row  record;
            queued     integer := 0;
        BEGIN
            -- One row per doctor who owns at least one open FOLLOW_UP flag.
            -- CRITICAL is deliberately excluded: those are sent as they
            -- happen and must never wait for a morning batch.
            FOR owner_row IN
                SELECT pc.current_owner_id AS user_id,
                       count(*)            AS case_count
                  FROM pending_cases pc
                 WHERE pc.current_owner_id IS NOT NULL
                   AND pc.severity = 'follow_up'
                   AND pc.state = 'flagged'
                   AND pc.deleted_at IS NULL
                 GROUP BY pc.current_owner_id
            LOOP
                PERFORM pgmq.send(
                    'notifications',
                    jsonb_build_object(
                        'template_key', 'follow_up_digest',
                        'audience',     'responsible_doctor',
                        'user_id',      owner_row.user_id,
                        'digest',       true,
                        'case_count',   owner_row.case_count,
                        -- The dispatcher reads the cases itself; the intent
                        -- only says who is owed a digest and when.
                        'generated_at', now()
                    )
                );
                queued := queued + 1;
            END LOOP;
            RETURN queued;
        END;
        $$;
        """)

    # ── ADR 0004 guard 1 · the weekly roster reminder ─────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION rg_enqueue_roster_reminders()
        RETURNS integer
        LANGUAGE plpgsql
        AS $$
        DECLARE
            dept_row record;
            queued   integer := 0;
        BEGIN
            FOR dept_row IN
                SELECT d.id,
                       d.code,
                       d.name,
                       d.unit_head_user_id,
                       EXISTS (
                           SELECT 1 FROM duty_roster r
                            WHERE r.department_id = d.id
                              AND r.deleted_at IS NULL
                              AND r.shift_end > now()
                       ) AS has_upcoming_shift
                  FROM departments d
                 WHERE d.active
                   AND d.deleted_at IS NULL
                   AND d.unit_head_user_id IS NOT NULL
            LOOP
                PERFORM pgmq.send(
                    'notifications',
                    jsonb_build_object(
                        'template_key', 'roster_weekly_reminder',
                        'audience',     'unit_head',
                        'user_id',      dept_row.unit_head_user_id,
                        'department_id', dept_row.id,
                        'department_name', dept_row.name,
                        -- "Confirming an unchanged roster is one click; it
                        -- must not require re-entry." The flag lets the
                        -- template say which of the two this is.
                        'has_upcoming_shift', dept_row.has_upcoming_shift,
                        'generated_at', now()
                    )
                );
                queued := queued + 1;
            END LOOP;
            RETURN queued;
        END;
        $$;
        """)

    # ── schedule both ─────────────────────────────────────────────
    # Unschedule first so the migration is safe to re-run, and guard the
    # database name exactly as migration 0005 does: pg_cron lives in one
    # database, and a job scheduled into the wrong one fires against nothing.
    for job, schedule, function in (
        (DIGEST_JOB, DIGEST_CRON, "rg_enqueue_follow_up_digests"),
        (ROSTER_JOB, ROSTER_CRON, "rg_enqueue_roster_reminders"),
    ):
        op.execute(f"""
            DO $$
            BEGIN
                PERFORM cron.unschedule('{job}')
                  FROM cron.job WHERE jobname = '{job}';
                PERFORM cron.schedule(
                    '{job}', '{schedule}', 'SELECT {function}()'
                );
            END $$;
            """)


def downgrade() -> None:
    for job in (ROSTER_JOB, DIGEST_JOB):
        op.execute(f"""
            DO $$
            BEGIN
                PERFORM cron.unschedule('{job}')
                  FROM cron.job WHERE jobname = '{job}';
            END $$;
            """)
    op.execute("DROP FUNCTION IF EXISTS rg_enqueue_roster_reminders()")
    op.execute("DROP FUNCTION IF EXISTS rg_enqueue_follow_up_digests()")
    op.execute("DELETE FROM notifications WHERE case_id IS NULL")
    op.alter_column("notifications", "case_id", nullable=False)
