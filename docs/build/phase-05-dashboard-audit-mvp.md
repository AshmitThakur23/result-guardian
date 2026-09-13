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
| 5.1 Auth & authorisation | A | ✅ done | Argon2id, 15min/12h rotated tokens, lockout, RBAC on **every** endpoint, row scoping, break-glass |
| 5.2 Doctor dashboard | A | ✅ done | CRITICAL-first worklist, cursor pagination, case detail, plain-language rule output, 30s polling |
| 5.3 Acknowledgement & closure | A | ✅ done | 5 reasons + mandatory notes, reopen, bulk close refuses CRITICAL |
| 5.4 Unit head & admin views | A | ✅ done | Users, keywords, thresholds, chain, provider health, NODE B kill switch, overrides |
| 5.5 Hash-chained audit log ★ | A | ✅ done | Chain verifies; tampering detected. ⚠️ REVOKE inert under a superuser — see Deviations |
| 5.6 Reports & metrics | A | ✅ done | All nine metrics, CSV, NABH PDF with no third-party PDF library |
| 5.7 Hardening for pilot | A | ✅ done | Cursor pagination, configurable auth rate limit, input caps, UI states, `docs/runbook.md` |
| **Exit Gate 5** | A | 🔴 **blocked** | Needs two weeks of ward shadow-running and a clinician sign-off. Neither can be produced by code. |

---

## 5.1 Auth & authorisation

- [x] Login with `employee_code` + password (**Argon2id**)
- [x] JWT access token (**15 min**) + refresh token (**12h**, rotated, stored hashed in `sessions`)
- [x] Force password change on first login; password policy
- [x] Account lockout after **5 failures for 15 min**
- [x] RBAC dependency on **every** endpoint — `require_role("doctor","unit_head")`
- [x] Row-level scoping: a doctor sees their department's cases; auditor sees all but **read-only**
- [x] Break-glass access with mandatory reason, **logged loudly**
- [ ] Optional later: SAML/OIDC against hospital AD — 🚫 out of scope for the MVP

## 5.2 Doctor dashboard

- [x] Default view: my open flags, sorted **CRITICAL first, then oldest**
- [x] Rows show: patient name + MRN, test, severity chip, age of flag, escalation rung, next escalation time (countdown)
- [x] Filters: severity, department, date range, state
- [x] System status pill in the header, driven by `/api/health`: `AI: connected` / `AI: offline — core tracking unaffected`
- [x] **Case detail page:**
  - patient & encounter header
  - the result, rendered as a table (analytes), grid (sensitivities) or text (narrative)
  - abnormal values highlighted with the reference range
  - rule engine output **in plain language**: *"Amoxicillin-clavulanate (discharge medication) is Resistant for E. coli"*
  - full timeline from `case_events`
  - action bar: Acknowledge / Reassign / Add note
- [x] Real-time-ish updates: TanStack Query polling every **30s** (skip websockets for v1)

## 5.3 Acknowledgement & closure

- [x] `POST /api/cases/{id}/acknowledge` — requires `closure_reason` from enum:
  - `action_taken` (+ required note describing action)
  - `already_handled` (+ where/when)
  - `not_clinically_relevant` (+ justification)
  - `duplicate_report` (+ link to original case)
  - `patient_uncontactable` (+ attempts made)
- [x] Transaction: set state `closed`, stop timers, cancel escalations, write audit, write `case_events`
- [x] Reopen path: amended result or manual reopen with reason
- [x] **Bulk acknowledge is not available for CRITICAL cases — deliberate friction**

## 5.4 Unit head & admin views

- [x] Unit head: all department cases, overdue list, per-doctor acknowledgement times
- [x] Admin: user management, roster, panic thresholds editor, escalation chain editor, keyword editor, notification provider health, **NODE B status and kill switch (`LLM_ENABLED`)**
- [x] Overrides report — every discharge override with reason and who approved

## 5.5 Hash-chained audit log ★

- [x] **`audit_log`** — id BIGSERIAL, seq, occurred_at, actor_user_id, actor_ip, action, entity_type, entity_id, before JSONB, after JSONB, prev_hash CHAR(64), row_hash CHAR(64)

```
row_hash = SHA256(seq || occurred_at || actor || action || entity ||
                  canonical_json(before) || canonical_json(after) || prev_hash)
```

- [x] Canonical JSON: **sorted keys, no whitespace, UTC ISO-8601** — hashing must be reproducible
- [x] DB rules: `REVOKE UPDATE, DELETE` from the app role; `BEFORE UPDATE/DELETE` trigger raises exception
- [x] Writes happen **in the same transaction as the change** — never fire-and-forget
- [x] Single writer to guarantee `seq` ordering: advisory lock, or a sequence + serialisable insert
- [x] `GET /api/audit/verify?from=&to=` → recomputes the chain, returns the first break if any
- [x] Nightly `pg_cron` job verifies the chain and publishes the head hash to a separate append-only file (a cheap notarisation)
- [x] Audit viewer UI for the `auditor` role, exportable to CSV

## 5.6 Reports & metrics

- [x] Turnaround: discharge → result → flag → acknowledgement (**p50/p90**)
- [x] Open cases by age bucket
- [x] Escalation rate by rung, by department
- [x] Closure reason distribution — **a spike in `not_clinically_relevant` means a threshold problem**
- [x] Flag rate per 100 discharges (the alert fatigue metric)
- [x] Patient notifications sent / patients who called back
- [x] Override count
- [x] Lab flags open and aged
- [x] Export to CSV; monthly PDF for NABH review (**AAC.12, AAC.6.g**)

## 5.7 Hardening for pilot

- [x] Pagination everywhere (cursor-based on UUIDv7)
- [x] Rate limiting on auth endpoints
- [x] Input size limits, request timeout
- [x] Empty states, loading skeletons, error toasts in the UI
- [x] `docs/runbook.md`: restart procedure, how to check worker health, how to manually fire a stuck timer, how to reach NODE B, **what to do when NODE B is down**

---

## 🏁 MILESTONE — MVP COMPLETE

> **Zero AI. Single node. Fully functional. Pilot in one department. Everything after this is acceleration.**

---

## 🔍 Phase 5 audit — findings and fixes

Every finding below was **reproduced before it was fixed** and has a
regression test that fails when the fix is reverted. Severity is judged by
what a patient loses, not by how hard it was to find.

| # | Sev | Finding | Fix | Test |
|---|---|---|---|---|
| **C1** | **P0** | **31 clinical endpoints were reachable with no credential** — every Phase 1–4 route. The dashboard was behind a login and the API was not. Anyone who could reach NODE A's port could list patients, read an encounter, or **discharge a patient**. A gated single-page app is not an access control. | `require_role` applied at `include_router`, so it covers routes added later too. | `test_phase_5_rbac_coverage.py` — sends a real unauthenticated request to **every** published endpoint and requires 401, behind a 5-entry written allow-list. ⚠️ An earlier draft inspected the dependency graph and **passed while the endpoints were still open**; the black-box version cannot be fooled that way. |
| **C2** | **P0** | **The audit chain could fork silently under concurrency.** `append()` took `rg_audit_next_seq()` and the head `prev_hash` in one statement; under READ COMMITTED the statement's snapshot predates the advisory lock the function acquires, so a waiting writer got a correct `seq` but a stale `prev_hash`. **No duplicate `seq` to give it away** — the tamper-evidence property was quietly broken. | Lock and head-read issued as two statements, so the second gets a post-lock snapshot. | `test_concurrent_appends_do_not_fork_the_chain` — four writers on independent connections. Reverting the fix fails it with `prev_hash does not match`. |
| **C3** | **P1** | **Phase 5's `closure_reason` CHECK broke Phase 2's `close_case`** — a THE ONE RULE violation, caught by the full regression rather than by reading. A caller passing an unconstrained reason now hit an `IntegrityError` *after* the timers had been cancelled. | `close_case` validates against `CLOSURE_REASONS` **before writing anything**, raising `UnknownClosureReasonError` with the accepted list. | `tests/test_case_lifecycle.py`, `tests/test_timer_lifecycle.py` — both were failing; both pass. |
| **C4** | **P1** | The **escalation-chain editor could not save**: the INSERT omitted `id`, and UUIDv7 is generated in Python with no server-side default. The admin screen 500'd on every write. | Supply `uuid7()`. Three other admin inserts were using `uuid.uuid4()`, violating the time-sortable-PK convention; all four now use `uuid7()`. | `test_the_escalation_chain_editor_upserts_a_rung`. |
| **C5** | **P2** | The **auth rate limit was hardcoded** at 20/minute — exactly what CLAUDE.md's *"configuration lives in tables, never in code"* guards against. It also silently throttled the E2E suite, which looked like an authorisation bug. | `RG_AUTH_RATE_LIMIT_PER_MINUTE`, read per request. | `test_login_is_rate_limited_at_the_configured_number` — sets its own limit and asserts the endpoint honours it, rather than asserting a constant. |
| **C6** | **P2** | **The delivery-receipt webhook was unauthenticated**, and applying router-level RBAC would have **broken a working Phase 4 SMS integration on upgrade**. Left open, it lets anyone on the LAN assert that a patient's message was delivered — falsifying the evidence 5.6's patient-contact metric and a NABH reviewer rely on. | Moved to its own router with an **optional** `RG_WEBHOOK_SECRET`: enforced when set, and when unset it accepts (preserving Phase 4) while logging a warning on **every** call. | `test_the_webhook_accepts_when_no_secret_is_configured`, `test_the_webhook_refuses_a_wrong_secret_when_one_is_configured`. |
| **C7** | **P3** | The CSV formula-injection guard only checked the whole cell, so the first draft of its test asserted the wrong thing — `before`/`after` are JSON and always start with `{`. The cells that matter are the plain-text ones. | Test rewritten against `break_glass_reason`, which a user can genuinely start with `=`. | `test_csv_export_neutralises_a_formula`. |
| **C9** | **P2** | **Two Phase 4 tests were flaky by wall clock** and had been passing by luck. `test_the_digest_lists_every_open_follow_up` and `test_the_roster_reminder_renders_and_sends` assert that a caseless notification *sends*; both are **correctly** suppressed inside quiet hours (22:00–07:00 IST), so both passed all afternoon and failed at 22:50 when the full regression happened to run late. A green suite that depends on the time of day is not a green suite. | The product behaviour is right and untouched. The tests now set a quiet-hours window on the far side of the clock **through `rule_config`** — which is possible only because *"configuration lives in tables, never in code"*. Nothing is mocked and no policy is bypassed. | `tests/test_phase_4_scheduled.py::_no_quiet_hours_right_now`. |
| **C8** | **P3** | The runbook named a function that **does not exist** (`rg_enqueue_due_timers`). Caught by running every command in it against the live stack rather than by writing it carefully. | `rg_sweep_overdue_sla_timers`. | Manual: every command in `docs/runbook.md` was executed. |

**Not fixed, recorded instead:** the `REVOKE` half of 5.5's DB rules is inert
because the application connects as a superuser. See Deviations row 1 — it is
asserted by a test that will fail the day that changes.

---

## 🧭 Deviations from the build plan, and why

| # | The plan says | What was built | Why |
|---|---|---|---|
| 1 | 5.5: *"DB rules: `REVOKE UPDATE, DELETE` from the app role; `BEFORE UPDATE/DELETE` trigger raises exception"* | The trigger works and is tested. **The REVOKE is applied but currently inert.** | `rg_app` is the bootstrap **superuser**, and PostgreSQL superusers bypass every privilege check. The grant itself is correct — the ACL reads `arxt` (INSERT, SELECT, REFERENCES, TRIGGER) with **no `w` and no `d`** — so the moment the application runs as a non-superuser role the layer becomes live. `test_revoke_is_ineffective_against_a_superuser` asserts the *situation*, so the day that changes, the test fails and somebody re-reads this row. A separate non-superuser application role is Phase 10 security hardening. Layers 1 (trigger) and 3 (hash chain) are unaffected and are the ones that resist a determined attacker anyway. |
| 2 | 5.5: nightly `pg_cron` job verifies the chain | **Split in two.** `rg_verify_audit_chain()` (SQL, pg_cron, 02:30 IST) checks `seq` continuity and `prev_hash` linkage; `verify_chain()` (Python) recomputes every row hash. | Reproducing `canonical_json` in SQL byte-for-byte is not possible through a JSONB round-trip — Postgres orders object keys by length-then-bytes and inserts whitespace, where canonical JSON sorts lexicographically with none. A second implementation that is *nearly* right would report tampering on every honest row and be switched off within a week. The SQL half keeps running when the API is stopped, which is when someone would try; the Python half catches content changes. Neither alone is sufficient. |
| 3 | 5.6: *"monthly PDF for NABH review"* | A ~200-line PDF writer in `app/services/pdf.py`. No PDF library. | The stack is locked and the requirement is a monthly table of numbers. ReportLab/WeasyPrint bring 5–40 MB of dependency and a new supply-chain surface onto the machine that holds patient data. The cost is stated in the module: WinAnsi only, no embedded fonts, no wrapping cleverness. |
| 4 | 5.1: *"RBAC dependency on **every** endpoint"* | Applied at `include_router` for Phases 1–4 rather than per route. | The audit found **31 clinical endpoints reachable with no credential** — every Phase 1–4 route. Applying it at the router covers routes added later too, which is what stops it regressing. `test_phase_5_rbac_coverage.py` asserts the property black-box against the live route table. |
| 5 | — | `/api/notifications/delivery-receipt` stays outside RBAC, with an optional `RG_WEBHOOK_SECRET`. | An SMS provider callback has no user and cannot hold a token. Making the secret *mandatory* would break a working Phase 4 integration on upgrade, which THE ONE RULE forbids; so it is optional, and its absence logs a warning on **every** call. Configure it before a pilot. |
| 6 | 5.7: *"Rate limiting on auth endpoints"* | `RG_AUTH_RATE_LIMIT_PER_MINUTE`, default 20, in-process. | The number was hardcoded first, which is the thing CLAUDE.md's *"configuration lives in tables, never in code"* guards against. It is a deployment knob rather than a clinical threshold, so it lives with the other `RG_` settings. In-process and not Redis-backed: the locked stack has no Redis, and NODE A runs a single API container. Both limitations are written in `app/services/rate_limit.py`. |
| 7 | 5.3 supersedes Phase 4's acknowledgement | `POST /api/cases/{id}/close` is the Phase 5 endpoint; Phase 4's `/acknowledge` is unchanged. | Phase 4's guarantee — *"acknowledging at any rung stops everything"* — is re-asserted in `test_phase_5_closure.py` against the new path, so the later phase demonstrably does not weaken the earlier one. |

---

## ✅ EXIT GATE 5

- [ ] **Two weeks of shadow running** in one ward with manual result entry
- [ ] **Zero missed cases**
- [x] Audit chain verifies clean — `GET /api/audit/verify` returns `intact: true` after real application traffic, asserted end-to-end in `web/e2e/phase5-dashboard.spec.ts`
- [ ] Clinician sign-off on flag quality
- [x] **NODE B has not been switched on once** — every Phase 5 test and the whole E2E suite run with NODE B unreachable

> 🔴 **This gate is OPEN and cannot be closed by writing code.**
>
> Three of its five clauses need a ward, two weeks and a clinician. *"Zero
> missed cases"* is a claim about a fortnight of real patients; *"clinician
> sign-off on flag quality"* is the same ≥95% agreement requirement that
> [Phase 3's gate](phase-03-rule-engine.md) is still blocked on, and it is
> still the case that **no clinician has reviewed the rule engine** — see
> [`docs/clinical-validation.md`](../clinical-validation.md).
>
> The two ticked clauses are ticked because they are properties of the system
> that a test can demonstrate, and the tests exist. The other three are not,
> and marking them would be a lie about a patient-safety gate.

---

**Cross-ref:** [architecture/04-rag-explain-and-closure.md](../architecture/04-rag-explain-and-closure.md) (Step 11)
