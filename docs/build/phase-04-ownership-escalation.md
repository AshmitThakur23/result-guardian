# Phase 4 — Ownership + Escalation ★

**Goal:** no flag can be silently ignored; **no doctor gets spammed into ignoring flags.**
**Duration:** 2–3 weeks · **Node:** A only · **Depends on NODE B?** No

---

## 📍 STATUS SUMMARY — Phase 4

> ⚠️ **Do not start this phase until Exit Gate 3 passes.**

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 4.1 Availability model | A | ✅ **done** | migration `0008`; unit head maintains it, weekly ([ADR 0004](../adr/0004-duty-roster-ownership.md)); all four ADR guards shipped |
| 4.2 Owner resolution | A | ✅ **done** | six steps + an explicit-assignment step 0 (audit B1); 26 tests |
| 4.3 Notification layer | A | ✅ **done** | 5 adapters, 3 locales, retry 3×, delivery webhook, failed-list endpoint |
| 4.4 Escalation engine | A | ✅ **done** | rungs on Phase 2 timers; ack cancels the rest atomically |
| 4.5 Alert fatigue controls ★ | A | ✅ **done** | quiet hours, digest (`pg_cron`), dedup, rate cap, flag-rate metric |
| 4.6 Patient notification | A | ✅ **done** | never carries the result; deceased/lama/transferred/unverified all handled |
| 4.7 Tests | A | ✅ **done** | 121 new backend tests; every 4.7 bullet covered |
| **Exit Gate 4** | A | ✅ **PASSED** | both clauses proven — see below |

> ⚠️ **Started with Exit Gate 3 open.** A knowing exception, on the project
> owner's instruction, recorded in [`PROGRESS.md`](../../PROGRESS.md). Phase 4
> routes whatever severity Phase 3 produces and does not depend on those
> severities being clinically *correct* — but **Exit Gate 4 passing does not
> close Exit Gate 3.**

---

## 4.1 Availability model

- [x] **`duty_roster`** — id, user_id, department_id, shift_start, shift_end, role_on_duty (`primary|backup|consultant`)
  - **Decide who maintains this.** A roster nobody updates is worse than no roster, because the system will confidently notify someone who left.
  - Options: HR sync (Phase 9.4), unit-head weekly entry, or admin upload. **Pick one and write it in the SOP.**
- [x] **`user_absences`** — id, user_id, absence_type (`leave|resigned|suspended|training`), starts_at, ends_at (null = indefinite), delegate_user_id
- [x] **`escalation_chain`** — id, department_id, level, target_type (`owner|roster_on_duty|unit_head|admin|patient`), delay_minutes, channels[], active
  - One row per rung, per department. **This is what makes escalation configurable per hospital.**
- [x] Roster-maintenance owner decided and recorded in `PROGRESS.md` — the unit head ([ADR 0004](../adr/0004-duty-roster-ownership.md))

## 4.2 Owner resolution

```
resolve_owner(case):
  1. contract.responsible_doctor → available? → use
  2. absence.delegate_user_id    → available? → use
  3. duty_roster primary for department at now() → use
  4. duty_roster backup → use
  5. department unit_head → use
  6. admin fallback + high-priority system alert
```

- [x] Each hop writes a `case_event` with the reason
- [x] `POST /api/cases/{id}/reassign` — manual reassign with **mandatory reason**
  - **Reassignment does not reset the escalation clock**, otherwise it becomes a dodge.

## 4.3 Notification layer

- [x] **`notifications`** — id, case_id, user_id (nullable), patient_id (nullable), channel (`in_app|email|sms|whatsapp`), template_key, locale, payload JSONB, status (`queued|sent|delivered|failed|suppressed`), provider_msg_id, attempts, sent_at, error
- [x] Adapter interface: `send(channel, recipient, template, context) -> ProviderResult`
- [x] Implementations: `InAppAdapter` (DB row + polling), `SmtpAdapter`, `SmsAdapter` (MSG91 / Gupshup — **DLT-registered templates required in India**), `NullAdapter` (tests)
- [x] Provider failure → retry **3×** with backoff → mark `failed` → alert admin dashboard (`GET /api/notifications/failed`)
- [x] Delivery receipt webhook endpoint for SMS provider
- [x] Template store: Jinja2 files per `template_key` × locale (`en`, `hi`, `pa`)

## 4.4 Escalation engine

| Rung | Default delay | Critical delay | Target | Channels |
|---|---|---|---|---|
| 0 | immediate | immediate | owner | in_app + email |
| 1 | +4h | +1h | owner | in_app + email + SMS |
| 2 | +12h | +4h | unit head | all |
| 3 | +24h | +8h | **patient** | SMS |
| 4 | +48h | +48h | admin / medical superintendent | all |

- [x] Delays read from `escalation_chain`, differentiated by severity
- [x] Each rung fires a timer; **acknowledgement cancels all remaining rungs in one transaction**
- [x] `case_events` records every rung with target and channel

## 4.5 Alert fatigue controls ★

> **The #1 killer of clinical alert systems.** Build these in the same sprint as the ladder, not later.

- [x] **Quiet hours** (default **22:00–07:00 IST**) — FOLLOW_UP batches to the next window; **CRITICAL always sends immediately**
- [x] **Digest** — `pg_cron` job `rg-follow-up-digest` at 07:00 IST. — one email per doctor per morning listing all open FOLLOW_UP flags, not one each
- [x] **Dedup / grouping** — multiple analytes in one report = **one** notification, not twelve. Multiple pending tests for one patient group into one message.
- [x] **Rate cap** — max N SMS per doctor per hour, overflow rolls into digest
- [x] **Metric on the admin dashboard: flag rate per 100 discharges.** `GET /api/metrics/flag-rate`; Phase 5.4 renders it. If this exceeds **~15%** you must retune thresholds before the pilot expands. **Put this number on the wall.**

## 4.6 Patient notification — handle carefully

- [x] **Never include the result in an SMS.** Template: *"Namaste. A test report from your recent visit to [Hospital] needs discussion. Please call [number] or visit OPD. — [Hospital]"*
- [x] Language from `patients.preferred_language`, fallback English
- [x] **Only send if `phone_verified_at` is set**; otherwise escalate to unit head instead
- [x] **Suppress entirely if encounter status is `deceased`.** Also handle `lama` and `transferred` — for `transferred`, notify the receiving facility contact instead if known.
- [x] Log consent basis for the message (`sms_consent_basis`) — read by the patient gate
- [x] Inbound: **`patient_contacts`** table so front desk can mark "patient called back"

## 4.7 Tests

- [x] Full ladder on a **compressed clock** (delays in seconds) → 4 rungs fire in order
- [x] Acknowledge at rung 1 → rungs 2–4 **never** fire
- [x] Owner on leave → delegate notified → event logged
- [x] Quiet hours: FOLLOW_UP at 23:00 is deferred, CRITICAL at 23:00 is **not**
- [x] SMS provider returns 500 → retried → failure surfaced, **ladder continues**
- [x] Patient deceased → patient rung suppressed, unit head notified instead
- [x] Unverified phone → patient rung skipped, escalates to unit head

---

## Deviations from the build plan, and why

| What | Why |
|---|---|
| **`case_escalation` is a sixth `timer_type`**, not a reuse of `owner_reminder` / `unit_head_escalation`. | Phase 2.3 already uses those two for the **lab** re-check chain ("the result has not arrived"). Phase 4's ladder is the opposite situation — the result arrived, was flagged, and nobody has looked. Sharing a type would leave the fire handler unable to tell the chains apart and it would run the wrong one. |
| **The idempotency key gained the rung.** | Two rungs may legitimately fall due at the same instant — a hospital can configure it, and a compressed test clock guarantees it. Without the rung in the key they collapsed onto one timer and the ladder silently lost its upper rungs. Phase 2 keys are byte-identical. |
| **`resolve_owner` gained a step 0: the explicitly assigned owner.** The plan's list starts at `contract.responsible_doctor`. | Without it `POST /reassign` is cosmetic: measured, a reassigned case still escalated to the original doctor. An explicit assignment is a human decision about *this* case and outranks the automatic chain — still subject to availability, so it cannot be used to silence a case. |
| **The escalation fire path takes the case lock before the timer lock.** | `close_case` and `record_result` already lock `pending_cases` before `sla_timers`; Phase 2's fire path locked no case at all. Rung 0 assigns the owner, which closed the cycle against `acknowledge_case` and produced a real `DeadlockDetectedError`. Only `case_escalation` takes the extra lock, so Phase 2 is untouched. |
| **Patient messages pass through the fatigue controls.** | The plan scopes quiet hours by *severity*, not audience. A FOLLOW_UP patient SMS was going out at 03:00; a patient can neither act on that nor tell whether it is urgent. CRITICAL still bypasses, and 4.6's absolute rules (deceased, unverified) still run first and are never merely "deferred". |
| **`notifications.case_id` is nullable.** | The morning digest spans every open follow-up a doctor owns and the weekly roster reminder belongs to no case. Forcing a case id would mean inventing one or not recording the send. |
| **Metrics ship as endpoints, not as dashboard UI.** | Phase 5.4 builds the dashboard. Phase 4 ships the measurement, the same split Phase 2.3 used for `/api/lab-flags/metrics`. Phase 4 adds no UI because the phase doc lists none. |
| **The digest and the roster reminder are `pg_cron` jobs enqueueing intents.** | The same mechanism as Phase 2.1's sweep, so there is one way scheduled work happens. Enqueueing (rather than sending) means a digest cannot bypass the quiet hours and rate caps every other notification obeys. |

---

## ✅ EXIT GATE 4

- [x] ✅ A critical flag left untouched walks **all four rungs** and produces a patient SMS (to a test number), with a complete `case_events` trail
      — `test_exit_gate_a_critical_flag_walks_all_rungs_to_a_patient_sms`: rungs 0–4 all fire, rung 3 sends an SMS through the adapter to the patient's number, and the trail carries five `escalation_rung_fired` events with target and channel on each.
- [x] ✅ Acknowledging at **any** rung stops everything
      — `test_exit_gate_acknowledging_at_any_rung_stops_everything`, parametrised over rungs 0/1/2/3: every remaining timer ends `cancelled`, and a rung that fires afterwards is a no-op.

**Gate verdict: ✅ PASSED.** ⚠️ This does **not** close Exit Gate 3, which is
open for a clinician and unaffected by anything in this phase.

---

**Cross-ref:** [architecture/03-rules-ownership-escalation.md](../architecture/03-rules-ownership-escalation.md) (Steps 8–9)
