# Phase 2 — Durable Tracking + Timers

**Goal:** the clock is correct after crashes, restarts and clock changes.
**Duration:** 1–1.5 weeks · **Node:** A only · **Depends on NODE B?** No

---

## 2.1 Timer model

- [ ] **`sla_timers`** — id, case_id, timer_type, fire_at, status (`pending|fired|cancelled|superseded`), pgmq_msg_id, attempts, fired_at, idempotency_key (unique)
  - timer_type: `result_due`, `owner_reminder`, `unit_head_escalation`, `patient_notification`, `stale_preliminary`
- [ ] **Timers are derived from the DB, not only from the queue.** pgmq carries the wake-up; **the table carries the truth.**
- [ ] `pg_cron` sweep **every 5 minutes**: any pending timer with `fire_at < now() - interval '10 min'` and no live queue message → re-enqueue. This closes the gap if the queue is ever lost.

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
