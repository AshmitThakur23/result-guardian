# Result Guardian — Progress Ledger

> **Read this first, every session.** It is the resume point.
> Updating it after every unit of work is mandatory — see the protocol in [`CLAUDE.md`](CLAUDE.md).

**Last updated:** 2026-09-11

---

## Phase status

Status values: `not started` · `in progress` · `blocked` · `exit gate passed`

| Phase | Node | Status | Exit gate | Notes |
|---|---|---|---|---|
| — Knowledge base | — | **done** | n/a | 20 docs extracted from both PDFs, 2026-09-11 |
| 0 · Foundation | A + B | `not started` | ☐ | 5–7 days. NODE B provisioning (0.3) can be done any time by anyone |
| 1 · Data model + discharge gate ★ | A | `not started` | ☐ | 2–3 wks. **This is the product.** Also kicks off 1.6 corpus + 1.7 vendor |
| 2 · Durable timers | A | `not started` | ☐ | 1–1.5 wks |
| 3 · Clinical rule engine | A | `not started` | ☐ | 2–3 wks. **Book clinician time now** |
| 4 · Ownership + escalation ★ | A | `not started` | ☐ | 2–3 wks. Alert fatigue controls ship in the same sprint |
| 5 · Dashboard, closure, audit | A | `not started` | ☐ | 2–3 wks → 🏁 **MVP, pilot ready** |
| 6 · Document ingestion | A | `not started` | ☐ | 2 wks. ⛔ blocked by 1.6 corpus |
| 7 · Extraction + matching | A (+B fallback) | `not started` | ☐ | 3 wks |
| 8 · RAG explanation | A + B | `not started` | ☐ | 3 wks. First phase that uses NODE B |
| 9 · Hospital integration | A | `not started` | ☐ | 3–5 wks. ⛔ gated by hospital IT / HIS vendor |
| 10 · Security + production | A + B | `not started` | ☐ | 3 wks + external test turnaround |

**Timeline reference:** MVP at 11 weeks (team of 3) / 21 weeks (solo). Production at 25 / 46 weeks.

---

## ⏳ Long-lead items — calendar-gated, start regardless of phase

These are not blocked by code. They are blocked by other people, and they take months. **The build plan says to start the top two in week one of Phase 1.**

| Item | Source | Blocks | Status | Started |
|---|---|---|---|---|
| De-identified corpus, 200+ real reports | 1.6 | **Phase 6 cannot start without it** | `not started` | — |
| HIS/LIS vendor conversation | 1.7 | **Phase 9** | `not started` | — |
| Clinician time — gold set, 100+ results | 3.8 | **Exit Gate 3** | `not started` | — |
| Clinician time — 100-question AI eval set | 8.7 | **Exit Gate 8** | `not started` | — |
| Medico-legal liability policy, signed | 10.3 | **Go-live** | `not started` | — |
| Hospital's own critical-value list (for `panic_thresholds`) | 3.2 | Phase 3 seeding | `not started` | — |

---

## ❓ Open decisions

Record the decision, the date, and the reasoning. A decision that lives only in chat gets re-litigated.

| # | Decision | Forced by | Status |
|---|---|---|---|
| 1 | **OPD in scope or out for v1?** OPD pending results are the same failure mode at higher volume but have no discharge event to hang the gate on. If in scope, the trigger becomes "visit closed". **Changes the encounter model — cannot be deferred past Phase 1.** | Phase 1 scope note | ⬜ **undecided** |
| 2 | **Who maintains `duty_roster`?** Options: HR/AD sync (Phase 9.4), unit-head weekly entry, or admin upload. A roster nobody updates is worse than no roster — the system will confidently notify someone who left. Must be written into the SOP. | Phase 4.1 | ⬜ **undecided** |
| 3 | Node IPs / network method for the real deployment (router vs hotspot vs direct ethernet) | Phase 0.4 | ⬜ undecided — docs assume `192.168.1.10` / `192.168.1.50` |
| 4 | Qwen3 model size: start 8B q4, benchmark 14B/32B against the actual GPU | Phase 8.4 | ⬜ undecided |

---

## 📓 Session log

Newest first. One line per completed unit of work.

### 2026-09-11

- Read both source PDFs in full (architecture 24pp, build plan 48pp) via `pypdf` text extraction.
- Created `docs/architecture/` — 6 chunks covering Steps 0–11, degradation ladder, security, AI boundary.
- Created `docs/build/` — 16 chunks: governing rules, locked stack + repo layout, Phases 0–10 as checklists with exit gates, gap traceability + timeline.
- Created `docs/README.md` index with reading order and cross-refs.
- Created `CLAUDE.md` — governing rules, mandatory work-logging protocol, locked decisions, doc map.
- Created `PROGRESS.md` (this file) — phase table, long-lead items, open decisions, session log.
- Wrote memory base at `~/.claude/projects/d--result-guardian/memory/` (5 memories + index).
- **Next:** decide Open Decision #1 (OPD scope) before Phase 1; Phase 0.1 repo scaffold is the first code task.
