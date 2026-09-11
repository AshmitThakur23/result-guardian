# Build 00 — Governing Rules & Deployment Topology

> Source: `result-guardian-build-plan-v2.md.pdf`, pages 1–3.

Phase-by-phase execution plan. Every phase has: goal, duration, which node it runs on, task checklist, database objects, API endpoints, tests, and an exit gate.

> **Do not start a phase until the previous exit gate passes.**

---

## TWO GOVERNING RULES

### RULE 1 — The safety property must never depend on AI.

A pending result is tracked and escalated by **database constraints and a state machine**. Phases 0–5 give you a complete working product with **zero AI**. Phases 6–10 make it faster and connected.

### RULE 2 — The safety property must never depend on the network between nodes.

Everything that keeps a patient safe runs on **NODE A**. NODE B is a stateless accelerator. **Unplug it and the system loses a convenience, never a guarantee.**

---

## Deployment topology — two nodes

Decide this before Phase 0. Every later phase references it.

```
                 PRIVATE LAN — NO INTERNET REQUIRED
                                │
┌───────────────────────────────┴───────────────────────────┐
▼                                                           ▼
┌──────────────────────────────┐        ┌──────────────────────────────┐
│  NODE A — CORE SERVER        │        │  NODE B — INFERENCE SERVER   │
│  192.168.1.10                │        │  192.168.1.50                │
│  Any office PC. CPU only.    │        │  One shared GPU box.         │
├──────────────────────────────┤        ├──────────────────────────────┤
│  Caddy            :80/:443   │        │  Ollama          :11434      │
│  FastAPI          :8000      │ ─HTTP─►│    Qwen3-Instruct            │
│  Worker (pgmq consumers)     │  30s   │                              │
│  PostgreSQL 16    :5432      │◄─JSON──│  GPU / VRAM                  │
│    ├ pgvector                │        │                              │
│    ├ pgmq                    │        │  STATELESS                   │
│    ├ pg_cron                 │        │  No database                 │
│    ├ pg_trgm                 │        │  No patient data at rest     │
│    └ unaccent                │        │  No disk writes              │
│  bge-m3 embedder    CPU      │        │  Rebuildable in 5 minutes    │
│  bge-reranker       CPU      │        │                              │
│  React dashboard (static)    │        │                              │
│  Document storage (disk)     │        │                              │
│                              │        │                              │
│  ALL PATIENT DATA            │        │  NEVER HOLDS PATIENT DATA    │
│  ALL SAFETY LOGIC            │        │  OPTIONAL TO THE WORKFLOW    │
└──────────────────────────────┘        └──────────────────────────────┘
```

---

## Phase → node dependency table

| Phase | Node | Depends on NODE B? |
|---|---|---|
| 0 Foundation | A (+ B provisioning) | No |
| 1 Discharge gate | A | No |
| 2 Timers | A | No |
| 3 Rule engine | A | No |
| 4 Escalation | A | No |
| 5 Dashboard / audit | A | No |
| 6 Document ingestion | A | No (OCR is local) |
| 7 Extraction | A | Optional fallback only |
| 8 RAG explanation | A + B | **Yes**, degrades cleanly |
| 9 HIS integration | A | No |
| 10 Production | A + B | No |

**What crosses the wire:** the clinical question, retrieved guideline text, the JSON answer back.
**What never crosses:** patient name, MRN, phone, address, encounter rows, original PDFs.

---

## Design decision — embeddings stay on NODE A

Only **generation** crosses the network. Embedding and reranking run on NODE A's CPU beside pgvector. This means:

1. Retrieval never depends on the LAN
2. There is no round trip for a millisecond-scale search
3. NODE B stays a pure swappable function

Record this in `docs/adr/0002-two-node-split.md`.

---

**Cross-ref:** [architecture/00-two-node-topology.md](../architecture/00-two-node-topology.md)
