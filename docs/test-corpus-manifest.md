# Task 1.6 — De-identified test corpus

**Status: 🔴 NOT STARTED — 0 of 200+ reports collected. This is a blocker for Phase 6.**
**Owner: human. Nothing here can be completed by writing code.**
**Opened:** 2026-09-12 (Phase 1, week one, as the build plan requires)

---

## Why this exists now, in Phase 1

Phase 6 (Document Ingestion) carries an explicit blocking prerequisite:

> ⛔ **BLOCKING PREREQUISITE:** the de-identified test corpus from **Phase 1.6**.
> If it is not ready, this phase cannot start — **do not fake it with synthetic
> PDFs, they will not surface the failure modes real labs produce.**

And the reason it starts in week one of Phase 1 rather than week one of Phase 6:

> Permissions take months. **Phase 6 cannot start without this** and you will not
> be able to compress the wait later.

Phase 6 is roughly 15 weeks out. The paperwork is the long pole, not the collecting.

---

## ⛔ What must never happen

- **No patient-identifiable report ever enters git.** `.gitignore` already blocks
  `*.pdf`, `corpus/`, `uploads/` and `data/`. Do not add exceptions.
- **The reports live outside the repository.** Only this manifest is tracked —
  that is the build plan's instruction: *"Store outside git, manifest in git."*
- **No synthetic substitutes.** Generated PDFs do not reproduce the skew, stamps,
  bleed-through, bilingual headers or broken table borders that real labs emit,
  and Exit Gate 6 measures against real documents.

De-identification must remove, at minimum: patient name, MRN, phone, address,
date of birth, relatives' names, treating doctor's name, and any barcode or QR
code that encodes an identifier. Dates may be shifted but must stay internally
consistent, because collection-date-to-report-date intervals are what Phase 7.5
matches on.

---

## Target

| | |
|---|---|
| **Count** | **200+** real reports |
| **Coverage** | every lab the hospital actually uses, not just the main one |
| **Exit Gate 6** | ≥95% must produce usable text; spans spot-checked on 20 |

## Required categorisation

Per the build plan, categorise **as you collect** — retro-fitting this is painful:

| Axis | Values |
|---|---|
| Capture | `native` (digital text) · `scanned` (needs OCR) |
| Discipline | `lab` · `radiology` · `pathology` · `micro` |
| Pages | `single` · `multi` |

Deliberately over-sample **scanned** and **micro**: scanned documents are where
OCR confidence collapses, and culture/sensitivity reports drive Rule B, the
highest-clinical-value rule in Phase 3.

---

## Current state

**0 reports collected. 0 labs covered. Manifest has 0 rows.**

See [`test-corpus-manifest.csv`](test-corpus-manifest.csv) — headers only.

## Blockers — all require human action

| # | Blocker | Needed from | Status |
|---|---|---|---|
| 1 | Written permission to use de-identified reports for system development | Hospital administration / ethics committee | ⬜ not started |
| 2 | Confirmation of the de-identification standard the hospital accepts | Hospital privacy officer / DPO | ⬜ not started |
| 3 | List of every lab that sends the hospital reports | Lab services / medical records | ⬜ not started |
| 4 | A safe storage location outside git, backed up, access-controlled | Project owner | ⬜ not started |
| 5 | Named person responsible for collecting and de-identifying | Project owner | ⬜ not started |

**Next action:** start blocker #1. It is the one with a multi-month lead time,
and everything else can proceed in parallel once it is moving.
