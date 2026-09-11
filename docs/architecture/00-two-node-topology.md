# Architecture 00 — The Two Servers (Step 0)

> Source: `result-guardian-architecture-2server.txt.pdf`, Step 0 + "How the two servers are connected".
> Read this before any other architecture chunk. Every later step states which node it runs on.

Everything runs across **two machines on one private network**.

```
                 PRIVATE LAN — NO INTERNET REQUIRED
                              │
┌─────────────────────────────┴─────────────────────────────┐
│                                                           │
▼                                                           ▼
┌──────────────────────────────┐            ┌──────────────────────────────┐
│  NODE A — CORE SERVER        │            │  NODE B — INFERENCE SERVER   │
│  192.168.1.10                │            │  192.168.1.50                │
│  Any office PC. CPU only.    │            │  One shared GPU box.         │
├──────────────────────────────┤            ├──────────────────────────────┤
│  Caddy            :80        │            │  Ollama          :11434      │
│  FastAPI          :8000      │            │    Qwen 3 Instruct           │
│  Worker — pgmq consumers     │  ── HTTP ─►│                              │
│  PostgreSQL 16    :5432      │   30s max  │  GPU / VRAM                  │
│    ├ pgvector  embeddings    │            │                              │
│    ├ pgmq      SLA timers    │◄─ JSON ────│  STATELESS                   │
│    └ pg_cron   sweeps        │            │  No database                 │
│  bge-m3 embed     CPU        │            │  No patient data at rest     │
│  bge-reranker     CPU        │            │  No disk writes              │
│  React dashboard             │            │  Rebuildable in 5 minutes    │
│                              │            │                              │
│  ALL PATIENT DATA HERE       │            │  NEVER HOLDS PATIENT DATA    │
│  ALL SAFETY LOGIC HERE       │            │  OPTIONAL TO THE WORKFLOW    │
└──────────────────────────────┘            └──────────────────────────────┘
        ▲
        │  browser
        │
   DOCTOR / NURSE / UNIT HEAD
   Any device on the same LAN
```

**Why two.** The workflow needs a database. The model needs a GPU. A hospital has an office PC per ward and, at best, one GPU box. So the system splits into two roles on a private network.

> **STEPS 1 to 9 and STEP 11 RUN ENTIRELY ON NODE A.**
> **ONLY STEP 10 CROSSES TO NODE B — and only when a doctor clicks.**

---

## Network — fallback chain, in order

| # | Option | Notes |
|---|---|---|
| 1 | Travel router | Best. Own DHCP. Does **not** need internet. |
| 2 | Phone hotspot | Works. IPs may shift on reconnect. |
| 3 | Ethernet direct | No router. Static `10.0.0.1` / `10.0.0.2` |
| 4 | Cloud API | Last resort. Breaks the on-prem claim. |

**NOT USABLE:** campus, hotel or public wifi. Client isolation blocks laptop-to-laptop traffic even when both show "connected".

Test with:

```bash
ping 192.168.1.50
```

No reply → switch to hotspot or cable.

The network does **not** need internet access. It only creates the local link between the two servers. Hospital internet can be completely down and NODE A ⇄ NODE B still works.

---

## NODE B settings — required

```bash
OLLAMA_HOST=0.0.0.0:11434     # bind to the LAN, not just localhost
OLLAMA_KEEP_ALIVE=-1          # keep model in VRAM, avoid reload stall
# Firewall: allow inbound TCP 11434
```

Verify from NODE A before trusting anything:

```bash
curl http://192.168.1.50:11434/api/tags
```

---

## NODE A settings — one line to switch targets

```bash
LLM_BASE_URL=http://192.168.1.50:11434    # router
LLM_BASE_URL=http://172.20.10.3:11434     # phone hotspot
LLM_BASE_URL=http://10.0.0.2:11434        # direct ethernet
LLM_ENABLED=false                         # forced degrade / kill switch
```

---

## Design decision — embeddings stay on NODE A

Only **generation** crosses the wire. Embedding and reranking run on NODE A's CPU next to pgvector. Three reasons:

1. Retrieval never depends on the LAN being up
2. No round trip for a search that takes milliseconds locally
3. NODE B becomes a pure swappable function — unplug it and retrieval still works, only the written prose is lost

Recorded as `docs/adr/0002-two-node-split.md` (to be written in Phase 0.1).

---

**Cross-ref:** [build/00-governing-rules-and-topology.md](../build/00-governing-rules-and-topology.md) · [architecture/05-degradation-security-ai-boundary.md](05-degradation-security-ai-boundary.md)
