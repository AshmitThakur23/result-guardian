# Architecture 03 — Rule Engine, Ownership, Escalation (Steps 7–9)

> Source: architecture PDF, Steps 7–9. **All on NODE A. No AI anywhere in this chunk.**

## STEP 7 — Clinical rule engine — Python / NO AI · `[NODE A]`

```
                           │
                           ▼
            DETERMINISTIC RULE ENGINE
                           │
      ┌────────────────────┼────────────────────┐
      │                    │                    │
      ▼                    ▼                    ▼

RULE A — NUMERIC     RULE B — CULTURE      RULE C — TEXT
     TEST                / SENSITIVITY          REPORT
      │                    │                    │
      ▼                    ▼                    ▼
Value + Reference     Organism + S/R       Detect clinical
     Range                 Table             flag terms
      │                    │                    │
      ▼                    ▼                    ▼
 Compare Value       Read Discharge        positive /
 with Range          Medicine              malignant /
      │                    │                growth /
┌─────┼─────┐              │                abnormal
│     │     │              ▼                    │
LOW  HIGH  NORMAL    Medicine marked R?         ▼
│     │     │        ┌──────┴──────┐       Follow-up /
│     │     │       YES            NO        Critical
│     │     │        │              │
└──┬──┘     │        ▼              ▼
   ▼        │    CRITICAL        FOLLOW-UP
ABNORMAL    │
   │        ▼
   │     AUTO-CLOSE
   ▼
How far outside range?
  ┌────────┴────────┐
  │                 │
Slightly        Far / Panic
  │                 │
  ▼                 ▼
FOLLOW-UP        CRITICAL

                           │
      ┌────────────────────┼────────────────────┐
      │                    │                    │
      ▼                    ▼                    ▼
 NORMAL RESULT      FOLLOW-UP NEEDED      CRITICAL ALERT
      │                    │                    │
      ▼                    │                    │
Auto-close + Log           └─────────┬──────────┘
                                     ▼
```

> Pure Python on NODE A. **Same input always gives same output.** Never calls NODE B. Never needs the network.

---

## STEP 8 — Owner / responsible doctor · `[NODE A]`

```
                           │
                           ▼
               Is Assigned Doctor Available?
                           │
                 ┌─────────┴─────────┐
                YES                  NO
                 │                    │
                 ▼                    ▼
          Notify Owner         Reassign to
                              On-Duty Doctor
                          (leave / shift / left)
                 │                    │
                 └─────────┬──────────┘
                           ▼
                 FLAG ON DASHBOARD
```

---

## STEP 9 — Escalation workflow ★ · `[NODE A]`

```
                 T + 0 — FLAG RAISED
                           │
                 No acknowledgement
                           ▼
                 T + 4h — OWNER REMINDER
                           │
                 No acknowledgement
                           ▼
              T + 12h — UNIT HEAD ESCALATION
                           │
                 No acknowledgement
                           ▼
              T + 24h — PATIENT NOTIFICATION
                           │
                           ▼
                 SMS / Notification
                 Hindi / Punjabi / etc.
                           │
                           ▼
                 Patient contacts hospital
```

> Timers live in **PostgreSQL on NODE A**, not only in the queue.
> NODE A reboots → `pg_cron` sweep re-fires every overdue timer.
> NODE B off → this ladder is **completely unaffected**.

---

**Cross-ref:** [build/phase-03-clinical-rule-engine.md](../build/phase-03-clinical-rule-engine.md) · [build/phase-04-ownership-escalation.md](../build/phase-04-ownership-escalation.md)
