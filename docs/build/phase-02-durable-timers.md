# Phase 2 — Durable Tracking + Timers

**Goal:** the clock is correct after crashes, restarts and clock changes.
**Duration:** 1–1.5 weeks · **Node:** A only · **Depends on NODE B?** No

---

## 📍 STATUS SUMMARY — Phase 2

> ⚠️ **Do not start this phase until Exit Gate 1 passes.**

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 2.1 Timer model | A | ✅ **done** | Migration `0005_sla_timers`. Table + 4 CHECKs + 3 FKs + 5 indexes, **0 PG enum types**, `alembic check` drift 0, and a real head→0004→head round trip. `pg_cron` job `rg-sla-timer-sweep` scheduled `*/5 * * * *`. **36 new tests.** ⚠️ Creating a timer on discharge is **2.2**, not here |
| 2.2 Timer lifecycle | A | ✅ **done** | Discharge creates the durable `result_due`; idempotent fire handler under `SELECT … FOR UPDATE`; atomic cancel on closure; supersede on result; pause/resume for deceased/transferred. **All four concurrency races tested on two real connections** |
| 2.3 Missing-result path / lab flags ★ | A | ✅ **done** | `lab_flags` (migration 0006), case stays `awaiting_result`, lab **and** doctor notified, 24h re-check to a 7-day ceiling then unit-head escalation, `GET /api/lab-flags/metrics` |
| 2.4 Result intake (manual) | A | ✅ **done** | `POST /api/orders/{id}/results` → `result_received`, supersedes `result_due`, enqueues classification. Replay-protected on `(source, source_ref)` |
| 2.5 Tests | A | ✅ **done** | 85 new backend tests. Fires once under 3× and 5× delivery; visibility-lapse redelivery; 2-hours-down recovery; cancelled-in-flight no-op; UTC/IST |
| **Exit Gate 2** | A | ✅ **PASSED** | `scripts/chaos_exit_gate_2.sh`: 50 cases, 2-min deadlines, **restarted twice** (api+worker, then api+worker+postgres) → **exactly 50 flags, 50 fired timers, 50 events, max 1 flag per case** |

---

## 2.1 Timer model

- [x] **`sla_timers`** — id, case_id, timer_type, fire_at, status (`pending|fired|cancelled|superseded`), pgmq_msg_id, attempts, fired_at, idempotency_key (unique) — [`models/timers.py`](../../api/app/db/models/timers.py), migration [`0005_sla_timers`](../../api/alembic/versions/20260912_0005_sla_timers.py). Every field present, plus the Phase 0.6 audit/soft-delete columns every clinical table since 1.1 carries. `pgmq_msg_id` is **BIGINT** to match `pgmq.q_sla_timers.msg_id`, and **nullable** on purpose
  - [x] timer_type: `result_due`, `owner_reminder`, `unit_head_escalation`, `patient_notification`, `stale_preliminary` — text + CHECK, never a PG enum. **Not one value more**
- [x] **Timers are derived from the DB, not only from the queue.** pgmq carries the wake-up; **the table carries the truth.** `pgmq_msg_id` points outward, from truth to wake-up, and never back — a missing message is indistinguishable from a timer that never existed, one that fired, and one that was lost, so the answer lives where it can be constrained and audited
- [x] `pg_cron` sweep **every 5 minutes**: any pending timer with `fire_at < now() - interval '10 min'` and no live queue message → re-enqueue — `rg_sweep_overdue_sla_timers()`, scheduled as `rg-sla-timer-sweep`. Written as a function rather than inline in the cron command so a test can call it instead of waiting five minutes. Verified: re-enqueues an overdue timer with no message, **leaves one that still has a live message**, ignores the 10-minute grace window, ignores non-pending and soft-deleted timers, and never changes timer state

### Scope boundary, stated explicitly

**"Create on discharge: `result_due` at `contract.expected_by`" is §2.2, not §2.1.** Phase 1.3's discharge still enqueues its own wake-up and writes **no** `sla_timers` row; wiring the two together is 2.2's first bullet. Until then the sweep has nothing to sweep — the correct state of a half-built phase, not a defect, and the reason the sweep is tested by inserting timer rows directly.

### ⚠️ Spec ambiguity — `idempotency_key`

The build plan names the column and marks it unique, and **never defines its construction**. Reported rather than silently decided. The construction used is `case_id:timer_type:fire_at` (UTC, microsecond precision) — the only reading that satisfies both 2.2 (one `result_due` per case at `contract.expected_by`; a replayed discharge must not create a second) and 2.3 (a re-check "every 24h until resolved" is a different instant and so must be a different timer). A second `UNIQUE(case_id, timer_type, fire_at)` was **not** added: the plan specifies one unique column, and encoding the rule twice means two places to change if 2.2 refines it.

`(status = 'fired') = (fired_at IS NOT NULL)` is likewise **derived** from the column's meaning rather than quoted. If a 2.2 lifecycle transition is refused by it, that constraint is the thing to revisit.

## 2.2 Timer lifecycle

- [x] Create on discharge: `result_due` at `contract.expected_by` — [`services/timers.py`](../../api/app/services/timers.py) `create_timer`, called from the Phase 1.3 discharge transaction. **Truth first, doorbell second**: the row is inserted `ON CONFLICT (idempotency_key) DO NOTHING`, and only the winner enqueues a wake-up, so a replay produces neither a second timer nor a second message
- [x] Fire handler is **idempotent**: check `status != 'fired'` inside a `SELECT ... FOR UPDATE` before acting — [`worker/consumers/sla_timers.py`](../../api/worker/consumers/sla_timers.py). The lock is held across the flag, the events, the notifications and the next timer, so a duplicate delivery finds `fired` and does nothing
- [x] Cancel all case timers **atomically** on case closure — [`services/cases.py`](../../api/app/services/cases.py) `close_case`. One `UPDATE ... WHERE status = 'pending'`, so there is no window with half a case cancelled, and a timer a worker already fired is left as history rather than rewritten
- [x] Supersede: new result arrives → cancel `result_due`, create classification timers — superseded, not cancelled, because the plan distinguishes them and so should an audit six months later. A **preliminary** report creates a `stale_preliminary` timer (a final report is still owed); a final/amended/corrected one creates none
- [x] Pause capability for patient `deceased` / `transferred` states — `paused_at` + `pause_reason`, **not a fifth status**. The timer stays `pending` and keeps its deadline; the fire handler and the sweep skip it. The reason is read from the encounter, never accepted from a caller — a client that could assert "this patient died" could silence any case's timers

## 2.3 Missing-result path — lab accountability ★

> The lab must be flagged too, not just the doctor. **A missing result is a lab-side failure.**

- [x] `result_due` fires with no result → `pending_cases.state` stays `awaiting_result`, raise a lab-side flag — the state deliberately does **not** move; an "overdue" state would make the case vanish from the one query that matters
- [x] **`lab_flags`** — id, case_id, flag_type (`sample_missing`, `report_delayed`, `sample_rejected`), raised_at, resolved_at, resolved_by, resolution_note — [`models/lab.py`](../../api/app/db/models/lab.py), migration 0006. A partial unique index allows **one open flag of a type per case**, so a repeated re-check cannot fill the lab's queue with copies of one problem
- [x] Notify: lab department queue **+** responsible doctor — both, as durable *intents* on the existing `notifications` queue. Channel, template, quiet hours and the escalation ladder are Phase 4.3/4.6 and are deliberately absent
- [x] Re-check timer every 24h until resolved, **max 7 days**, then escalate to unit head — `owner_reminder` timers to the ceiling, then one `unit_head_escalation`. A re-check is never scheduled past the ceiling
- [x] Lab-side metric on the admin dashboard: open lab flags by age — `GET /api/lab-flags/metrics`, bucketed `under_24h | 1_to_3_days | 3_to_7_days | over_7_days`. **The measurement only**; the dashboard that draws it is Phase 5.4, so that phase is a rendering job rather than a rendering *and* measurement job. Counts carry no patient identifiers

## 2.4 Result intake (manual)

- [x] `POST /api/orders/{id}/results` — accepts a structured payload — [`routers/orders.py`](../../api/app/routers/orders.py). Replay-protected on `(source, source_ref)`; a result for an order with no case is still stored rather than lost
- [x] Transitions case to `result_received`, cancels `result_due`, enqueues classification — all in one transaction, so a recorded result can never leave a live timer behind to flag a lab for a report that arrived
- [x] **This endpoint stays forever.** Built as a production endpoint, not a test shortcut. **It interprets nothing**: the payload is stored verbatim and no severity is assigned — RULE 1, the tracking guarantee must not wait on interpretation

## 2.5 Tests

- [x] Timer fires **once** even if the queue delivers the message 3 times — and 5. The *clinical effect* is counted (one lab flag, one `result_due_fired` event), not the message
- [x] Kill worker mid-processing → message returns after visibility timeout → still exactly one effect — the kill is simulated by claiming the message and never acknowledging it, then lapsing its visibility timeout; `read_ct` is asserted to have advanced, which is what a SIGKILL mid-handler actually leaves behind
- [x] Stop the whole stack for 2 hours past `fire_at` → on restart the sweep fires it — verified twice: as a unit test, and for real in the Exit Gate chaos run
- [x] Cancelled timer that is already in flight does nothing when consumed
- [x] DST/timezone: all math in UTC, IST display verified — the same instant expressed in IST and UTC produces **one** timer, because the idempotency key normalises to UTC

---

## ✅ EXIT GATE 2 — **PASSED 2026-09-12**

> Audited independently on 2026-09-12 and pushed as `13a7151`. CI green (run
> `34716574610`): 425 backend tests, coverage 88.20%, lint/types/build clean.
> The audit additionally proved **sweep-only recovery** — the entire wake-up
> queue was destroyed and all ten timers still fired exactly once, which is the
> strongest evidence that PostgreSQL, not pgmq, is timer truth.

- [x] **Chaos test:** create 50 cases with deadlines 2 minutes out, `docker compose restart` **twice** during the window, then verify **exactly 50 flags** — no duplicates, no misses.
  — [`scripts/chaos_exit_gate_2.sh`](../../scripts/chaos_exit_gate_2.sh), run from the host because it restarts the containers the suite runs inside. 50 real discharges over HTTP through Caddy; restart 1 is api+worker, restart 2 also takes **postgres**. Result: **50 flags, 50 fired `result_due` timers, 50 `result_due_fired` events, max 1 flag on any case.** The assertion reads `lab_flags` out of PostgreSQL — a duplicate queue message is not a second clinical event.

---

**Cross-ref:** [architecture/01-patient-to-discharge-gate.md](../architecture/01-patient-to-discharge-gate.md) (Step 3)
