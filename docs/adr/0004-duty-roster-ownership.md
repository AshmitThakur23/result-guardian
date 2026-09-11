# ADR 0004 — The unit head maintains `duty_roster`, weekly

**Status:** Accepted · **Date:** 2026-09-11 · **Phase:** 4.1 · **Decided by:** Claude, delegated by the user

## Context

The build plan forces this decision and states the stakes bluntly:

> Decide who maintains this. **A roster nobody updates is worse than no roster**, because the system will confidently notify someone who left. Options: HR sync (Phase 9.4), unit-head weekly entry, or admin upload. Pick one and write it in the SOP.

`duty_roster` is load-bearing. `resolve_owner` falls through to it at steps 3 and 4 whenever the contracted doctor is unavailable. If it is stale, a CRITICAL flag gets routed to someone who is on leave, has resigned, or is off shift — and the escalation ladder keeps ticking against a person who will never acknowledge.

## Decision

**The unit head of each department maintains their department's roster, weekly.**

HR/AD sync (Phase 9.4) is layered in later for **user identity** — who exists, who resigned — but **not** for shift data.

## Rationale

**HR sync cannot be the v1 answer, for a timing reason.** Phase 9.4 is after MVP. Phase 4 needs a working roster at MVP. Choosing HR sync means Phase 4 ships with no roster at all, which is the "worse than no roster" outcome the plan warns about. It is not the right answer arriving late; it is the wrong answer for v1.

**Admin upload centralises the job onto the wrong person.** A hospital administrator has no visibility into which registrar swapped a night shift. They will upload once, it will drift, and nothing will signal the drift.

**The unit head has skin in the game.** This is the decisive argument. The unit head is **rung 2 of the escalation ladder** (Phase 4.4). When a flag is routed to an absent doctor and goes unacknowledged, it escalates — to the unit head. The person who suffers the consequence of a stale roster is the person maintaining it. Every other option separates the cost from the control, and maintenance tasks whose neglect lands on someone else are always neglected.

They are also the only person who actually knows the answer.

## Guards to implement alongside it

A decision about who maintains data is worthless without staleness detection. Ship these in the same sprint as Phase 4.1:

1. **Weekly reminder** — notification to each unit head to confirm or update the coming week's roster. Confirming an unchanged roster is one click; it must not require re-entry.
2. **Graceful fallthrough** — `resolve_owner` step 5 already targets the department unit head when the roster yields nobody. A stale roster therefore degrades to "the unit head gets it" rather than to silence. This is the degradation ladder applied to a data-quality failure.
3. **Roster-stale badge** — the admin and unit-head dashboards show a warning when a department has no active shift row covering `now()`.
4. **Metric** — count of owner resolutions that fell through to step 5 or 6, per department. A rising number is the measurable signature of a roster going stale, and it belongs next to the flag-rate metric in Phase 5.6.

## Consequences

- The Phase 10.3 SOP — *"Management of post-discharge pending investigations"* — must name the unit head as the roster owner. The plan explicitly requires the roster-maintenance owner to appear in the SOP.
- Unit-head training (Phase 10.5) covers roster upkeep, not just flag acknowledgement.
- Phase 9.4's HR/AD sync populates and deactivates `users` and may propose roster rows, but **the unit head's entry remains authoritative for shift data**. An HR system knows employment status; it does not know tonight's on-call swap.

## Reversal condition

Revisit if the hospital turns out to run a genuinely maintained electronic duty roster in their HIS. Then Phase 9.4 syncs from it and this ADR is superseded — but only after verifying it is actually current, not merely present.
