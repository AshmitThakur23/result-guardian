# ADR 0002 — Two nodes, and embeddings stay on NODE A

**Status:** Accepted · **Date:** 2026-09-11 · **Phase:** 0.1

## Context

The workflow needs a database that is always up. The LLM needs a GPU. A hospital has an office PC per ward and, at best, one shared GPU box. Those are different machines with different availability characteristics, and the GPU box is the one that gets borrowed, rebooted, or repurposed.

## Decision

**Two nodes on a private LAN with no internet requirement.**

- **NODE A — core.** Postgres, FastAPI, worker, Caddy, React dashboard, document storage. **All patient data. All safety logic.**
- **NODE B — inference.** Ollama + Qwen3 only. Stateless. No database, no disk writes, no patient data at any moment. Rebuildable in under 15 minutes from a provisioning script.

**Only generation crosses the wire.** Embedding (bge-m3) and reranking (bge-reranker-v2-m3) run on **NODE A's CPU**, beside pgvector.

What crosses: the clinical question, retrieved guideline text, the JSON answer back.
What never crosses: patient name, MRN, phone, address, encounter rows, original PDFs.

## Rationale for keeping embeddings on NODE A

1. **Retrieval never depends on the LAN.** If the link is down, hybrid search still returns guideline chunks; only the written prose is lost. The doctor still sees evidence.
2. **No round trip for a millisecond-scale search.** A vector search next to the database costs less than the network hop to reach a GPU.
3. **NODE B becomes a pure swappable function.** Prompt in, JSON out, forgets. Unplug it and nothing stateful is lost — which is precisely what makes RULE 2 true rather than aspirational.

## Consequences

- Phases 0–7 and 9–10 have **no dependency on NODE B**. Phase 8 is the only one that does, and it degrades to showing retrieved chunks.
- `/api/health` carries an `llm` block from day one, probed on a 30 s cache that **never blocks the response**. The dashboard shows "AI: offline — core tracking unaffected" rather than an error.
- `LLM_ENABLED=false` is an admin kill switch that forces the degraded path without touching the network.
- NODE B needs **no backup** — it holds nothing. Back up the provisioning script instead.
- NODE B's firewall accepts 11434 from NODE A's IP only, and NODE B is included in the egress rules that prove the on-prem claim. Verified with firewall rules, not by promise.

## Current deployment (2026-09-11, corrected)

| Role | Machine | Notes |
|---|---|---|
| **NODE A** | **Abhinendra's laptop** (`LAPTOP-06ER0HBM`) | Docker present. Holds the database and every safety guarantee. **No NVIDIA GPU — Intel UHD only.** It does not need one: NODE A never runs inference. |
| **NODE B** | **Ashmit's machine** (`LAPTOP-5JCGN9SJ`) | Not yet provisioned. **NVIDIA RTX 3050 Laptop, 4 GB VRAM** — verified with `nvidia-smi` on that machine. |

> ⚠️ An earlier version of this table had these two **backwards**, and a later
> revision then attached the GPU to the wrong machine. Both errors came from
> writing *"this machine"* in files read on two machines. See
> [ADR 0006](0006-node-roles-corrected.md) and its correction note, which
> supersede [ADR 0005](0005-node-roles-and-model.md).
>
> **`qwen3:4b` is a sound decision, not a placeholder.** It was derived from
> 4 GB of VRAM belonging to NODE B — the node that actually runs inference —
> and 8B q4 (~5–6 GB) genuinely does not fit.

The model is an env var (`LLM_MODEL`), so a larger model on a real GPU box is a one-line change. See [0005](0005-node-roles-and-model.md).

## Alternatives rejected

- **Single node with GPU** — a hospital will not give a ward an always-on GPU machine, and it makes tracking depend on GPU availability. Violates RULE 2.
- **Cloud LLM API** — breaks the on-prem claim and DPDP posture. Listed in the network fallback chain as a last resort requiring written approval, and it has never been chosen.
- **Embeddings on NODE B** — would make retrieval depend on the LAN, which makes the Explain feature fail *completely* rather than partially when the link drops.
