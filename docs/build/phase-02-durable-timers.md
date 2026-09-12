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
| 2.2 Timer lifecycle | A | ⬜ not started |  |
| 2.3 Missing-result path / lab flags ★ | A | ⬜ not started |  |
| 2.4 Result intake (manual) | A | ⬜ not started |  |
| 2.5 Tests | A | ⬜ not started |  |
| **Exit Gate 2** | A | ⬜ **not started** | |

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

- [ ] Create on discharge: `result_due` at `contract.expected_by`
- [ ] Fire handler is **idempotent**: check `status != 'fired'` inside a `SELECT ... FOR UPDATE` before acting
- [ ] Cancel all case timers **atomically** on case closure
- [ ] Supersede: new result arrives → cancel `result_due`, create classification timers
- [ ] Pause capability for patient `deceased` / `transferred` states

## 2.3 Missing-result path — lab accountability ★

> The lab must be flagged too, not just the doctor. **A missing result is a lab-side failure.**

- [ ] `result_due` fires with no result → `pending_cases.state` stays `awaiting_result`, raise a lab-side flag
- [ ] **`lab_flags`** — id, case_id, flag_type (`sample_missing`, `report_delayed`, `sample_rejected`), raised_at, resolved_at, resolved_by, resolution_note
- [ ] Notify: lab department queue **+** responsible doctor
- [ ] Re-check timer every 24h until resolved, **max 7 days**, then escalate to unit head
- [ ] Lab-side metric on the admin dashboard: open lab flags by age

## 2.4 Result intake (manual)

- [ ] `POST /api/orders/{id}/results` — accepts a structured payload
- [ ] Transitions case to `result_received`, cancels `result_due`, enqueues classification
- [ ] **This endpoint stays forever.** Phases 6–7 just add an automatic caller for it. It is also the permanent fallback when extraction fails.

## 2.5 Tests

- [ ] Timer fires **once** even if the queue delivers the message 3 times
- [ ] Kill worker mid-processing → message returns after visibility timeout → still exactly one effect
- [ ] Stop the whole stack for 2 hours past `fire_at` → on restart the sweep fires it
- [ ] Cancelled timer that is already in flight does nothing when consumed
- [ ] DST/timezone: all math in UTC, IST display verified

---

## ✅ EXIT GATE 2

- [ ] **Chaos test:** create 50 cases with deadlines 2 minutes out, `docker compose restart` **twice** during the window, then verify **exactly 50 flags** — no duplicates, no misses.

---

**Cross-ref:** [architecture/01-patient-to-discharge-gate.md](../architecture/01-patient-to-discharge-gate.md) (Step 3)
