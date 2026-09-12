# Result Guardian — Knowledge Base Index

This directory is the **source of truth**. It was extracted at full fidelity from two PDFs which are now archive-only:

| PDF | Pages | View | Chunked into |
|---|---|---|---|
| `result-guardian-architecture-2server.txt.pdf` | 24 | **Runtime** — what flows where at execution time | `architecture/` |
| `result-guardian-build-plan-v2.md.pdf` | 48 | **Construction** — what to build in what order | `build/` |

> The two views are complementary, not redundant. Architecture answers *"what happens when a doctor clicks Discharge?"* Build answers *"what do I write this week?"*

---

## Reading order for a fresh session

1. [`../CLAUDE.md`](../CLAUDE.md) — rules you must not break
2. [`../PROGRESS.md`](../PROGRESS.md) — what's done, what's next
3. [`build/00-governing-rules-and-topology.md`](build/00-governing-rules-and-topology.md) — the two governing rules
4. The specific `build/phase-NN-*.md` you are working on
5. Its **Cross-ref** line → the matching architecture chunk

---

## `architecture/` — runtime view

| File | Covers |
|---|---|
| [00-two-node-topology.md](architecture/00-two-node-topology.md) | Step 0 · NODE A / NODE B · LAN fallback chain · node env settings · why embeddings stay on A |
| [01-patient-to-discharge-gate.md](architecture/01-patient-to-discharge-gate.md) | Steps 1–3 · admission → **discharge gate** → post-discharge tracking |
| [02-ingestion-extraction-matching.md](architecture/02-ingestion-extraction-matching.md) | Steps 4–6 · ingestion layer · classification & extraction · order matching |
| [03-rules-ownership-escalation.md](architecture/03-rules-ownership-escalation.md) | Steps 7–9 · Rules A/B/C · owner resolution · escalation ladder |
| [04-rag-explain-and-closure.md](architecture/04-rag-explain-and-closure.md) | Steps 10–11 · hybrid RAG · span verifier · doctor decision · loop closed |
| [05-degradation-security-ai-boundary.md](architecture/05-degradation-security-ai-boundary.md) | Degradation ladder · `/api/health` · foundation · HIS · future imaging · DPDP/NABH · AI boundary |

## `build/` — construction view

| File | Covers |
|---|---|
| [00-governing-rules-and-topology.md](build/00-governing-rules-and-topology.md) | **RULE 1, RULE 2** · phase → node dependency table |
| [01-tech-stack-and-repo-layout.md](build/01-tech-stack-and-repo-layout.md) | **LOCKED** stack · repo tree · base conventions |
| [phase-00-foundation.md](build/phase-00-foundation.md) | Repo, containers, NODE B provisioning, network runbook, skeleton, worker, CI |
| [phase-01-data-model-discharge-gate.md](build/phase-01-data-model-discharge-gate.md) | ★ **The product.** Schema, readiness API, gate UI, corpus + vendor kickoff |
| [phase-02-durable-timers.md](build/phase-02-durable-timers.md) | SLA timers, pg_cron sweep, lab flags, manual result intake |
| [phase-03-clinical-rule-engine.md](build/phase-03-clinical-rule-engine.md) | Result schema, config tables, Rules A/B/C, orchestrator, clinician validation |
| [phase-04-ownership-escalation.md](build/phase-04-ownership-escalation.md) | Roster, owner resolution, notifications, 5-rung ladder, **alert fatigue** |
| [phase-05-dashboard-audit-mvp.md](build/phase-05-dashboard-audit-mvp.md) | 🏁 **MVP.** Auth, dashboard, closure, hash-chained audit, metrics |
| [phase-06-document-ingestion.md](build/phase-06-document-ingestion.md) | Upload, OCR, spans with bboxes |
| [phase-07-extraction-matching.md](build/phase-07-extraction-matching.md) | Templates-before-LLM, LOINC, **no-AI patient matching** |
| [phase-08-rag-explanation.md](build/phase-08-rag-explanation.md) | KB, hybrid retrieval, NODE B generation, **span verifier** |
| [phase-09-hospital-integration.md](build/phase-09-hospital-integration.md) | HL7 v2, FHIR R4, master data, reconciliation |
| [phase-10-security-production.md](build/phase-10-security-production.md) | Security, DPDP, NABH, reliability, **6 degraded-mode tests**, rollout |
| [99-gaps-timeline-degradation.md](build/99-gaps-timeline-degradation.md) | Gap traceability · timeline · **THE ONE RULE** |

---

## Standalone records

| File | Covers |
|---|---|
| [clinical-validation.md](clinical-validation.md) | **Has a clinician checked the rule engine? No.** Phase 3.8's status, why Exit Gate 3 is open, and what a hospital must do to close it |
| [clinical-validation-protocol.md](clinical-validation-protocol.md) | **How Exit Gate 3 gets closed.** Import schema for real results, de-identification, reviewer identity, disagreement handling, and the agreement arithmetic — fixed in advance |
| [network-runbook.md](network-runbook.md) | Getting NODE A and NODE B talking |

---

## Conventions in these files

- `- [ ]` checkboxes are the **live backlog**. Tick them as work completes — the doc *is* the task list.
- `★` marks items the source plan singles out as high-risk or high-value.
- `⛔ BLOCKING PREREQUISITE` marks a phase that cannot start until something outside the code is ready.
- Every phase ends with `## ✅ EXIT GATE N`. **Do not start phase N+1 until every box there is ticked.**

## Re-extracting the PDFs

If a chunk is ever suspected of drift, re-extract and diff:

```python
import pypdf
r = pypdf.PdfReader("result-guardian-build-plan-v2.md.pdf")
print("\n".join(p.extract_text() or "" for p in r.pages))
```
