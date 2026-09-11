# Phase 9 — Hospital Integration

**Goal:** orders and results flow in automatically; closure flows back.
**Duration:** 3–5 weeks, **largely gated by hospital IT and the HIS vendor** · **Node:** A only · **Depends on NODE B?** No

> ⛔ **BLOCKING PREREQUISITE:** vendor conversations started in **Phase 1.7**.

---

## 📍 STATUS SUMMARY — Phase 9

> ⚠️ **Do not start this phase until Exit Gate 8 passes.**

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 9.1 Discovery — on paper | A | 🔴 blocked | needs the vendor conversation from 1.7 |
| 9.2 HL7 v2 inbound | A | ⬜ not started |  |
| 9.3 FHIR R4 | A | ⬜ not started |  |
| 9.4 Master data sync | A | ⬜ not started |  |
| 9.5 Resilience | A | ⬜ not started |  |
| **Exit Gate 9** | A | ⬜ **not started** | |

---

## 9.1 Discovery — do this first, on paper

- [ ] Which HIS/LIS? Which version? Which vendor contact?
- [ ] Which interfaces exist: HL7 v2 (which version), FHIR R4, proprietary REST, or database views?
- [ ] Can they push, or must you poll?
- [ ] **Get real sample messages — not the documentation's examples**
- [ ] Test environment access and network path (VLAN, firewall, static IPs)
- [ ] Write `docs/integration-spec.md` **and get the vendor to sign it**

## 9.2 HL7 v2 inbound

- [ ] MLLP listener (asyncio TCP, `\x0b … \x1c\x0d` framing), configurable port, **IP allowlist**
- [ ] Parse with hl7apy: `ORM^O01` (orders), `ORU^R01` (results), `ADT^A01/A03/A08` (admit/discharge/update)
- [ ] **`hl7_messages`** table — raw message stored **verbatim, forever**
- [ ] Map segments: PID → `patients`, PV1 → `encounters`, ORC/OBR → `orders`, OBX → `result_analytes`, NTE → narratives
- [ ] Handle **Z-segments** — every hospital has custom ones
- [ ] Send ACK / NACK correctly — **a missing ACK makes the sender retry forever**
- [ ] Sequence gap detection on MSH-10 / control IDs

## 9.3 FHIR R4

- [ ] Inbound: poll or subscribe to `ServiceRequest`, `DiagnosticReport`, `Observation`
- [ ] Outbound write-back on closure: update `Task` status, post an `Observation` note, or `DiagnosticReport.conclusion` — whichever the vendor supports
- [ ] OAuth2 client credentials or mTLS as the vendor requires
- [ ] **`fhir_sync_log`** with request/response bodies for the first **90 days** of operation

## 9.4 Master data sync

- [ ] Nightly patient demographics sync — **phone numbers especially, the Phase 4 SMS depends on this**
- [ ] Doctor/user sync from HR or AD — this can also feed `duty_roster` (see 4.1)
- [ ] Department and ward mapping table
- [ ] Test catalogue sync → auto-populate `test_synonyms`

## 9.5 Resilience

- [ ] **Reconciliation job:** nightly compare HIS orders vs local orders for the last 7 days, report gaps
- [ ] **Replay tool:** reprocess stored raw messages after a bug fix
- [ ] **Downtime buffer:** if the HIS is unreachable, queue outbound writes, retry for **72h**
- [ ] Duplicate detection on message control ID
- [ ] Kill switch **per integration channel** in admin settings

---

## ✅ EXIT GATE 9

End-to-end in the hospital's test environment:

- [ ] Order created in HIS appears locally
- [ ] Result released in LIS flows in, classifies and flags
- [ ] Acknowledgement writes back and is visible in the HIS
- [ ] Reconciliation reports **zero gaps over 7 days**

---

**Cross-ref:** [architecture/05-degradation-security-ai-boundary.md](../architecture/05-degradation-security-ai-boundary.md) (Hospital integration)
