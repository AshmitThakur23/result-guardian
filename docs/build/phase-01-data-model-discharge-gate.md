# Phase 1 — Data Model + Discharge Gate ★

**Goal:** a doctor **physically cannot** complete discharge while an investigation has no owner and no date.
**Duration:** 2–3 weeks · **Node:** A only · **Depends on NODE B?** No

> **This is the product. Spend the time.**

---

## 📍 STATUS SUMMARY — Phase 1

> ⚠️ **Do not start this phase until Exit Gate 0 passes.**

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 1.1 Core schema (Alembic revision 002) | A | 🔵 **6 of 11 tables done** | ✅ departments, users, patients, encounters, orders, discharge_contracts — migration `0002_core_schema`, applied and round-tripped on NODE A. ⬜ **remaining: discharge_contract_revisions, pending_cases, case_events, discharge_medications, discharge_overrides** |
| 1.2 Indexes | A | ⬜ not started |  |
| 1.3 Discharge readiness API | A | ⬜ not started |  |
| 1.4 Discharge gate UI | A | ⬜ not started |  |
| 1.5 Supporting screens | A | ⬜ not started |  |
| 1.6 Test corpus collection ★ calendar-gated | A | 🔴 **opened 2026-09-12** | 0 of 200+ collected. Manifest + blocker list in [`../test-corpus-manifest.md`](../test-corpus-manifest.md). **All 5 blockers need human action** |
| 1.7 Vendor conversations ★ calendar-gated | A | 🔴 **opened 2026-09-12** | Discovery questionnaire in [`../integration-spec.md`](../integration-spec.md), every answer still `— UNANSWERED —`. **Needs hospital IT to name the vendor** |
| 1.8 Tests | A | ⬜ not started |  |
| — OPD scope decision | A | ✅ **decided** | OUT of scope for v1, schema stays ready ([ADR 0003](../adr/0003-opd-out-of-scope-v1.md)) |
| **Exit Gate 1** | A | ⬜ **not started** | |

---

## 1.1 Core schema (Alembic revision 002)

- [x] **`departments`** — id, code, name, unit_head_user_id, active
- [x] **`users`** — id, employee_code (unique), full_name, email, phone_e164, role, department_id, password_hash, is_active, last_login_at, must_change_password
  - roles: `doctor`, `unit_head`, `lab_tech`, `admin`, `auditor`
  - Create the table and the role column **now** even though auth lands in Phase 5. Retrofitting ownership onto rows that have no user is painful.
- [x] **`patients`** — id, mrn (unique, indexed), name, dob, sex, phone_primary_e164, phone_alt_e164, preferred_language (`en|hi|pa`), address_line, city, pincode, phone_verified_at, sms_consent_at, sms_consent_basis
  - `phone_verified_at` is **not decoration**. The Phase 4 T+24h patient rung silently fails without it.
  - Capture consent at admission, not at notification time.
- [x] **`encounters`** — id, patient_id, encounter_no, type (`ipd|opd|emergency|daycare`), admitted_at, discharged_at, ward, bed, attending_doctor_id, department_id, status (`active|discharged|lama|transferred|deceased`)
  - `type` includes `opd` deliberately — see the Scope note below.
  - `lama`, `transferred`, `deceased` drive the Phase 4.6 suppression rules.
- [x] **`orders`** — id, encounter_id, patient_id, external_order_id (nullable, indexed), test_code, test_name, category (`lab|radiology|pathology|micro`), ordered_by_user_id, ordered_at, sample_collected_at, expected_tat_hours, status
  - status: `ordered | collected | in_lab | preliminary | final | cancelled | rejected`
- [x] **`discharge_contracts`** — id, encounter_id, order_id, responsible_doctor_id, expected_by (timestamptz), created_by, created_at, note
  - `UNIQUE (order_id)` — one contract per pending order
  - This row is **immutable**. Changes go to `discharge_contract_revisions`.
- [ ] **`discharge_contract_revisions`** — id, contract_id, field, old_value, new_value, changed_by, reason, changed_at
- [ ] **`pending_cases`** — id, order_id (unique), encounter_id, patient_id, contract_id, current_owner_id, state, severity, opened_at, result_received_at, flagged_at, acknowledged_at, closed_at, closure_reason, closure_note, reopened_count
  - state: `awaiting_result | result_received | classified | flagged | acknowledged | closed | reopened`
  - severity: `null | normal | follow_up | critical`
- [ ] **`case_events`** — id, case_id, event_type, actor_user_id (nullable for system), payload JSONB, occurred_at
  - Append-only. No UPDATE, no DELETE — **enforce with a trigger, not convention.**
- [ ] **`discharge_medications`** — id, encounter_id, drug_name, drug_code, atc_code, dose, route, frequency, duration_days, is_antibiotic
  - Needed by Rule B in Phase 3. **Capture it now or Rule B has nothing to compare against.**
- [ ] **`discharge_overrides`** — id, encounter_id, order_id, reason_code, reason_text, overridden_by, approved_by (nullable), created_at

## 1.2 Indexes

- [ ] `orders(encounter_id, status)`
- [ ] `orders(external_order_id) WHERE external_order_id IS NOT NULL`
- [ ] `pending_cases(state, severity, opened_at)`
- [ ] `pending_cases(current_owner_id) WHERE state IN ('flagged','result_received')`
- [ ] `patients USING gin (name gin_trgm_ops)` for fuzzy search
- [ ] `case_events(case_id, occurred_at)`

## 1.3 Discharge readiness API

- [ ] `GET /api/encounters/{id}/discharge-readiness`
  - returns `{can_discharge: bool, blocking_orders: [...], already_contracted: [...]}`
  - blocking = orders with status **NOT IN** (`final`, `cancelled`, `rejected`)
  - each blocking order carries: test_name, ordered_at, current status, expected TAT, suggested `expected_by` (ordered_at + TAT)
- [ ] `POST /api/encounters/{id}/discharge-contracts` — bulk create, atomic, all-or-nothing
  - validate: responsible doctor is active; `expected_by` is future; `expected_by` ≤ 30 days
- [ ] `POST /api/encounters/{id}/discharge` — final action
  - re-runs readiness check server-side (**never trust the client**)
  - returns **409** with the blocking list if unsatisfied
  - inside one transaction: set `encounters.discharged_at`, status `discharged`, create `pending_cases`, enqueue SLA timers, write `case_events`
- [ ] `POST /api/encounters/{id}/discharge-overrides` — emergency path
  - requires `reason_code` from a fixed list: `patient_lama`, `transfer_out`, `deceased`, `system_outage`, `clinical_urgency`
  - requires free-text reason ≥ 20 chars
  - still creates a `pending_case`, flagged to unit head immediately
  - appears on an "overrides" audit report

## 1.4 Discharge gate UI

- [ ] Route `/encounters/:id/discharge`
- [ ] **Step 1:** summary of pending investigations, red banner, **no "skip" button anywhere on screen**
- [ ] **Step 2:** per-order row with two required fields
  - Responsible doctor: searchable select, defaults to attending doctor, shows availability badge
  - Expected-by: date-time picker, default = `ordered_at + TAT`, min = `now+1h`
  - "Apply to all" convenience button
- [ ] **Step 3:** review screen in plain language: *"Dr. X will review Urine Culture by 14 Mar, 6:00 PM"*
- [ ] Confirm → success screen with contract reference numbers
- [ ] Override flow behind a separate red button with a confirmation modal and typed reason
- [ ] Form state survives refresh (persist draft in `sessionStorage`)
- [ ] **Full keyboard operation** — doctors will not reach for a mouse

## 1.5 Supporting screens

- [ ] Patient search (MRN, name, phone)
- [ ] Encounter detail: orders list with status, discharge medications, contracts
- [ ] Manual order creation (until HIS integration exists)
- [ ] Discharge medication entry form — required for Rule B later
- [ ] Seed script: 20 patients, 60 orders in varied states, 10 doctors

## 1.6 Test corpus collection starts NOW ★

> Not code. **Start the paperwork this week.**

- [ ] Target: **200+ real reports**, de-identified, covering every lab the hospital uses
- [ ] Permissions take months. **Phase 6 cannot start without this** and you will not be able to compress the wait later.
- [ ] Categorise as you collect: native/scanned, lab/radiology/path/micro, single/multi-page
- [ ] Store outside git, manifest in git

## 1.7 Vendor conversations start NOW ★

> Phase 9 is gated by hospital IT and the HIS vendor, **not by your code**.

- [ ] Open the conversation in week one of Phase 1
- [ ] Ask: which HIS, which version, which interfaces, who the contact is

## 1.8 Tests

- [ ] Unit: readiness logic for every order status combination
- [ ] Integration: discharge blocked → contracts created → discharge succeeds
- [ ] Integration: client bypasses UI and POSTs discharge directly → **409**
- [ ] Integration: two concurrent discharge requests → **exactly one** succeeds (row lock / unique constraint)
- [ ] Integration: contract creation fails halfway → **nothing persisted**
- [ ] Integration: override path creates a case flagged to unit head
- [ ] E2E Playwright: doctor completes the whole gate flow

---

## ⚠ Scope note — OPD (decision forced)

OPD pending results are the same failure mode at higher volume, with **no discharge event** to hang the gate on. Decide explicitly now:

- **in scope** (the trigger becomes "visit closed"), or
- **out of scope for v1** (documented, revisited after pilot)

> **Do not leave it undecided — it changes the encounter model.**

- [ ] OPD decision made and recorded in `PROGRESS.md`

---

## ✅ EXIT GATE 1

- [ ] Demo: admit patient → order 3 tests → 1 resulted, 2 pending → click discharge → **blocked** → assign owners and dates → discharge succeeds → 2 pending cases exist with timers queued
- [ ] Attempting discharge via raw API without contracts returns **409**
- [ ] All of it with **NODE B powered off**

---

**Cross-ref:** [architecture/01-patient-to-discharge-gate.md](../architecture/01-patient-to-discharge-gate.md)
