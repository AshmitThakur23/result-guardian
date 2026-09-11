# Architecture 01 — Patient → Discharge Gate → Tracking (Steps 1–3)

> Source: architecture PDF, Steps 1–3. **All on NODE A.**

## STEP 1 — Patient in hospital · `[NODE A]`

```
                 Patient Admitted
                       │
                       ▼
       Hospital Information System (HIS)
                       │
                       ▼
    Doctor Orders Tests / Imaging / Procedures
                       │
                       ▼
       Sample Collected → Lab / Radiology /
               Pathology Department
                       │
                       ▼
          Patient Receives Treatment
```

---

## STEP 2 — DISCHARGE GATE ★ CORE INNOVATION · `[NODE A]`

```
                       │
                       ▼
             Doctor Clicks "DISCHARGE"
                       │
                       ▼
        System Checks: Any Investigation Pending?
                       │
            ┌──────────┴──────────┐
            │                     │
           NO                    YES
            │                     │
            ▼                     ▼
     Discharge Allowed      SCREEN STOPS
            │                     │
            │                     ▼
            │              Show Pending Test
            │              "Urine Culture"
            │                     │
            │                     ▼
            │             Doctor MUST assign:
            │             ┌─────────────────────┐
            │             │ Responsible Doctor  │
            │             │ Expected-by Date    │
            │             │ Cannot Skip         │
            │             └─────────────────────┘
            │                     │
            │                     ▼
            │             DIGITAL CONTRACT
            │                  SAVED
            │                     │
            └──────────────┬──────┘
                           ▼
                    Patient Discharged
                           │
                           ▼
                 PENDING CASE CREATED
                           │
                           ▼
                   TRACKING TIMER STARTS
```

> **No GPU. No network. No AI. Works with NODE B switched off.**

---

## STEP 3 — Post-discharge tracking / waiting · `[NODE A]`

```
                           │
            ┌──────────────┴──────────────┐
            │                             │
            ▼                             ▼
      REPORT ARRIVES              NO REPORT BY DEADLINE
            │                             │
            │                             ▼
            │                    "Sample / Report Missing?"
            │                             │
            │                             ▼
            │                     Notify Lab + Owner
            │                             │
            └──────────────┬──────────────┘
                           ▼
                 REPORT PROCESSING STARTS
```

---

**Cross-ref:** [build/phase-01-data-model-discharge-gate.md](../build/phase-01-data-model-discharge-gate.md) · [build/phase-02-durable-timers.md](../build/phase-02-durable-timers.md)
