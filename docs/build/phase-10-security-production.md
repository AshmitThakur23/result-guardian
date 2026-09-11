# Phase 10 — Security, Compliance, Production

**Goal:** safe and legal to run on real patients.
**Duration:** 3 weeks **plus external test turnaround** · **Nodes:** A and B · **Depends on NODE B?** No

---

## 📍 STATUS SUMMARY — Phase 10

> ⚠️ **Do not start this phase until Exit Gate 9 passes.**

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 10.1 Security | A + B | ⬜ not started |  |
| 10.2 Privacy / DPDP | A + B | ⬜ not started |  |
| 10.3 NABH alignment | A + B | ⬜ not started |  |
| 10.4 Reliability | A + B | ⬜ not started |  |
| 10.5 Rollout | A + B | ⬜ not started |  |
| **Exit Gate 10** | A + B | ⬜ **not started** | |

---

## 10.1 Security

- [ ] TLS everywhere via Caddy; internal service traffic on a **private Docker network only**
- [ ] **NODE A ⇄ NODE B:** dedicated VLAN or physically separate network segment; NODE B firewall accepts **11434 from NODE A's IP only**; consider mTLS or an SSH tunnel if the segment is shared
- [ ] Postgres: dedicated app role with **least privilege**; no superuser from the app; `pgcrypto` for any column-level needs
- [ ] **Disk encryption (LUKS) on both servers** — the hospital's IT must confirm
- [ ] Secrets in Docker secrets or a **600 env file owned by root**; rotated; **never in git**
- [ ] Dependency scanning: `pip-audit`, `npm audit`, Trivy on images **in CI**
- [ ] Security headers: HSTS, CSP, X-Frame-Options, no-referrer
- [ ] Session timeout, concurrent session limit, logout everywhere
- [ ] **Penetration test by an external firm**; remediate all high/critical **before go-live**

## 10.2 Privacy / DPDP Act 2023

- [ ] **Data inventory:** every table holding personal data, purpose, retention period
- [ ] Explicit statement that **NODE B holds no personal data** — include the prompt-construction code path in the inventory **as evidence**
- [ ] Retention policy: clinical records per hospital policy; documents archived; logs **1 year**
- [ ] Erasure workflow for non-clinical data (patient contact preferences)
- [ ] Consent record for patient SMS notification, captured at admission (schema in 1.1)
- [ ] Notice text for patients explaining the tracking system
- [ ] Breach response plan with the **72-hour** notification path
- [ ] **Confirm on-prem processing in writing** — no data leaves the hospital network. **Verify with egress firewall rules, not trust. Include NODE B in the egress rules.**
- [ ] DPO / grievance officer contact published in the app footer

## 10.3 NABH alignment

- [ ] Map features to **AAC.12** and **AAC.6.g** in `docs/nabh-mapping.md`
- [ ] Monthly report template the quality team can submit directly
- [ ] SOP document: *"Management of post-discharge pending investigations"* — the hospital must adopt this **formally**, including **who maintains the duty roster**
- [ ] **Medico-legal policy ★** — written agreement on liability: **who is accountable when the system flags and no one acts.** Get this signed **before go-live, not after an incident.** This is organisational, not technical, and **it takes longer than you expect.**

## 10.4 Reliability

- [ ] Backups: nightly `pg_dump` + weekly base backup + WAL archiving; **restore tested monthly** (an untested backup is not a backup)
- [ ] Document storage backed up separately
- [ ] **NODE B needs no backup — it holds nothing.** Back up the provisioning script instead, and confirm a full rebuild takes **under 15 minutes**
- [ ] Monitoring: Prometheus + Grafana dashboards for API latency, queue depth, worker lag, timer backlog, notification failure rate, DB connections, disk usage, **NODE B reachability and generation latency, LLM degradation event rate**
- [ ] Alert rules → email/SMS to the IT team: worker down >5 min, queue depth >1000, timer overdue 30 min, disk >85%. **NODE B unreachable is a low-priority alert — it is not an outage.**

### Degraded-mode test ★ — all six must leave tracking and escalation working

- [ ] 1. Stop Ollama on NODE B
- [ ] 2. Physically cut the LAN between NODE A and NODE B
- [ ] 3. Stop the OCR worker
- [ ] 4. Block the SMS provider
- [ ] 5. Disconnect the HIS
- [ ] 6. Reboot NODE A mid-timer-window

- [ ] Load test with Locust at **3× peak** (e.g. 500 discharges/day, 2000 results/day)
- [ ] Server sizing recorded for both nodes: CPU, RAM, disk, GPU/VRAM, UPS, network

## 10.5 Rollout

- [ ] Training: 1-hour doctor session, 2-hour lab/admin session, **printed quick-reference card**
- [ ] Pilot: **one department, 4 weeks**, daily standup with the unit head
- [ ] Success metrics **defined before the pilot**: % pending results closed within SLA, missed-result count vs baseline, doctor satisfaction, flag precision
- [ ] Feedback loop: weekly threshold tuning during the pilot
- [ ] **Expand department by department, never hospital-wide on day one**
- [ ] Support process: who to call, response times, bug triage

---

## ✅ EXIT GATE 10

- [ ] Pilot completes with **measurable reduction in missed results**
- [ ] **No P1 incidents**
- [ ] Restore test passed
- [ ] Pen test remediated
- [ ] **Medico-legal policy signed**
- [ ] **All six degraded-mode tests passed**

---

**Cross-ref:** [architecture/05-degradation-security-ai-boundary.md](../architecture/05-degradation-security-ai-boundary.md)
