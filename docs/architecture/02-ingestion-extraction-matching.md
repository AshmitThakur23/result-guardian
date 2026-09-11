# Architecture 02 — Ingestion, Classification, Extraction, Matching (Steps 4–6)

> Source: architecture PDF, Steps 4–6. **NODE A — NODE B only as a fallback in Step 5.**

## STEP 4 — Report ingestion layer · `[NODE A]`

```
                 REPORT / RESULT ARRIVES
                           │
                           ▼
┌────────────────────────────────────────────────────────────┐
│                  REPORT INGESTION LAYER                    │
│                                                            │
│  • PDF Reports                                            │
│  • HL7 v2 Messages                                        │
│  • FHIR APIs / Resources                                  │
│  • LIS / HIS Integration                                  │
│  • Future: DICOM Medical Images                           │
└────────────────────────────────────────────────────────────┘
                           │
                           ▼
               What type of input?
                           │
            ┌──────────────┴──────────────┐
            │                             │
     Native Text PDF                 Scanned Report
            │                             │
            ▼                             ▼
     Docling / PyMuPDF                 PaddleOCR
            │                             │
            └──────────────┬──────────────┘
                           ▼
```

---

## STEP 5 — AI report classification + extraction · `[NODE A — NODE B only as fallback]`

```
                 AI REPORT CLASSIFICATION
                           │
     ┌─────────────────────┼─────────────────────┐
     │                     │                     │
     ▼                     ▼                     ▼
LABORATORY REPORT     RADIOLOGY REPORT      PATHOLOGY REPORT
     │                     │                     │
CBC / LFT / Culture   CT / MRI / X-Ray     Biopsy / Histology
     │                     │                     │
     └─────────────────────┼─────────────────────┘
                           ▼
          MEDICAL DATA EXTRACTION ENGINE
                           │
                           ▼
          Extract Structured Report Data
          ┌────────────────────────────────┐
          │ • Test / Finding Name          │
          │ • Value                        │
          │ • Reference Range              │
          │ • Unit                         │
          │ • Organism / S-R Result        │
          │ • Report Status                │
          │ • Bounding Box / Source Region │
          └────────────────────────────────┘
                           │
                           ▼
               LOINC NORMALIZATION
        ("S. Creat" → Serum Creatinine)
                           │
                           ▼
                 Structured JSON
                           │
                           ▼
                Store in PostgreSQL
```

> Templates and table parsing run on **NODE A**. NODE B is called only for documents those two cannot handle. **If NODE B is down, the document goes to manual entry instead.**

---

## STEP 6 — Report → pending order matching · `[NODE A]`

```
            Match Report to Pending Order
                           │
                           ▼
                 Order ID Available?
                  ┌────────┴────────┐
                 YES                NO
                  │                  │
                  ▼                  ▼
            Exact / Code       AI-assisted
               Match            Fuzzy Match
                  │                  │
                  └────────┬─────────┘
                           ▼
                 Preliminary or Final?
                           │
            ┌──────────────┴──────────────┐
            │                             │
      PRELIMINARY                       FINAL
            │                             │
            ▼                             ▼
       HOLD / WAIT                 Continue Processing
   (prevent double alerts)
```

---

**Cross-ref:** [build/phase-06-document-ingestion.md](../build/phase-06-document-ingestion.md) · [build/phase-07-extraction-matching.md](../build/phase-07-extraction-matching.md)
