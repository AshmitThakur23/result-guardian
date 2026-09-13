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

If **delivery receipts** specifically are missing, check whether the provider
is being refused: with `RG_WEBHOOK_SECRET` set, a callback that does not carry
the matching `X-Webhook-Secret` header gets a 401 and the receipt is never
recorded. See section 9.

```bash
docker compose logs api | grep -E "webhook_secret_rejected|webhook_unauthenticated" | tail
```

---

## 9. Before a pilot — settings that must be set

Two settings ship with defaults that are right for a development machine and
**wrong for a ward**. Neither stops the system working, which is exactly why
they need a checklist entry rather than a runtime error.

Check both, on NODE A, before the first real patient:

```bash
docker compose exec api python -c "\
from app.config import get_settings; s = get_settings(); \
print('webhook_secret set:', bool(s.webhook_secret)); \
print('auth rate limit  :', s.auth_rate_limit_per_minute)"
```

### `RG_WEBHOOK_SECRET` — 🔴 must be set

Authenticates the SMS provider's delivery-receipt callback
(`POST /api/notifications/delivery-receipt`). That endpoint is the **only**
one with no user behind it — a provider callback cannot hold a bearer token —
so it sits outside RBAC and this secret is what stands in its place. The
provider sends the value in an `X-Webhook-Secret` header; a request without
it, or with the wrong value, is refused with 401.

**Leaving it unset leaves the webhook unauthenticated.** Anyone who can reach
NODE A's port can then assert that a patient's message was delivered. That is
not a data leak; it is worse in one specific way — it **falsifies the evidence
that a patient was reached**, which is the number the patient-contact metric
and a NABH reviewer both rely on.

**The current default is unset, and that state must not be used for a
real-patient deployment.** It is unset by default only so that upgrading from
Phase 4 does not break a working SMS integration on the day of the upgrade.
While it is unset the endpoint logs a warning on **every** call:

```bash
docker compose logs api | grep webhook_unauthenticated | tail -5
```

Setting it:

1. Generate a value — never reuse another secret, and never invent a
   memorable one:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Put it in `.env` as `RG_WEBHOOK_SECRET=...` and `docker compose up -d api`.
3. Give the same value to the SMS provider for the `X-Webhook-Secret` header.
4. Confirm it is live — an unsigned call must now be refused:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST \
     localhost/api/notifications/delivery-receipt \
     -H 'Content-Type: application/json' \
     -d '{"provider_msg_id":"probe","delivered":true}'
   # expect 401
   ```

**Keep it private.** Treat it exactly like `RG_JWT_SECRET`: it lives only in
`.env` (root-owned, 0600), never in a commit, a URL, a ticket or a chat
message. Rotate it — new value in `.env`, restart, update the provider — if it
is ever exposed or if someone who knew it leaves.

### `RG_AUTH_RATE_LIMIT_PER_MINUTE` — check the value

Failed authentication attempts allowed per source address per minute before
`/api/auth/login` and `/api/auth/refresh` answer **429**. **Default when
unset: 20** — far above a person typing a password, far below what a
credential spray needs.

This is **not** the account lockout. That is a separate control: 5 failures
locks one account for 15 minutes, counted on the account rather than the
address, and it is not configurable. This limit protects the *server* against
volume, including spraying across many accounts, which a per-account lockout
cannot see.

Two things to know before changing it:

* **`docker-compose.override.yml` raises it to 500, and that file is
  development only.** It exists so the E2E suite — which signs in before
  nearly every navigation, all from one browser — does not throttle itself. A
  hospital deployment runs `docker-compose.yml` alone and gets the default.
  **Confirm the effective value on the machine** with the command above rather
  than assuming.
* It is counted **in process, per API container**, and resets on restart. NODE
  A runs a single API container, so today the limit is the limit. If the API
  is ever scaled out, each replica gets its own allowance and this moves into
  Postgres.

Raise it only where many real users share one apparent address — a ward behind
a single NAT, for instance — and write down why.

---

## 10. Backups

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

## 11. Who to call

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
