"""PRELIMINARY, FINAL, AMENDED — and what each does to a case. Phase 7.6.

Three rules, and each exists because of a specific way a hospital gets hurt:

* ``PRELIMINARY`` → **store, hold, set a stale timer.** A preliminary result is
  real information, so it is stored and shown; but it is not the end of the
  story, so the case stays open and a timer watches for the final that should
  follow. A preliminary that is never finalised is precisely the "lost result"
  this product exists to catch.

* ``FINAL`` supersedes a preliminary on the same order → link
  ``superseded_by_result_id``, **and do not alert twice.** The clinician has
  already been told. A second identical page at 2 a.m. is how a ward learns to
  ignore this system.

* ``AMENDED`` / ``CORRECTED`` → **re-open a closed case**, mark the flag as
  amendment-driven, and notify with **distinct wording**. An amendment is the
  one case where a closed case must come back, and the message must not read
  like the original — the recipient has to know something *changed*.

## The decision this module refuses to make

Supersession is keyed on the **order**, never on the patient or the analyte. Two
results for the same patient on the same day may be different tests, and
superseding across them would delete a finding nobody ever saw. If the order is
unknown, nothing is superseded and the case stays open — the same bias towards
the queue as everywhere else in Phase 7.
"""

from __future__ import annotations

import dataclasses

from app.services.extraction.parse import (
    STATUS_AMENDED,
    STATUS_FINAL,
    STATUS_PRELIMINARY,
)

ACTION_STORE_AND_HOLD = "store_and_hold"
ACTION_SUPERSEDE = "supersede"
ACTION_REOPEN = "reopen_amended"
ACTION_STORE_ONLY = "store_only"


@dataclasses.dataclass(frozen=True)
class StatusDecision:
    action: str
    #: Whether a notification should be sent for this arrival at all.
    notify: bool
    #: Whether the message must be worded as an amendment rather than a result.
    amended_wording: bool
    #: The result this one replaces, when it replaces one.
    supersedes_result_id: str | None
    #: Whether a stale timer should watch for the final report.
    set_stale_timer: bool
    reason: str


def decide(
    *,
    incoming_status: str | None,
    prior_status: str | None = None,
    prior_result_id: str | None = None,
    case_is_closed: bool = False,
    prior_was_notified: bool = False,
) -> StatusDecision:
    """What to do with an arriving result, given what came before it.

    ``incoming_status is None`` means the report carried **no stamp**, which is
    deliberately *not* treated as ``final``: an unstamped report has not told us
    it is complete, so it may not supersede anything. It is stored, and the case
    stays open.
    """
    # ── AMENDED — the only path that re-opens a closed case ───────────
    if incoming_status == STATUS_AMENDED:
        return StatusDecision(
            action=ACTION_REOPEN,
            notify=True,
            # ★ Distinct wording is not cosmetic. "Your patient has a result"
            # and "a result you were already told about has CHANGED" require
            # different actions from the reader.
            amended_wording=True,
            supersedes_result_id=prior_result_id,
            set_stale_timer=False,
            reason=(
                "This report is an amendment to one already issued."
                + (
                    " The case was closed and has been re-opened."
                    if case_is_closed
                    else ""
                )
            ),
        )

    # ── FINAL following a PRELIMINARY on the same order ────────────────
    if incoming_status == STATUS_FINAL and prior_status == STATUS_PRELIMINARY:
        return StatusDecision(
            action=ACTION_SUPERSEDE,
            # ★ Do not double-alert. The clinician was told when the
            # preliminary arrived; telling them again teaches them to stop
            # reading these messages.
            notify=not prior_was_notified,
            amended_wording=False,
            supersedes_result_id=prior_result_id,
            set_stale_timer=False,
            reason="Final report replaces the preliminary one for this order.",
        )

    # ── PRELIMINARY — real, but not the end ───────────────────────────
    if incoming_status == STATUS_PRELIMINARY:
        return StatusDecision(
            action=ACTION_STORE_AND_HOLD,
            notify=True,
            amended_wording=False,
            supersedes_result_id=None,
            # The whole point: something must watch for the final that should
            # follow, or a preliminary result quietly becomes the last word.
            set_stale_timer=True,
            reason="Preliminary report. The case stays open until the final arrives.",
        )

    if incoming_status == STATUS_FINAL:
        return StatusDecision(
            action=ACTION_STORE_ONLY,
            notify=True,
            amended_wording=False,
            supersedes_result_id=None,
            set_stale_timer=False,
            reason="Final report.",
        )

    # ── no stamp at all ───────────────────────────────────────────────
    return StatusDecision(
        action=ACTION_STORE_AND_HOLD,
        notify=True,
        amended_wording=False,
        supersedes_result_id=None,
        # Treated like a preliminary, because that is the safe reading of "the
        # report did not say it was complete".
        set_stale_timer=True,
        reason=(
            "This report carries no status stamp, so it is treated as "
            "provisional and the case stays open."
        ),
    )
