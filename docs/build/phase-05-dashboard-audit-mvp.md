# Phase 5 — Dashboard, Closure, Audit → MVP

**Goal:** shippable product. Pilot-ready.
**Duration:** 2–3 weeks · **Node:** A only · **Depends on NODE B?** No

---

## 📍 STATUS SUMMARY — Phase 5

> ⚠️ **Do not start this phase until Exit Gate 4 passes.**

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 5.1 Auth & authorisation | A | ⬜ not started |  |
| 5.2 Doctor dashboard | A | ⬜ not started |  |
| 5.3 Acknowledgement & closure | A | ⬜ not started |  |
| 5.4 Unit head & admin views | A | ⬜ not started |  |
| 5.5 Hash-chained audit log ★ | A | ⬜ not started |  |
| 5.6 Reports & metrics | A | ⬜ not started |  |
| 5.7 Hardening for pilot | A | ⬜ not started |  |
| **Exit Gate 5** | A | ⬜ **not started** | |

---

## 5.1 Auth & authorisation

- [ ] Login with `employee_code` + password (**Argon2id**)
- [ ] JWT access token (**15 min**) + refresh token (**12h**, rotated, stored hashed in `sessions`)
- [ ] Force password change on first login; password policy
- [ ] Account lockout after **5 failures for 15 min**
- [ ] RBAC dependency on **every** endpoint — `require_role("doctor","unit_head")`
- [ ] Row-level scoping: a doctor sees their department's cases; auditor sees all but **read-only**
- [ ] Break-glass access with mandatory reason, **logged loudly**
- [ ] Optional later: SAML/OIDC against hospital AD

## 5.2 Doctor dashboard

- [ ] Default view: my open flags, sorted **CRITICAL first, then oldest**
- [ ] Rows show: patient name + MRN, test, severity chip, age of flag, escalation rung, next escalation time (countdown)
- [ ] Filters: severity, department, date range, state
- [ ] System status pill in the header, driven by `/api/health`: `AI: connected` / `AI: offline — core tracking unaffected`
- [ ] **Case detail page:**
  - patient & encounter header
  - the result, rendered as a table (analytes), grid (sensitivities) or text (narrative)
  - abnormal values highlighted with the reference range
  - rule engine output **in plain language**: *"Amoxicillin-clavulanate (discharge medication) is Resistant for E. coli"*
  - full timeline from `case_events`
  - action bar: Acknowledge / Reassign / Add note
- [ ] Real-time-ish updates: TanStack Query polling every **30s** (skip websockets for v1)

## 5.3 Acknowledgement & closure

- [ ] `POST /api/cases/{id}/acknowledge` — requires `closure_reason` from enum:
  - `action_taken` (+ required note describing action)
  - `already_handled` (+ where/when)
  - `not_clinically_relevant` (+ justification)
  - `duplicate_report` (+ link to original case)
  - `patient_uncontactable` (+ attempts made)
- [ ] Transaction: set state `closed`, stop timers, cancel escalations, write audit, write `case_events`
- [ ] Reopen path: amended result or manual reopen with reason
- [ ] **Bulk acknowledge is not available for CRITICAL cases — deliberate friction**

## 5.4 Unit head & admin views

- [ ] Unit head: all department cases, overdue list, per-doctor acknowledgement times
- [ ] Admin: user management, roster, panic thresholds editor, escalation chain editor, keyword editor, notification provider health, **NODE B status and kill switch (`LLM_ENABLED`)**
- [ ] Overrides report — every discharge override with reason and who approved

## 5.5 Hash-chained audit log ★

- [ ] **`audit_log`** — id BIGSERIAL, seq, occurred_at, actor_user_id, actor_ip, action, entity_type, entity_id, before JSONB, after JSONB, prev_hash CHAR(64), row_hash CHAR(64)

```
row_hash = SHA256(seq || occurred_at || actor || action || entity ||
                  canonical_json(before) || canonical_json(after) || prev_hash)
```

- [ ] Canonical JSON: **sorted keys, no whitespace, UTC ISO-8601** — hashing must be reproducible
- [ ] DB rules: `REVOKE UPDATE, DELETE` from the app role; `BEFORE UPDATE/DELETE` trigger raises exception
- [ ] Writes happen **in the same transaction as the change** — never fire-and-forget
- [ ] Single writer to guarantee `seq` ordering: advisory lock, or a sequence + serialisable insert
- [ ] `GET /api/audit/verify?from=&to=` → recomputes the chain, returns the first break if any
- [ ] Nightly `pg_cron` job verifies the chain and publishes the head hash to a separate append-only file (a cheap notarisation)
- [ ] Audit viewer UI for the `auditor` role, exportable to CSV

## 5.6 Reports & metrics

- [ ] Turnaround: discharge → result → flag → acknowledgement (**p50/p90**)
- [ ] Open cases by age bucket
- [ ] Escalation rate by rung, by department
- [ ] Closure reason distribution — **a spike in `not_clinically_relevant` means a threshold problem**
- [ ] Flag rate per 100 discharges (the alert fatigue metric)
- [ ] Patient notifications sent / patients who called back
- [ ] Override count
- [ ] Lab flags open and aged
- [ ] Export to CSV; monthly PDF for NABH review (**AAC.12, AAC.6.g**)

## 5.7 Hardening for pilot

- [ ] Pagination everywhere (cursor-based on UUIDv7)
- [ ] Rate limiting on auth endpoints
- [ ] Input size limits, request timeout
- [ ] Empty states, loading skeletons, error toasts in the UI
- [ ] `docs/runbook.md`: restart procedure, how to check worker health, how to manually fire a stuck timer, how to reach NODE B, **what to do when NODE B is down**

---

## 🏁 MILESTONE — MVP COMPLETE

> **Zero AI. Single node. Fully functional. Pilot in one department. Everything after this is acceleration.**

---

## ✅ EXIT GATE 5

- [ ] **Two weeks of shadow running** in one ward with manual result entry
- [ ] **Zero missed cases**
- [ ] Audit chain verifies clean
- [ ] Clinician sign-off on flag quality
- [ ] **NODE B has not been switched on once**

---

**Cross-ref:** [architecture/04-rag-explain-and-closure.md](../architecture/04-rag-explain-and-closure.md) (Step 11)
