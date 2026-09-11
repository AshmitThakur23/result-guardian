# Architecture 04 — AI Explanation & Closing the Loop (Steps 10–11)

> Source: architecture PDF, Steps 10–11.
> **Step 10 is the ONLY step that uses NODE B — and only when a doctor clicks.**

## STEP 10 — AI explanation — only when doctor asks

```
                 Doctor Opens Critical /
                   Follow-up Flag
                           │
                           ▼
                Doctor Clicks "EXPLAIN"
                           │
                           ▼
          Load Structured JSON from PostgreSQL
               (PDF is NOT re-read)
                           │
                           ▼
                 HYBRID RAG LAYER
                      [ NODE A ]
                           │
          ┌────────────────┴────────────────┐
          │                                 │
          ▼                                 ▼
  Semantic Vector Search             Keyword Search
          │                                 │
     bge-m3 Embeddings                    BM25
     (NODE A — CPU)                    (PostgreSQL)
          │                                 │
          └────────────────┬────────────────┘
                           ▼
                   Context Fusion
                           │
                           ▼
                 Vector Knowledge Store
                      (pgvector)
                      [ NODE A ]
                           │
┌──────────────────────────────────────────────────────┐
│              TRUSTED MEDICAL KNOWLEDGE BASE          │
│                     [ NODE A ]                       │
│                                                      │
│ • WHO Guidelines                                     │
│ • ICMR Guidelines                                    │
│ • Hospital SOPs                                      │
│ • Hospital Antibiogram                               │
│ • Antibiotic / AMR Policies                          │
│ • NLEM Drug Reference                                │
│ • Clinical Protocols                                 │
│ • Medical Knowledge Documents                        │
│                                                      │
│              NO PATIENT DATA                         │
└──────────────────────────────────────────────────────┘
                           │
                           ▼
             bge-reranker-v2-m3
          (Rank Retrieved Evidence)
             [ NODE A — CPU ]
                           │
                           ▼
              Is NODE B reachable?
                 ┌─────────┴─────────┐
                YES                  NO / timeout
                 │                        │
                 │                        ▼
                 │              Show retrieved chunks
                 │              with no generated text
                 │              Flag stays CRITICAL
                 │              Escalation keeps running
                 │
                 ▼
      ─ ─ ─ ─ ─ PRIVATE LAN ─ ─ ─ ─ ─
      SENT:  question + guideline text
      NOT SENT: name, MRN, phone, PDF,
                any patient table row
                 │
                 ▼
┌──────────────────────────────────────────────────────┐
│        NODE B — INFERENCE SERVER                     │
│        192.168.1.50 : 11434                          │
│                                                      │
│            Qwen 3 Instruct via Ollama                │
│                  GPU / VRAM                          │
│                                                      │
│        STATELESS. Prompt in → JSON out → forgets.    │
│        Stores nothing. Logs nothing. Keeps nothing.  │
│        30 second timeout, then NODE A degrades.      │
└──────────────────────────────────────────────────────┘
                 │
                 ▼
      ─ ─ ─ ─ ─ PRIVATE LAN ─ ─ ─ ─ ─
      Structured JSON returns
                 │
                 ▼
             STRUCTURED AI RESPONSE
                [ back on NODE A ]
          ┌────────────────────────────────┐
          │ • Actionable                   │
          │ • Clinical Reason              │
          │ • Clinical Summary             │
          │ • Evidence                     │
          │ • Source / Citation            │
          │ • Source Span                  │
          └────────────────────────────────┘
                           │
                           ▼
                 SPAN VERIFIER
                  (CODE — NO AI)
                    [ NODE A ]
                           │
             Does citation point to
             real source text?
                 ┌─────────┴─────────┐
                YES                  NO
                 │                    │
                 ▼                    ▼
           Show Evidence          Reject Response
                 │
                 ▼
      Evidence-Based Explanation
      + Exact Source Line Highlighted
                 │
                 ▼
          Doctor Reviews Information
```

---

## STEP 11 — Doctor decision + close the loop · `[NODE A]`

```
                 Doctor Makes Decision
                 (AI NEVER decides)
                           │
                           ▼
               Acknowledge + Record Reason
                           │
      ┌────────────────────┼────────────────────┐
      │                    │                    │
      ▼                    ▼                    ▼
Action Taken        Already Handled      Not Clinically
                                            Relevant
      │                    │                    │
      └────────────────────┼────────────────────┘
                           │
                      Duplicate Report
                           │
                           ▼
                      CLOCK STOPS
                           │
                           ▼
                 ESCALATION CANCELLED
                           │
                           ▼
                      CASE CLOSED
                           │
            ┌──────────────┴──────────────┐
            │                             │
            ▼                             ▼
     HASH-CHAINED AUDIT              FHIR R4 UPDATE
      APPEND-ONLY LOG
            │                             │
            └──────────────┬──────────────┘
                           ▼
                 LOOP CLOSED / COMPLETE
```

---

**Cross-ref:** [build/phase-08-rag-explanation.md](../build/phase-08-rag-explanation.md) · [build/phase-05-dashboard-audit-mvp.md](../build/phase-05-dashboard-audit-mvp.md)
