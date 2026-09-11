# Architecture 05 — Degradation, Foundation, Integration, Security, AI Boundary

> Source: architecture PDF, everything after Step 11.

## Degradation ladder — the one rule

> **A LATER LAYER MUST NEVER BREAK AN EARLIER ONE.**
> The system degrades to the previous layer, never to silence.

| Failure | What still works | What is lost |
|---|---|---|
| Nothing wrong | Everything | — |
| NODE B off / model unloaded | Tracking, flags, timers, escalation, SMS, dashboard, retrieval | Written prose. Chunks still shown. |
| LAN down entirely | Tracking, flags, timers, escalation, SMS, dashboard, retrieval | Live generation. Cache serves seen cases. |
| OCR / extraction fails | Case still tracked. Result goes to manual entry with page images side by side | Automatic intake |
| HIS / LIS offline | Manual order entry | Auto order sync |
| SMS provider down | In-app + email rungs fire. Failure visible on the admin dashboard | SMS rung |
| NODE A reboots | `pg_cron` sweep re-fires every overdue timer on restart | Nothing. Timers live in the DB. |

### Health endpoint — `/api/health`

```json
{
  "status": "ok",
  "db": "ok",
  "worker_heartbeat_age_s": 4,
  "llm": { "reachable": true, "host": "192.168.1.50:11434" },
  "degraded_features": []
}
```

Shown in the dashboard header as a status pill:
`"AI: connected"` / `"AI: offline — core tracking unaffected"`

---

## Shared system foundation

```
            ON-PREMISE DEPLOYMENT — TWO SERVERS
                           │
     ┌─────────────────────┴─────────────────────┐
     │                                           │
     ▼                                           ▼
NODE A — STATE                            NODE B — COMPUTE
     │                                           │
     ▼                                           ▼
 PostgreSQL                              Qwen 3 via Ollama
     │                                           │
┌────┼─────────────┬─────────────┐               ▼
▼    ▼             ▼             ▼        Nothing persisted
PostgreSQL     pgvector        pgmq       Nothing stored
     │             │             │        Rebuild in 5 min
┌────┼────┐        │        Durable SLA
│    │    │        │          Timers
▼    ▼    ▼        ▼
Cases Owners States Guideline
                Embeddings
     │
     ▼
Append-Only Audit Log
     │
     ▼
 SHA-256 Chain

        ONE docker-compose.yml on NODE A
        ONE ollama service on NODE B
                  │
                  ▼
          ON-PREMISE SERVERS
                  │
                  ▼
      Local Qwen / Ollama Inference
                  │
                  ▼
         Hospital Data Stays Local
```

> Everything that must survive a crash lives on NODE A. **NODE B can be wiped and rebuilt and the hospital loses nothing.**

---

## Hospital integration

```
      HIS / LIS / Hospital Systems
                │
     ┌──────────┼──────────┐
     │          │          │
     ▼          ▼          ▼
  HL7 v2     FHIR R4     APIs
     │          │          │
     └──────────┼──────────┘
                ▼
         RESULT GUARDIAN
             [ NODE A ]
                │
                ▼
         FHIR R4 Update
                │
                ▼
      Hospital Record Updated
```

---

## Future medical imaging

```
  DICOM X-Ray / CT / MRI / Medical Images
                │
                ▼
         DICOM Processing
                │
                ▼
       Medical Imaging AI
      (MONAI / PyTorch / nnU-Net)
          [ NODE B — GPU ]
                │
                ▼
       Findings / Structured Data
                │
                ▼
         Result Guardian
         Workflow Engine
            [ NODE A ]
```

> **NOTE:** Actual medical-image analysis is **FUTURE SCOPE**. The current prototype processes the textual Radiology Report. This is also why the GPU server exists as its own role — imaging is the workload it scales into.

---

## Security / governance

```
              Hospital Data
                   │
                   ▼
            ON-PREMISE PROCESSING
            BOTH SERVERS, ONE LAN
                   │
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
Access Control  Encryption   Audit Trail
     │             │             │
     └─────────────┼─────────────┘
                   ▼
          Privacy / Governance
              DPDP Act 2023
```

- Data never leaves the hospital network.
- NODE B holds no patient data at any moment.
- **Verified with egress firewall rules, not by promise.**

**NABH 6th Edition references:** `AAC.12`, `AAC.6.g`

---

## AI boundary

```
              RESULT GUARDIAN
                   │
  ┌────────────────┴────────────────┐
  │                                 │
  ▼                                 ▼
WORKFLOW ENGINE                 AI ENGINE
 [ NODE A ]                  [ NODE A + NODE B ]
NO AI. EVER.                 OPTIONAL. REMOVABLE.
  │                                 │
Discharge Gate                Report Reading
Ownership                     Extraction
Timers                        Classification
Matching                      Explanation
Rule Engine                   Hybrid RAG
Notifications                 Evidence Retrieval
Escalation                    Citation Verification
Closure
  │                                 │
  └────────────────┬────────────────┘
                   ▼
             DOCTOR DECIDES
```

> AI assists. Rules trigger. Workflow guarantees accountability. **The doctor decides.**
>
> Turn off NODE B and the patient is still tracked, still flagged, still escalated. **That is the design.**

---

**Cross-ref:** [build/99-gaps-timeline-degradation.md](../build/99-gaps-timeline-degradation.md) · [build/phase-10-security-production.md](../build/phase-10-security-production.md)
