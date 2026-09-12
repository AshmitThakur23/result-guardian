# Task 1.7 — HIS / LIS integration discovery

**Status: 🔴 NOT STARTED — no vendor contact made. Blocker for Phase 9.**
**Owner: human. No answer below may be filled in by guessing.**
**Opened:** 2026-09-12 (Phase 1, week one, as the build plan requires)

---

## Why this exists now, in Phase 1

> Phase 9 is gated by hospital IT and the HIS vendor, **not by your code**.
> Open the conversation in week one of Phase 1.

Phase 9 is ~20 weeks out. Vendor scheduling, test-environment provisioning and
network approvals are measured in months, so the conversation starts now even
though no integration code will be written for a long time.

⚠️ **Every answer below must come from the vendor or hospital IT.** An assumed
answer is worse than a blank one: it produces an integration spec that looks
complete, gets signed off, and is wrong. Leave `— UNANSWERED —` in place until
someone has actually said otherwise.

Phase 9.1 requires this document to be **signed by the vendor** before
integration work begins.

---

## A. Identify the systems

| Question | Answer |
|---|---|
| Which HIS? Vendor, product, version | — UNANSWERED — |
| Which LIS? Vendor, product, version | — UNANSWERED — |
| Are radiology / pathology / micro on the same LIS or separate? | — UNANSWERED — |
| Vendor technical contact (name, email, phone) | — UNANSWERED — |
| Hospital IT contact | — UNANSWERED — |
| Is there a support/change-request process, and what is the lead time? | — UNANSWERED — |

## B. Available interfaces

| Question | Answer |
|---|---|
| HL7 v2? Which version (2.3 / 2.5.1 / other)? | — UNANSWERED — |
| Which message types: `ORM^O01`, `ORU^R01`, `ADT^A01/A03/A08`? | — UNANSWERED — |
| FHIR R4? Which resources exposed? | — UNANSWERED — |
| Proprietary REST API? Database views? | — UNANSWERED — |
| **Can they push to us, or must we poll?** | — UNANSWERED — |
| If push: MLLP over TCP? What source IP will it come from? | — UNANSWERED — |
| Are there custom Z-segments? (Every hospital has some.) | — UNANSWERED — |

## C. Identifiers — the part that decides Phase 7.5

Phase 7.5's matching is arithmetic on these fields, and wrong-patient matching
is the one failure mode required to be **zero**. Ambiguity here is dangerous.

| Question | Answer |
|---|---|
| Patient identifier: is MRN globally unique and stable for life? | — UNANSWERED — |
| Does MRN ever get reissued, merged, or change after a merge? | — UNANSWERED — |
| Encounter/visit identifier: format and uniqueness | — UNANSWERED — |
| **Order identifier / accession number**: format, and is it on the printed report? | — UNANSWERED — |
| Does the accession number appear in the HL7 message *and* the PDF identically? | — UNANSWERED — |
| Test coding: LOINC, local codes, or both? | — UNANSWERED — |
| If local codes: can we get the full test catalogue as a file? | — UNANSWERED — |

## D. Result behaviour

| Question | Answer |
|---|---|
| How is a **preliminary** result distinguished from a **final** one? | — UNANSWERED — |
| How is an **amended / corrected** result signalled? | — UNANSWERED — |
| Does an amendment reuse the original accession number? | — UNANSWERED — |
| Are cancelled or rejected samples communicated? How? | — UNANSWERED — |
| Are culture results sent incrementally as sensitivities come in? | — UNANSWERED — |
| How are critical values currently communicated, if at all? | — UNANSWERED — |

## E. Write-back — closing the loop

| Question | Answer |
|---|---|
| Can we write acknowledgement back? Via FHIR `Task`, `Observation`, or not at all? | — UNANSWERED — |
| What authentication: OAuth2 client credentials, mTLS, API key? | — UNANSWERED — |
| Is write-back permitted by hospital policy, separate from technical capability? | — UNANSWERED — |

## F. Environment and network

| Question | Answer |
|---|---|
| Is there a **test environment**, and can we have access? | — UNANSWERED — |
| Network path: VLAN, firewall rules, static IPs required? | — UNANSWERED — |
| **Real sample messages** — not the documentation's examples | ⬜ not received |
| Expected message volume per day (peak and average) | — UNANSWERED — |
| Any maintenance windows or downtime patterns we must tolerate? | — UNANSWERED — |

---

## Blockers

| # | Blocker | Needed from | Status |
|---|---|---|---|
| 1 | Identify and make contact with the HIS/LIS vendor | Hospital IT | ⬜ not started |
| 2 | Obtain real sample messages, not documentation examples | Vendor | ⬜ not started |
| 3 | Test environment access + network path | Vendor + hospital IT | ⬜ not started |
| 4 | Vendor signature on this spec once completed | Vendor | ⬜ not started |

**Next action:** blocker #1 — ask hospital IT which HIS and LIS are in use and
who the vendor contact is. Sections A and C are the highest value: A determines
whether integration is even possible, and C determines whether Phase 7.5 can
match safely.
