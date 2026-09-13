# Runbook — NODE A

> **Who this is for:** whoever is on call for Result Guardian at 3am.
> Everything here can be done from NODE A's terminal. Nothing here needs
> NODE B, and nothing here needs a developer.

**The one thing to understand before anything else:**

> **Tracking, timers, escalation and notification all run on NODE A.**
> NODE B does summarisation and search quality. If NODE B is off, unreachable,
> or returning nonsense, **no patient is at risk** — the system loses a
> convenience, never a guarantee. See [RULE 1 and RULE 2](../CLAUDE.md).
>
> The failure that *does* put a patient at risk is **the worker not running**,
> because that is what fires the timers. Check that first, every time.

---

## 0. Thirty-second triage

```bash
cd /path/to/result-guardian

docker compose ps                 # is everything up?
curl -s localhost/api/health | jq # what does the system say about itself?
```

`/api/health` answers without authentication and never blocks. Read it like
this:

| Field | Healthy | What it means when it is not |
|---|---|---|
| `status` | `"ok"` | `"degraded"` means **the database is unreachable**. Section 3. |
| `db` | `"ok"` | Same. This is the only thing that decides the HTTP status. |
| `worker_heartbeat_age_s` | a small number | **`null` or > 120 → timers may not be firing.** Section 2. This is the serious one. |
| `llm.reachable` | either | `false` is a normal operating state. Section 5. Not an emergency. |
| `degraded_features` | `[]` or `["llm_generation"]` | `"worker"` is serious; `"llm_generation"` is not. |

The dashboard header shows the same thing as a pill. `AI: offline — core
tracking unaffected` is **not** an incident.

---

## 1. Restarting

### The whole stack

```bash
docker compose restart            # keeps data, keeps the database up
```

### One service

```bash
docker compose restart api
docker compose restart worker
docker compose restart caddy
```

### After a machine reboot

```bash
docker compose up -d
docker compose exec api alembic upgrade head   # safe to run when already current
curl -s localhost/api/health | jq
```

**Nothing is lost by a reboot.** Timers live in PostgreSQL, not in memory:
`pg_cron` re-enqueues anything that came due while the machine was off, and
the worker drains the backlog on startup. That is the Phase 2 guarantee and it
is tested (`tests/test_timer_recovery.py`).

### After a code change

The **worker does not hot-reload**. The API does (in development). If you
have changed code, restart the worker explicitly or it will keep running the
old module:

```bash
docker compose restart worker
```

---

## 2. The worker is not responding

**This is the one that matters.** A dead worker means escalations are not
firing, and a flagged result can sit unseen.

### Confirm it

```bash
curl -s localhost/api/health | jq '.worker_heartbeat_age_s, .degraded_features'
docker compose ps worker
docker compose logs worker --tail 100
```

### Fix it

```bash
docker compose restart worker
# Then watch the heartbeat come back:
watch -n 5 "curl -s localhost/api/health | jq .worker_heartbeat_age_s"
```

### Check nothing was missed

Timers that came due while the worker was down are still `pending` and will
fire as soon as it is back. To see the backlog:

```bash
docker compose exec postgres psql -U rg_app -d result_guardian -c "
  SELECT timer_type, count(*), min(fire_at) AS oldest_due
    FROM sla_timers
   WHERE status = 'pending' AND fire_at < now() AND deleted_at IS NULL
   GROUP BY timer_type ORDER BY oldest_due;"
```

An empty result means nothing is overdue. The dashboard's **Overdue only**
filter shows the same thing per case, which is what to give a unit head.

### If the worker keeps dying

Look for the exception in `docker compose logs worker`. Messages that fail
five times are **dead-lettered**, not dropped:

```bash
docker compose exec postgres psql -U rg_app -d result_guardian -c "
  SELECT count(*) FROM pgmq.a_sla_timers;   -- archived (dead-lettered)"
```

A dead-lettered timer is a case that did not escalate. Find them, fix the
cause, then fire them manually (section 4).

---

## 3. The database is unreachable

```bash
docker compose ps postgres
docker compose logs postgres --tail 50
docker compose restart postgres
```

The API returns **503** while the database is down, and the dashboard shows
`Database unavailable`. Nothing is lost — no case closes and no timer is
cancelled without a committed transaction — but nothing fires either. Treat it
as a full outage.

Check the extensions are loaded after any restart from a fresh volume:

```bash
docker compose exec postgres psql -U rg_app -d result_guardian -c '\dx'
```

`vector`, `pgmq`, **`pg_cron`**, `pg_trgm`, `unaccent`, `pgcrypto` must all be
present. **Without `pg_cron` the sweep does not run and overdue timers are
never re-enqueued** — the system looks healthy and is not.

---

## 4. Firing a stuck timer by hand

Only after you know *why* it is stuck. Firing a timer that a bug is going to
re-stick just moves the problem.

```bash
# 1. Find it.
docker compose exec postgres psql -U rg_app -d result_guardian -c "
  SELECT id, case_id, timer_type, escalation_level, fire_at, status, attempts
    FROM sla_timers
   WHERE status = 'pending' AND fire_at < now() - interval '30 minutes'
     AND deleted_at IS NULL
   ORDER BY fire_at LIMIT 20;"

# 2. Put a wake-up back on the queue for it. The worker picks it up within a
#    few seconds. This is idempotent: the timer's own status check means a
#    duplicate wake-up is declined, not double-fired.
docker compose exec postgres psql -U rg_app -d result_guardian -c "
  SELECT pgmq.send('sla_timers', json_build_object('timer_id', '<TIMER-UUID>')::jsonb);"
```

**Never** `UPDATE sla_timers SET status = 'fired'` by hand. That marks the
rung as done without anyone being told — the exact outcome the product exists
to prevent.

To re-enqueue everything overdue at once (after fixing the cause):

```bash
docker compose exec postgres psql -U rg_app -d result_guardian -c "
  SELECT rg_sweep_overdue_sla_timers();"
```

---

## 5. NODE B is down

**Nothing needs to be done urgently.** Confirm the scope, then decide at
leisure.

```bash
curl -s localhost/api/health | jq .llm
curl -s -m 3 http://<NODE_B_IP>:11434/api/tags    # from NODE A
```

What is affected: summarisation and search ranking (Phases 6–9).
What is **not** affected: discharge gating, tracking, timers, classification,
escalation, notification, closure, audit. All of it is NODE A.

### Turning inference off deliberately

If NODE B is up but returning nonsense, turn it off rather than leaving it to
degrade quietly. **Sign in as an admin → Admin → NODE B → Turn inference
off**, with a reason. It takes effect within ten seconds, needs no restart,
and is written to the audit log.

The setting lives in the `system_settings` table, so it survives a restart:

```bash
docker compose exec postgres psql -U rg_app -d result_guardian -c "
  SELECT key, value, updated_at FROM system_settings WHERE key LIKE 'llm%';"
```

`RG_LLM_ENABLED` in the environment is only the **default**; the table
overrides it.

---

## 6. Someone is locked out

Five failed logins lock an account for 15 minutes. It clears on its own.

To clear it now: **Admin → Users → Unlock**. Or:

```bash
docker compose exec postgres psql -U rg_app -d result_guardian -c "
  UPDATE users SET locked_until = NULL, failed_login_count = 0
   WHERE employee_code = '<CODE>';"
```

To issue a new password, use **Admin → Users → Reset password** — it returns a
temporary password **once**, forces a change on next login, and revokes every
session the user holds. Do not set `password_hash` by hand; the column holds
an Argon2id hash, not a password.

**A pattern of lockouts is worth looking at.** The account counter is shared
by everyone who knows a colleague's employee code, so repeated lockouts on one
account are either a person with a bad keyboard or somebody guessing:

```bash
docker compose exec postgres psql -U rg_app -d result_guardian -c "
  SELECT a.occurred_at, a.after->>'reason' AS reason, HOST(a.actor_ip) AS ip
    FROM audit_log a
   WHERE a.action IN ('auth.login_failed','auth.account_locked')
     AND a.occurred_at > now() - interval '24 hours'
   ORDER BY a.seq DESC LIMIT 50;"
```

---

## 7. The audit chain reports a break

**Escalate this to the hospital's information-security contact. Do not try to
fix it.**

A break means a row in `audit_log` was changed, removed or inserted after it
was written. Three layers had to be got past for that to happen, so it is not
an accident.

1. **Do not restart anything.** A restart proves nothing and may lose logs.
2. Record what the verifier says, with the sequence number:

```bash
# Sign in as an auditor and use Audit trail → Verify the chain, or:
docker compose exec postgres psql -U rg_app -d result_guardian -c "
  SELECT * FROM rg_verify_audit_chain();"
```

3. Compare against the published anchors — the nightly head hashes. If an
   anchor exported off this machine still matches, the tampering is newer
   than that anchor and you have bounded the window:

```bash
docker compose exec postgres psql -U rg_app -d result_guardian -c "
  SELECT anchored_at, head_seq, head_hash, chain_intact, first_break_seq
    FROM audit_anchors ORDER BY anchored_at DESC LIMIT 14;"
```

4. Take a filesystem-level backup of the database volume before anything else
   touches it.

**Export the anchors off this machine regularly.** An anchor that only exists
on the same disk as the log is no harder to rewrite than the log:

```bash
docker compose exec -T postgres psql -U rg_app -d result_guardian -A -F, -c "
  SELECT anchored_at, head_seq, head_hash FROM audit_anchors
   ORDER BY anchored_at DESC LIMIT 31;" > anchors-$(date +%F).csv
```

---

## 8. Notifications are not arriving

```
Admin → Notification providers
```

shows sent/failed/suppressed per channel for the last 24 hours, the adapter in
use, and the last error. An adapter shown as `InAppAdapter (falls back to
in_app)` means **no provider is configured for that channel** — the message
was still delivered on screen, which is the designed degradation, but nothing
left the building.

Failed notifications can be listed and retried:

```bash
curl -s localhost/api/notifications/failed -H "Authorization: Bearer <TOKEN>" | jq
```

Suppressed is not failed: quiet hours (22:00–07:00 IST) hold non-critical
patient messages until morning **by design**. A CRITICAL case is never
suppressed.

---

## 9. Backups

The whole system is one PostgreSQL database. There is nothing else to back up
except the `.env` file.

```bash
# Nightly, off this machine.
docker compose exec -T postgres pg_dump -U rg_app -Fc result_guardian \
  > rg-$(date +%F).dump
```

Restore into an empty volume:

```bash
docker compose exec -T postgres pg_restore -U rg_app -d result_guardian --clean \
  < rg-YYYY-MM-DD.dump
docker compose exec api alembic upgrade head
```

⚠️ **A restore rewinds the audit log**, so verify the chain afterwards and
keep the pre-restore dump. The anchors from the discarded period will not
match; that is expected and should be recorded.

---

## 10. Who to call

| Symptom | Severity | Who |
|---|---|---|
| Worker heartbeat stale > 5 min | **Urgent** — escalations are not firing | On-call engineer, now |
| `/api/health` 503 / database down | **Urgent** — full outage | On-call engineer, now |
| Audit chain reports a break | **Urgent** — possible tampering | Information security, now. Do not restart. |
| A CRITICAL case is overdue on the worklist | **Urgent** — clinical | Unit head for that department |
| NODE B unreachable | Routine | Whenever convenient. Say so in handover. |
| Notification provider failing | Routine, same day | On-call engineer |
| A user locked out | Routine | Any admin |

---

## Appendix — useful queries

```sql
-- Open CRITICAL cases, oldest first. The list that matters.
SELECT p.name, p.mrn, o.test_name, pc.flagged_at,
       now() - pc.flagged_at AS age, u.full_name AS owner
  FROM pending_cases pc
  JOIN patients p ON p.id = pc.patient_id
  JOIN orders o ON o.id = pc.order_id
  LEFT JOIN users u ON u.id = pc.current_owner_id
 WHERE pc.severity = 'critical' AND pc.closed_at IS NULL
   AND pc.deleted_at IS NULL
 ORDER BY pc.flagged_at;

-- Is pg_cron actually scheduled?
SELECT jobname, schedule, active FROM cron.job ORDER BY jobname;

-- Queue depth. A growing number means the worker is behind.
SELECT 'sla_timers' AS q, count(*) FROM pgmq.q_sla_timers
UNION ALL SELECT 'classify', count(*) FROM pgmq.q_classify
UNION ALL SELECT 'notifications', count(*) FROM pgmq.q_notifications;

-- Break-glass access in the last week. Every row should have a reason
-- somebody can defend.
SELECT occurred_at, break_glass_reason, actor_user_id
  FROM audit_log
 WHERE break_glass_reason IS NOT NULL
   AND occurred_at > now() - interval '7 days'
 ORDER BY seq DESC;
```
