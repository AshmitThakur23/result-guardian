# Phase 4 — Ownership + Escalation ★

**Goal:** no flag can be silently ignored; **no doctor gets spammed into ignoring flags.**
**Duration:** 2–3 weeks · **Node:** A only · **Depends on NODE B?** No

---

## 4.1 Availability model

- [ ] **`duty_roster`** — id, user_id, department_id, shift_start, shift_end, role_on_duty (`primary|backup|consultant`)
  - **Decide who maintains this.** A roster nobody updates is worse than no roster, because the system will confidently notify someone who left.
  - Options: HR sync (Phase 9.4), unit-head weekly entry, or admin upload. **Pick one and write it in the SOP.**
- [ ] **`user_absences`** — id, user_id, absence_type (`leave|resigned|suspended|training`), starts_at, ends_at (null = indefinite), delegate_user_id
- [ ] **`escalation_chain`** — id, department_id, level, target_type (`owner|roster_on_duty|unit_head|admin|patient`), delay_minutes, channels[], active
  - One row per rung, per department. **This is what makes escalation configurable per hospital.**
- [ ] Roster-maintenance owner decided and recorded in `PROGRESS.md`

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

- [ ] Each hop writes a `case_event` with the reason
- [ ] `POST /api/cases/{id}/reassign` — manual reassign with **mandatory reason**
  - **Reassignment does not reset the escalation clock**, otherwise it becomes a dodge.

## 4.3 Notification layer

- [ ] **`notifications`** — id, case_id, user_id (nullable), patient_id (nullable), channel (`in_app|email|sms|whatsapp`), template_key, locale, payload JSONB, status (`queued|sent|delivered|failed|suppressed`), provider_msg_id, attempts, sent_at, error
- [ ] Adapter interface: `send(channel, recipient, template, context) -> ProviderResult`
- [ ] Implementations: `InAppAdapter` (DB row + polling), `SmtpAdapter`, `SmsAdapter` (MSG91 / Gupshup — **DLT-registered templates required in India**), `NullAdapter` (tests)
- [ ] Provider failure → retry **3×** with backoff → mark `failed` → alert admin dashboard
- [ ] Delivery receipt webhook endpoint for SMS provider
- [ ] Template store: Jinja2 files per `template_key` × locale (`en`, `hi`, `pa`)

## 4.4 Escalation engine

| Rung | Default delay | Critical delay | Target | Channels |
|---|---|---|---|---|
| 0 | immediate | immediate | owner | in_app + email |
| 1 | +4h | +1h | owner | in_app + email + SMS |
| 2 | +12h | +4h | unit head | all |
| 3 | +24h | +8h | **patient** | SMS |
| 4 | +48h | +48h | admin / medical superintendent | all |

- [ ] Delays read from `escalation_chain`, differentiated by severity
- [ ] Each rung fires a timer; **acknowledgement cancels all remaining rungs in one transaction**
- [ ] `case_events` records every rung with target and channel

## 4.5 Alert fatigue controls ★

> **The #1 killer of clinical alert systems.** Build these in the same sprint as the ladder, not later.

- [ ] **Quiet hours** (default **22:00–07:00 IST**) — FOLLOW_UP batches to the next window; **CRITICAL always sends immediately**
- [ ] **Digest** — one email per doctor per morning listing all open FOLLOW_UP flags, not one each
- [ ] **Dedup / grouping** — multiple analytes in one report = **one** notification, not twelve. Multiple pending tests for one patient group into one message.
- [ ] **Rate cap** — max N SMS per doctor per hour, overflow rolls into digest
- [ ] **Metric on the admin dashboard: flag rate per 100 discharges.** If this exceeds **~15%** you must retune thresholds before the pilot expands. **Put this number on the wall.**

## 4.6 Patient notification — handle carefully

- [ ] **Never include the result in an SMS.** Template: *"Namaste. A test report from your recent visit to [Hospital] needs discussion. Please call [number] or visit OPD. — [Hospital]"*
- [ ] Language from `patients.preferred_language`, fallback English
- [ ] **Only send if `phone_verified_at` is set**; otherwise escalate to unit head instead
- [ ] **Suppress entirely if encounter status is `deceased`.** Also handle `lama` and `transferred` — for `transferred`, notify the receiving facility contact instead if known.
- [ ] Log consent basis for the message (`sms_consent_basis`)
- [ ] Inbound: **`patient_contacts`** table so front desk can mark "patient called back"

## 4.7 Tests

- [ ] Full ladder on a **compressed clock** (delays in seconds) → 4 rungs fire in order
- [ ] Acknowledge at rung 1 → rungs 2–4 **never** fire
- [ ] Owner on leave → delegate notified → event logged
- [ ] Quiet hours: FOLLOW_UP at 23:00 is deferred, CRITICAL at 23:00 is **not**
- [ ] SMS provider returns 500 → retried → failure surfaced, **ladder continues**
- [ ] Patient deceased → patient rung suppressed, unit head notified instead
- [ ] Unverified phone → patient rung skipped, escalates to unit head

---

## ✅ EXIT GATE 4

- [ ] A critical flag left untouched walks **all four rungs** and produces a patient SMS (to a test number), with a complete `case_events` trail
- [ ] Acknowledging at **any** rung stops everything

---

**Cross-ref:** [architecture/03-rules-ownership-escalation.md](../architecture/03-rules-ownership-escalation.md) (Steps 8–9)
