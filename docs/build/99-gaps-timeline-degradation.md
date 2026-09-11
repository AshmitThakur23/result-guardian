# Build 99 — Gap Traceability, Timeline, The One Rule

> Source: build plan PDF, pages 45–48.

## Gap traceability

Every item previously listed as "missing from your diagram" is now a task inside a phase.

| Gap | Now lives in | Status |
|---|---|---|
| Auth, roles, sessions | 1.1 (`users` table), 5.1 (full auth) | Folded in |
| Amended / corrected reports reopening closed cases | 3.6, 7.6 | Folded in |
| Duty roster as a real data source | 4.1 — with an explicit "who maintains it" decision | Folded in |
| Patient phone validation at admission | 1.1 (`phone_verified_at`), 4.6 | Folded in |
| Alert fatigue | 4.5 — with the flag-rate-per-100-discharges metric | Folded in |
| Medico-legal liability policy | 10.3 | Folded in |
| OPD / outpatient pending results | 1.1 `encounters.type`, Phase 1 scope note | **Decision forced** |
| Death / LAMA / transfer closure paths | 1.1 `encounters.status`, 4.6 suppression | Folded in |
| Multiple pending tests → notification grouping | 4.5 dedup / grouping | Folded in |
| Per-hospital configurability | 3.2, 4.1 — config tables, never code | Folded in |
| Contaminant handling in cultures | 3.4 step 2 | Folded in |
| De-identified test corpus | 1.6 — starts in Phase 1 | Folded in |
| Lab-side accountability | 2.3 `lab_flags` | Folded in |
| Two-node deployment | Topology section, 0.3, 0.4, 8.4, 10.1, 10.4 | **New** |
| Rule A/B/C algorithm mix-up | 3.3, 3.4, 3.5 | **Corrected** |
| Network fallback + client isolation | 0.4, `docs/network-runbook.md` | **New** |
| LLM kill switch and degradation logging | 0.5, 5.4, 8.4 | **New** |

---

## Timeline

| Block | Phases | Team of 3 | Solo |
|---|---|---|---|
| Foundation + gate + timers | 0–2 | 5 weeks | 9 weeks |
| Rules + escalation + dashboard | 3–5 | 6 weeks | 12 weeks |
| **🏁 MVP — pilot ready** | | **11 weeks** | **21 weeks** |
| Ingestion + extraction | 6–7 | 4 weeks | 9 weeks |
| RAG explanation | 8 | 3 weeks | 5 weeks |
| Integration | 9 | 4 weeks | 6 weeks |
| Security + production | 10 | 3 weeks | 5 weeks |
| **🏁 Production** | | **25 weeks** | **46 weeks** |

**Parallelisable with 3 people:** Phases 6–7 (document track) can run alongside Phases 4–5 (workflow track) **once the Phase 3 schema is frozen**. NODE B provisioning (0.3) can be done by anyone, any time, since nothing depends on it until Phase 8.

---

## THE ONE RULE

> **A later phase must never be able to break an earlier one.**

| Failure | What still works | What is lost |
|---|---|---|
| Nothing wrong | Everything | — |
| NODE B off / model unloaded | Tracking, flags, timers, escalation, SMS, dashboard, retrieval | Generated prose. Chunks still shown. |
| LAN between nodes down | Same as above | Live generation. Cache serves seen cases. |
| Extraction fails | Case still tracked, human enters the result | Automatic intake |
| OCR fails | Manual entry with page images side-by-side | Automatic text |
| HIS offline | Manual order entry | Auto order sync |
| SMS provider down | In-app and email rungs still fire, failure is visible | SMS rung |
| NODE A reboots | `pg_cron` sweep re-fires every overdue timer | Nothing — timers live in the DB |

> **Build each phase so the system degrades to the previous phase, not to silence.**

---

**Cross-ref:** [architecture/05-degradation-security-ai-boundary.md](../architecture/05-degradation-security-ai-boundary.md)
