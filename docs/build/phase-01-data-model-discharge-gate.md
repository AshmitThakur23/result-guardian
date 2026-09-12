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
| 1.1 Core schema (Alembic revisions 002 + 003) | A | ✅ **done — all 11 tables** | `0002_core_schema` (departments, users, patients, encounters, orders, discharge_contracts) + `0003_core_schema_remaining` (discharge_contract_revisions, pending_cases, case_events, discharge_medications, discharge_overrides). Applied and round-tripped on NODE A; `case_events` append-only **enforced by trigger** and proven to reject UPDATE and DELETE; autogenerate drift = 0 |
| 1.2 Indexes | A | ✅ **done** | `0004_phase_1_2_indexes`. All 6 verified against PostgreSQL's catalogue, not just metadata: 2 composites, 2 partials, 1 GIN `gin_trgm_ops`, and `case_events(case_id, occurred_at)` reused from 0003 rather than duplicated. Two plain indexes replaced by their partial forms |
| 1.3 Discharge readiness API | A | ✅ **done — all 4 endpoints** | readiness GET · POST discharge-contracts · POST discharge · POST discharge-overrides. Server-side rechecks under `FOR UPDATE`, one transaction each, all verified live through Caddy |
| 1.4 Discharge gate UI | A | ✅ **done** | React 18 + TS + Vite + Tailwind in [`web/`](../../web). Three-step gate at `/encounters/:id/discharge`, **no skip control anywhere**, sessionStorage draft, full keyboard operation. **59 tests pass** (37 gate + 11 draft + 11 datetime) and `npm run build` is green; bundle served live through Caddy. Availability badge 🚫 **deferred to Phase 4.1** — `duty_roster`/`user_absences` do not exist, see deviations below |
| 1.5 Supporting screens | A | ✅ **done** | Patient search (MRN/name/phone, one box, Phase 1.2 trigram index) · encounter detail (orders + contracts + medications in one read) · manual order creation **under the same encounter row lock the discharge takes** · discharge medication entry · seed script (20 patients, 60 orders in varied states, 10 doctors, deterministic). **294 backend + 99 frontend tests pass**, coverage 87%, **autogenerate drift = 0 — no migration needed** |
| 1.6 Test corpus collection ★ calendar-gated | A | 🔴 **opened 2026-09-12** | 0 of 200+ collected. Manifest + blocker list in [`../test-corpus-manifest.md`](../test-corpus-manifest.md). **All 5 blockers need human action** |
| 1.7 Vendor conversations ★ calendar-gated | A | 🔴 **opened 2026-09-12** | Discovery questionnaire in [`../integration-spec.md`](../integration-spec.md), every answer still `— UNANSWERED —`. **Needs hospital IT to name the vendor** |
| 1.8 Tests | A | 🔵 **6 of 7 done** | All five integration tests and the unit-level status matrix pass against real PostgreSQL. 🟡 **E2E Playwright written, never run** — [`web/e2e/discharge-gate.spec.ts`](../../web/e2e/discharge-gate.spec.ts) + live-DB seeder exist and the seeder is verified working, but Chromium build 1243 is not downloaded (install declined 2026-09-12) |
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
- [x] **`discharge_contract_revisions`** — id, contract_id, field, old_value, new_value, changed_by, reason, changed_at
- [x] **`pending_cases`** — id, order_id (unique), encounter_id, patient_id, contract_id, current_owner_id, state, severity, opened_at, result_received_at, flagged_at, acknowledged_at, closed_at, closure_reason, closure_note, reopened_count
  - state: `awaiting_result | result_received | classified | flagged | acknowledged | closed | reopened`
  - severity: `null | normal | follow_up | critical`
- [x] **`case_events`** — id, case_id, event_type, actor_user_id (nullable for system), payload JSONB, occurred_at
  - Append-only. No UPDATE, no DELETE — **enforce with a trigger, not convention.**
- [x] **`discharge_medications`** — id, encounter_id, drug_name, drug_code, atc_code, dose, route, frequency, duration_days, is_antibiotic
  - Needed by Rule B in Phase 3. **Capture it now or Rule B has nothing to compare against.**
- [x] **`discharge_overrides`** — id, encounter_id, order_id, reason_code, reason_text, overridden_by, approved_by (nullable), created_at

## 1.2 Indexes

- [x] `orders(encounter_id, status)`
- [x] `orders(external_order_id) WHERE external_order_id IS NOT NULL`
- [x] `pending_cases(state, severity, opened_at)`
- [x] `pending_cases(current_owner_id) WHERE state IN ('flagged','result_received')`
- [x] `patients USING gin (name gin_trgm_ops)` for fuzzy search
- [x] `case_events(case_id, occurred_at)`

## 1.3 Discharge readiness API

- [x] `GET /api/encounters/{id}/discharge-readiness`
  - returns `{can_discharge: bool, blocking_orders: [...], already_contracted: [...]}`
  - blocking = orders with status **NOT IN** (`final`, `cancelled`, `rejected`)
  - each blocking order carries: test_name, ordered_at, current status, expected TAT, suggested `expected_by` (ordered_at + TAT)
- [x] `POST /api/encounters/{id}/discharge-contracts` — bulk create, atomic, all-or-nothing
  - validate: responsible doctor is active; `expected_by` is future; `expected_by` ≤ 30 days
- [x] `POST /api/encounters/{id}/discharge` — final action
  - re-runs readiness check server-side (**never trust the client**)
  - returns **409** with the blocking list if unsatisfied
  - inside one transaction: set `encounters.discharged_at`, status `discharged`, create `pending_cases`, enqueue SLA timers, write `case_events`
- [x] `POST /api/encounters/{id}/discharge-overrides` — emergency path
  - requires `reason_code` from a fixed list: `patient_lama`, `transfer_out`, `deceased`, `system_outage`, `clinical_urgency`
  - requires free-text reason ≥ 20 chars
  - still creates a `pending_case`, flagged to unit head immediately
  - appears on an "overrides" audit report

## 1.4 Discharge gate UI

- [x] Route `/encounters/:id/discharge` — [`web/src/pages/DischargeGate.tsx`](../../web/src/pages/DischargeGate.tsx). A malformed id is refused client-side before any request is made
- [x] **Step 1:** summary of pending investigations, red banner, **no "skip" button anywhere on screen** — [`Step1Pending.tsx`](../../web/src/pages/steps/Step1Pending.tsx). A test walks every button and link on the page and fails on `skip|dismiss|ignore|discharge anyway|not required|remind me later|mark as done`
- [x] **Step 2:** per-order row with two required fields — [`Step2Assign.tsx`](../../web/src/pages/steps/Step2Assign.tsx)
  - [x] Responsible doctor: searchable select, defaults to attending doctor — ARIA 1.2 combobox, [`DoctorSelect.tsx`](../../web/src/components/DoctorSelect.tsx)
  - [x] Expected-by: date-time picker, default = `ordered_at + TAT`, min = `now+1h`, max = 30 days (mirrors `MAX_CONTRACT_HORIZON_DAYS`)
  - [x] "Apply to all" convenience button
  - 🚫 **Availability badge deferred to Phase 4.1** — it needs `duty_roster` / `user_absences`, which do not exist. The field shows no availability at all rather than implying `is_active` means "on duty"
- [x] **Step 3:** review screen in plain language: *"Dr Asha Menon will review Urine Culture by 14 Mar 2026, 6:00 PM"* — [`Step3Review.tsx`](../../web/src/pages/steps/Step3Review.tsx). A doctor whose name cannot be resolved reads as "The assigned doctor", never as a raw uuid
- [x] Confirm → success screen with contract reference numbers — [`SuccessScreen.tsx`](../../web/src/pages/steps/SuccessScreen.tsx). The reference **is the `contract_id`**: `discharge_contracts` has no human-readable reference column and inventing one would print an id nothing in the system can look up
- [x] Override flow behind a separate red button with a confirmation modal and typed reason — [`OverrideDialog.tsx`](../../web/src/components/OverrideDialog.tsx). Confirm stays disabled until reason code + 20 trimmed characters + an overriding doctor are all present
- [x] Form state survives refresh (persist draft in `sessionStorage`) — [`draft.ts`](../../web/src/lib/draft.ts). Zod-validated on read; a corrupt, version-stale or foreign-encounter draft is **deleted, not half-read**
- [x] **Full keyboard operation** — doctors will not reach for a mouse. Arrow/Enter/Escape/Tab on the combobox, focus ring never suppressed. ⚠️ Proven in jsdom; **the real-browser keyboard path is part of the 🟡 E2E spec that has not run**

## 1.5 Supporting screens

- [x] **Patient search (MRN, name, phone)** — `GET /api/patients?q=`, one box for all three. Ranked so an unambiguous identifier wins: exact MRN → MRN prefix → phone → fuzzy name. Name matching uses the Phase 1.2 GIN `gin_trgm_ops` index (a clerk typing "Sunta Rao" finds "Sunita Rao", so they do not create a duplicate record). Phone compares digits to digits, since the column is E.164 and the clerk types what is on the file. **LIKE wildcards in `q` are escaped** — unescaped, a single `%` is a patient-index dump through a search box. UI: [`PatientSearch.tsx`](../../web/src/pages/PatientSearch.tsx), debounced, never fires on an empty box
- [x] **Encounter detail: orders list with status, discharge medications, contracts** — `GET /api/encounters/{id}/detail`, all three in **one transaction** so they cannot disagree with each other. Reports the gate's answer by calling `get_discharge_readiness`, never by re-implementing the blocking rule. UI: [`EncounterDetail.tsx`](../../web/src/pages/EncounterDetail.tsx). The Phase 1.4 gate's own `GET /encounters/{id}` is **untouched**
- [x] **Manual order creation (until HIS integration exists)** — `POST /api/encounters/{id}/orders`, [`services/orders.py`](../../api/app/services/orders.py). **Takes `SELECT … FOR UPDATE` on the encounter row — the same lock the discharge action takes.** That is the whole safety argument; see the race section below. `patient_id` is denormalised from the encounter and never accepted from the client (Phase 7.5 matches on it — a client-supplied value would be a wrong-patient hazard by construction)
- [x] **Discharge medication entry form — required for Rule B later** — `POST/GET /api/encounters/{id}/discharge-medications`. Capture only: no interaction checking, no dose validation, no formulary, no AI. Only `drug_name` is required, matching the table. **Accepted after discharge on purpose** — a summary is often typed up once the patient has left, and a medication row changes nothing the gate reads
- [x] **Seed script: 20 patients, 60 orders in varied states, 10 doctors** — [`api/scripts/seed_dev.py`](../../api/scripts/seed_dev.py). Deterministic (ids are stable **UUIDv7** derived from a fixed namespace, so re-running upserts rather than duplicating), obviously synthetic (`(SEED)` suffix, `SEED-` MRNs, 555-range phone numbers), and refuses to run when `RG_ENV=prod`. All 7 order statuses represented; the department has a unit head so the override path works on seeded data

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

- [x] Unit: readiness logic for every order status combination — `test_discharge_readiness.py`, parametrised over every value in `ORDER_STATUSES`
- [x] Integration: discharge blocked → contracts created → discharge succeeds — `test_readiness_reflects_the_new_contract`, and end-to-end through Caddy
- [x] Integration: client bypasses UI and POSTs discharge directly → **409** — `test_uncontracted_order_blocks_and_changes_nothing`, `test_body_cannot_assert_readiness`
- [x] Integration: two concurrent discharge requests → **exactly one** succeeds — `test_concurrent_discharges_produce_exactly_one`, two independent connections
- [x] Integration: contract creation fails halfway → **nothing persisted** — `test_one_bad_entry_creates_nothing` and the UNIQUE(order_id) IntegrityError path
- [x] Integration: override path creates a case flagged to unit head — `test_override_still_tracks_the_investigation`
- [ ] E2E Playwright: doctor completes the whole gate flow — 🟡 **written, never run.** [`web/e2e/discharge-gate.spec.ts`](../../web/e2e/discharge-gate.spec.ts): 10 tests against the real stack (Caddy → FastAPI → Postgres), asserting `discharge_contracts`, `pending_cases`, `discharge_overrides` and `encounters.status` **out of the database**, not off the screen. The seeder [`e2e/seed.mjs`](../../web/e2e/seed.mjs) is ✅ verified working — it created a live gated encounter and the readiness endpoint returned it correctly through Caddy. **Blocked only on `npx playwright install chromium`** (build 1243; install declined 2026-09-12)

---

## 🔒 The order-creation race, and how it is closed

Phase 1.5 added the first thing in the product that can add work to an
encounter the gate has already looked at. Without a lock, this interleaving is
reachable:

```
T1 (discharge)     lock encounter, readiness = clear
T2 (create order)                                     INSERT order, COMMIT
T1                 UPDATE status = discharged, COMMIT
```

— leaving an encounter **discharged with an outstanding, uncontracted order
nobody owns**, without the gate ever being wrong.

**The fix lives in order creation, not in the discharge algorithm.**
`create_manual_order` takes `SELECT … FOR UPDATE` on the same encounter row
the discharge action locks. Postgres then serialises the two, and both
orderings are safe:

| Winner | What happens |
|---|---|
| Discharge | Order creation wakes to `status = 'discharged'` → **409**, nothing written |
| Order | Discharge wakes and re-derives readiness *inside its own lock*, sees the new uncontracted order → **409 blocked** |

Nothing else was needed: no change to `discharge_action`, no advisory lock, no
SERIALIZABLE isolation, no retry loop.

**The test is proven to catch it.** `test_order_creation_cannot_race_a_discharge`
uses two independent connections, real commits and real locks, and asserts the
invariant out of the database afterwards. With `.with_for_update()` temporarily
removed it failed **6 out of 6 runs** with `['discharged', 'order_created']` —
the unsafe outcome, reproduced. With the lock restored it passed 6 of 6.

---

## Deviations from the build plan, and why

| § | Plan said | Built instead | Why |
|---|---|---|---|
| 1.4 Step 2 | Responsible-doctor select "shows availability badge" | **No availability shown at all** | Availability comes from `duty_roster` and `user_absences`, which are **Phase 4.1** and do not exist. The only liveness signal the schema carries is `users.is_active`, and inactive users are already filtered out server-side. Rendering `is_active` as an availability badge would tell a doctor "on duty" about someone who is on leave — worse than showing nothing. Restore the badge in 4.1, where the roster makes it true |
| 1.4 success screen | "contract reference numbers" | **The `contract_id` UUID**, copyable | `discharge_contracts` has no human-readable reference column. A prettified short code would print an identifier that cannot be looked up in the database, the API or a support call |
| 1.4 override dialog | Typed reason | Typed reason **plus an "overriding doctor" field** | `DischargeOverrideRequest.overridden_by` is required and authentication is **Phase 5.1**. Until a session exists, the identity has to come from somewhere; the field carries a note saying it disappears once auth lands |
| 1.5 medications | Entry form | **Accepted after discharge, not only before** | A discharge summary is often typed up once the patient has left. A medication row neither blocks nor unblocks the gate, so refusing it would starve Phase 3's Rule B for exactly the busiest encounters |
| 1.5 seed | "20 patients, 60 orders, 10 doctors" | Also **1 department with a unit head**, and 5 discharge medications | Without a unit head the Phase 1.3 override path 409s on seeded data with "no unit head to flag to", making the emergency path unusable in a demo |
| 1.5 (infrastructure) | — | **`scripts/` and `tests/` stripped from the runtime Docker image** | Adding `scripts/seed_dev.py` meant a script that writes twenty fake patients was being copied into the production image by `COPY . /app`. It refuses to run at `RG_ENV=prod`, but not being on a hospital server at all is the safer position. The `dev` stage copies both back |
| 1.4 (addendum to 1.3) | — | **Two new read-only endpoints**: `GET /api/users` and `GET /api/encounters/{id}` | The gate cannot be built without a way to search for a doctor and a way to read the encounter's `attending_doctor_id`. Both are plain reads — no new tables, no migration, no write path. 13 tests, all passing. Approved as the minimal unblock rather than pulling Phase 5.4 admin work forward |

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
