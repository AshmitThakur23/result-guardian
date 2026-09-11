# ADR 0003 — OPD is out of scope for v1, but the schema stays ready

**Status:** Accepted · **Date:** 2026-09-11 · **Phase:** 1 (scope note)

## Context

The build plan forces this decision in Phase 1 and refuses to let it be deferred, because it changes the encounter model:

> OPD pending results are the same failure mode at higher volume, with no discharge event to hang the gate on. Decide explicitly now: in scope (the trigger becomes "visit closed") or out of scope for v1. **Do not leave it undecided — it changes the encounter model.**

An outpatient whose culture comes back resistant three days after their visit is in exactly the same danger as a discharged inpatient. The difference is purely mechanical: there is no discharge click to intercept.

## Decision

**OPD is out of scope for v1. The schema stays ready for it.**

1. `encounters.type` keeps `opd` as a valid value from the first migration. No later migration is needed to admit OPD — the model is not foreclosed.
2. The **discharge gate binds to `ipd | emergency | daycare`** only. These have a real discharge event.
3. OPD orders may still be recorded; they simply do not create a `pending_case` through the gate.
4. Revisit after the Phase 10.5 pilot, with real flag-rate data in hand.

## Rationale

**There is no reliable trigger.** Making OPD work means the gate fires on "visit closed". Hospitals record visit closure inconsistently or not at all — an OPD visit often just ends. A gate wired to an event that does not reliably happen is a gate that does not reliably fire, and a safety feature that silently does not fire is worse than an absent one, because it is trusted.

**Volume would kill the pilot before thresholds are tuned.** Phase 4.5 puts a ceiling on alert fatigue: flag rate per 100 discharges above ~15% means retune before expanding. OPD volume is multiples of IPD. Turning it on before thresholds are validated against real hospital data would blow through that ceiling in week one, and doctors who learn to ignore flags do not un-learn it. Alert fatigue is named in the plan as "the #1 killer of clinical alert systems."

**The pilot is one department for four weeks** (Phase 10.5). A bounded, high-signal IPD pilot produces the threshold data that makes an OPD rollout safe later. The reverse order does not work.

## Why this is safe rather than negligent

An OPD result is **not** silently dropped. Phase 7.5 already specifies:

> Result arrives with no pending case (never-discharged patient, OPD) → create an **orphan case** linked to the encounter.

So an OPD result entering through ingestion lands in the orphan/unmatched queue and is visible to a human. What v1 does not do is *guarantee ownership in advance* for OPD — which is the thing the discharge gate provides and which OPD structurally cannot support yet.

This distinction must be stated plainly in the Phase 10.3 SOP: **v1 guarantees post-discharge inpatient results. OPD results are surfaced, not guaranteed.** Claiming otherwise to a hospital would be a misrepresentation of the safety property.

## Consequences

- Phase 1 schema work is unblocked and may begin.
- `discharge-readiness` and the gate check `encounter.type IN ('ipd','emergency','daycare')`.
- The Phase 10.3 SOP and any NABH submission must carry the scope statement above.
- Admitting OPD later is a *behaviour* change (a new trigger), not a *schema* change — which is precisely why `opd` stays in the enum today.

## Reversal condition

Revisit if the pilot shows flag rate comfortably under the 15% ceiling **and** the hospital can demonstrate a reliably recorded visit-closure event to bind the trigger to. Both conditions, not either.
