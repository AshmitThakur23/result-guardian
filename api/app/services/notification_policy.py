"""Alert-fatigue controls. Phase 4.5 ★

    **The #1 killer of clinical alert systems.** Build these in the same sprint
    as the ladder, not later.

Four controls, and one rule that overrides all of them:

* **Quiet hours** — default 22:00-07:00 IST. FOLLOW_UP batches to the next
  window. **CRITICAL always sends immediately.**
* **Digest** — one email per doctor per morning listing all open FOLLOW_UP
  flags, not one each.
* **Dedup / grouping** — multiple analytes in one report is **one**
  notification, not twelve.
* **Rate cap** — max N SMS per doctor per hour; overflow rolls into the digest.

The override, stated once and enforced in one place: **a CRITICAL notification
is never quiet-houred, never rate-capped and never batched.** Everything here
that could delay a message checks severity first. Getting that wrong is not an
annoyance — it is the product failing at the only moment it matters.

Every threshold lives in ``rule_config``, the key/value table Phase 3.2 built
for exactly this ("configuration lives in tables, never in code"). A hospital
that finds 22:00 too early changes a row.

**Suppression is recorded, never silent.** A notification the system chose not
to send becomes a row with ``status = 'suppressed'`` and a reason. "We decided
not to" and "we failed to" are different facts and an auditor must be able to
tell them apart.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rules import SEVERITY_CRITICAL

# IST. The hospital is in India and quiet hours are a wall-clock concept for
# the person being woken up, not a UTC one.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

DEFAULT_QUIET_START_HOUR = 22
DEFAULT_QUIET_END_HOUR = 7
DEFAULT_SMS_PER_HOUR = 6
DEFAULT_DIGEST_HOUR = 7

CONFIG_QUIET_HOURS = "notification_quiet_hours"
CONFIG_SMS_RATE_CAP = "notification_sms_rate_cap"
CONFIG_DIGEST = "notification_digest"

# Channels a quiet-hours deferral or a rate cap can meaningfully apply to. An
# in-app row does not wake anybody, so it is never deferred -- the dashboard
# should be correct at 3am even if nobody is looking.
INTRUSIVE_CHANNELS = ("sms", "whatsapp", "email")
RATE_CAPPED_CHANNELS = ("sms", "whatsapp")


@dataclass(frozen=True)
class QuietHours:
    start_hour: int
    end_hour: int

    def covers(self, at: dt.datetime) -> bool:
        """Is ``at`` inside the quiet window, in IST?"""
        hour = at.astimezone(IST).hour
        if self.start_hour == self.end_hour:
            return False
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        # Wraps midnight, which is the normal case for 22:00-07:00.
        return hour >= self.start_hour or hour < self.end_hour

    def next_window_after(self, at: dt.datetime) -> dt.datetime:
        """When the quiet window ends — where a deferred message goes."""
        local = at.astimezone(IST)
        candidate = local.replace(hour=self.end_hour, minute=0, second=0, microsecond=0)
        if candidate <= local:
            candidate += dt.timedelta(days=1)
        return candidate.astimezone(dt.UTC)


async def _config(
    session: AsyncSession, key: str, default: dict[str, object]
) -> dict[str, object]:
    row = (
        await session.execute(
            text("SELECT value FROM rule_config WHERE key = :k AND deleted_at IS NULL"),
            {"k": key},
        )
    ).first()
    if row is None or not isinstance(row.value, dict):
        return default
    merged = dict(default)
    merged.update(row.value)
    return merged


async def quiet_hours(session: AsyncSession) -> QuietHours:
    config = await _config(
        session,
        CONFIG_QUIET_HOURS,
        {"start_hour": DEFAULT_QUIET_START_HOUR, "end_hour": DEFAULT_QUIET_END_HOUR},
    )
    try:
        return QuietHours(
            start_hour=int(str(config["start_hour"])),
            end_hour=int(str(config["end_hour"])),
        )
    except (TypeError, ValueError):
        return QuietHours(DEFAULT_QUIET_START_HOUR, DEFAULT_QUIET_END_HOUR)


async def sms_rate_cap(session: AsyncSession) -> int:
    config = await _config(
        session, CONFIG_SMS_RATE_CAP, {"per_user_per_hour": DEFAULT_SMS_PER_HOUR}
    )
    try:
        return max(0, int(str(config["per_user_per_hour"])))
    except (TypeError, ValueError):
        return DEFAULT_SMS_PER_HOUR


@dataclass(frozen=True)
class PolicyVerdict:
    """What the fatigue controls decided about one pending notification."""

    send: bool
    suppression_reason: str | None = None
    defer_until: dt.datetime | None = None
    note: str | None = None

    @classmethod
    def allow(cls) -> PolicyVerdict:
        return cls(send=True)

    @classmethod
    def suppress(cls, reason: str, note: str | None = None) -> PolicyVerdict:
        return cls(send=False, suppression_reason=reason, note=note)

    @classmethod
    def defer(cls, until: dt.datetime, reason: str) -> PolicyVerdict:
        return cls(send=False, suppression_reason=reason, defer_until=until)


async def evaluate(
    session: AsyncSession,
    *,
    severity: str | None,
    channel: str,
    user_id: uuid.UUID | None,
    at: dt.datetime,
) -> PolicyVerdict:
    """Should this notification go out now?

    **CRITICAL short-circuits everything.** The plan says *"CRITICAL always
    sends immediately"* about quiet hours specifically, and the same reasoning
    applies to the rate cap: a doctor who has already had six texts this hour
    is exactly the doctor with a lot going on, and the seventh being the
    critical one is not a coincidence worth gambling on.
    """
    if severity == SEVERITY_CRITICAL:
        return PolicyVerdict.allow()

    # ── quiet hours ───────────────────────────────────────────────
    if channel in INTRUSIVE_CHANNELS:
        window = await quiet_hours(session)
        if window.covers(at):
            return PolicyVerdict.defer(
                window.next_window_after(at), reason="quiet_hours"
            )

    # ── rate cap ──────────────────────────────────────────────────
    if channel in RATE_CAPPED_CHANNELS and user_id is not None:
        cap = await sms_rate_cap(session)
        if cap == 0:
            return PolicyVerdict.suppress("rate_capped", "rate cap is zero")
        recent = (
            await session.execute(
                text(
                    "SELECT count(*) AS n FROM notifications "
                    " WHERE user_id = :u AND channel = :ch "
                    "   AND status IN ('sent', 'delivered') "
                    "   AND sent_at >= :since AND deleted_at IS NULL"
                ),
                {
                    "u": str(user_id),
                    "ch": channel,
                    "since": at - dt.timedelta(hours=1),
                },
            )
        ).one()
        if int(recent.n) >= cap:
            # "overflow rolls into digest" -- the case stays open and the
            # morning digest lists it, so nothing is lost by capping.
            return PolicyVerdict.suppress(
                "rate_capped",
                f"{recent.n} {channel} already sent to this user in the last hour "
                f"(cap {cap}); this one rolls into the morning digest",
            )

    return PolicyVerdict.allow()


async def open_follow_up_digest(
    session: AsyncSession, user_id: uuid.UUID, *, at: dt.datetime | None = None
) -> list[dict[str, object]]:
    """*"One email per doctor per morning listing all open FOLLOW_UP flags, not
    one each."*

    Reads the cases, not the notifications: a flag that was rate-capped or
    quiet-houred still appears, which is what makes the digest the safety net
    for everything the fatigue controls held back.
    """
    moment = at or dt.datetime.now(dt.UTC)
    rows = (
        await session.execute(
            text(
                "SELECT pc.id, pc.flagged_at, p.name AS patient_name, p.mrn, "
                "       o.test_name "
                "  FROM pending_cases pc "
                "  JOIN patients p ON p.id = pc.patient_id "
                "  JOIN orders o ON o.id = pc.order_id "
                " WHERE pc.current_owner_id = :u "
                "   AND pc.severity = 'follow_up' "
                "   AND pc.state = 'flagged' "
                "   AND pc.deleted_at IS NULL "
                " ORDER BY pc.flagged_at"
            ),
            {"u": str(user_id)},
        )
    ).all()
    return [
        {
            "case_id": str(r.id),
            "patient_name": r.patient_name,
            "mrn": r.mrn,
            "test_name": r.test_name,
            "flagged_label": (
                r.flagged_at.astimezone(IST).strftime("%d %b %H:%M")
                if r.flagged_at
                else "unknown"
            ),
            "age_hours": (
                round((moment - r.flagged_at).total_seconds() / 3600, 1)
                if r.flagged_at
                else None
            ),
        }
        for r in rows
    ]


async def flag_rate_per_100_discharges(
    session: AsyncSession, *, since: dt.datetime | None = None
) -> dict[str, object]:
    """Phase 4.5's metric, and the plan puts a number on it:

        **Metric on the admin dashboard: flag rate per 100 discharges.** If
        this exceeds **~15%** you must retune thresholds before the pilot
        expands. **Put this number on the wall.**

    Phase 4 ships the measurement; Phase 5.4 renders it. Shipping the number
    now means the dashboard is a rendering job rather than a rendering *and*
    measurement job — the same split Phase 2.3 used for lab-flag metrics.
    """
    window = since or (dt.datetime.now(dt.UTC) - dt.timedelta(days=30))
    row = (
        await session.execute(
            text(
                "SELECT "
                "  count(DISTINCT e.id) AS discharges, "
                "  count(DISTINCT pc.id) FILTER (WHERE pc.severity IN "
                "        ('follow_up', 'critical')) AS flagged_cases, "
                "  count(DISTINCT pc.id) FILTER (WHERE pc.severity = 'critical') "
                "        AS critical_cases "
                "  FROM encounters e "
                "  LEFT JOIN pending_cases pc ON pc.encounter_id = e.id "
                "       AND pc.deleted_at IS NULL "
                " WHERE e.discharged_at >= :since AND e.deleted_at IS NULL"
            ),
            {"since": window},
        )
    ).one()

    discharges = int(row.discharges)
    flagged = int(row.flagged_cases)
    rate = (flagged * 100.0 / discharges) if discharges else 0.0
    return {
        "since": window.isoformat(),
        "discharges": discharges,
        "flagged_cases": flagged,
        "critical_cases": int(row.critical_cases),
        "flags_per_100_discharges": round(rate, 2),
        # The plan's own threshold, carried with the number so a dashboard
        # cannot render the figure without the line it must not cross.
        "retune_threshold_per_100": 15.0,
        "exceeds_retune_threshold": rate > 15.0,
    }
