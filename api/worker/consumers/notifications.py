"""The notification consumer. Phase 4.3.

Phase 2.3 has been enqueueing notification *intents* onto the ``notifications``
queue since lab flags shipped, and Phase 3.6 added one more when an amended
result reopens a case. Nothing has consumed them — deliberately, because a stub
consumer would have **deleted** them, and an unconsumed queue is the correct
state for a phase that has not been built. This is the handler that finally
drains it.

An intent says *who should be told what*. It does not say through which
channel, because that is policy: quiet hours, rate caps and the patient rules
all live in ``services/notifications.py`` and are applied here on the way out.

**Idempotency comes from the database, not the queue.** ``notifications`` is
unique on ``dedupe_key``, built from the case, template, channel, recipient and
rung. A message delivered three times produces one row and one send.

Handler and ``pgmq.delete`` share one transaction (``worker/consumer.py``), so
the notification and the acknowledgement commit together.

**No NODE B.**
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import notifications as notify

log = structlog.get_logger(__name__)

# Phase 2.3 / 3.6 intents name an audience rather than a channel. This is the
# mapping from that vocabulary to how the person is actually reached.
CHANNELS_FOR_AUDIENCE = {
    "responsible_doctor": ("in_app", "email"),
    "unit_head": ("in_app", "email"),
    "lab_department": ("in_app",),
    "patient": ("sms",),
}
DEFAULT_CHANNELS = ("in_app",)

# Intents addressed to a person rather than to a case.
_CASELESS_TEMPLATES = ("follow_up_digest", "roster_weekly_reminder")


async def handle_notification(session: AsyncSession, message: dict[str, Any]) -> None:
    """Entry point for the ``notifications`` queue."""
    raw_case_id = message.get("case_id")
    template_key = message.get("template_key")

    # A digest and a roster reminder span cases (or none at all), so they are
    # addressed to a person rather than to a case. Both still need a template.
    if not raw_case_id and template_key in _CASELESS_TEMPLATES:
        await _dispatch_caseless(session, message, str(template_key))
        return

    if not raw_case_id or not template_key:
        # No case or no template means nothing can be rendered or attributed.
        # Dropping is better than guessing: a wrong case_id would tell a
        # doctor about somebody else's patient.
        log.warning("notification_message_incomplete", message=message)
        return

    try:
        case_id = uuid.UUID(str(raw_case_id))
    except ValueError:
        log.warning("notification_message_bad_case_id", case_id=raw_case_id)
        return

    audience = str(message.get("audience") or "")
    user_id = _as_uuid(message.get("user_id"))
    patient_id = _as_uuid(message.get("patient_id"))

    # An intent that names an audience but no specific user still has to reach
    # someone. Resolving here rather than at enqueue time is deliberate: the
    # right person may have changed between the flag and the send.
    if user_id is None and patient_id is None and audience in ("responsible_doctor",):
        user_id = await _current_owner(session, case_id)

    severity = message.get("severity") or await _case_severity(session, case_id)

    # Phase 4.5's digest arrives as an intent naming only the doctor; the
    # cases are read here so the message reflects the queue at send time
    # rather than at 01:30 when the job ran.
    context = dict(message)
    if message.get("digest") and user_id is not None:
        from app.services.notification_policy import open_follow_up_digest

        cases = await open_follow_up_digest(session, user_id)
        context["cases"] = cases
        context["case_count"] = len(cases)
        context["owner_name"] = await _user_name(session, user_id)
        if not cases:
            # Everything was acknowledged between the job and the send. An
            # empty digest is worse than none: it trains people to ignore it.
            log.info("digest_skipped_empty", user_id=str(user_id))
            return

    channels = CHANNELS_FOR_AUDIENCE.get(audience, DEFAULT_CHANNELS)
    for channel in channels:
        outcome = await notify.dispatch(
            session,
            case_id=case_id,
            template_key=str(template_key),
            channel=channel,
            user_id=user_id,
            patient_id=patient_id,
            context=context,
            severity=str(severity) if severity else None,
            escalation_level=_as_int(message.get("escalation_level")),
        )
        log.info(
            "notification_intent_handled",
            case_id=str(case_id),
            template_key=template_key,
            channel=outcome.channel,
            status=outcome.status,
            suppression_reason=outcome.suppression_reason,
            created=outcome.created,
        )


def _as_uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


async def _current_owner(session: AsyncSession, case_id: uuid.UUID) -> uuid.UUID | None:
    row = (
        await session.execute(
            text("SELECT current_owner_id FROM pending_cases WHERE id = :c"),
            {"c": str(case_id)},
        )
    ).first()
    return _as_uuid(row.current_owner_id) if row else None


async def _case_severity(session: AsyncSession, case_id: uuid.UUID) -> str | None:
    row = (
        await session.execute(
            text("SELECT severity FROM pending_cases WHERE id = :c"),
            {"c": str(case_id)},
        )
    ).first()
    return str(row.severity) if row and row.severity else None


async def _user_name(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    row = (
        await session.execute(
            text("SELECT full_name FROM users WHERE id = :u"), {"u": str(user_id)}
        )
    ).first()
    return str(row.full_name) if row else None


async def _dispatch_caseless(
    session: AsyncSession, message: dict[str, Any], template_key: str
) -> None:
    """A digest or a roster reminder: addressed to a person, not a case.

    Deduped on the recipient, the template and the **day** rather than a case
    id, so a redelivered wake-up cannot send a doctor two digests in one
    morning — and tomorrow's still goes out.
    """
    user_id = _as_uuid(message.get("user_id"))
    if user_id is None:
        log.warning("caseless_notification_without_recipient", message=message)
        return

    context = dict(message)
    context["owner_name"] = await _user_name(session, user_id)

    if template_key == "follow_up_digest":
        from app.services.notification_policy import open_follow_up_digest

        cases = await open_follow_up_digest(session, user_id)
        if not cases:
            # Acknowledged between the job running and this send. An empty
            # digest trains people to ignore the next one.
            log.info("digest_skipped_empty", user_id=str(user_id))
            return
        context["cases"] = cases
        context["case_count"] = len(cases)

    day = dt.datetime.now(dt.UTC).date().isoformat()
    outcome = await notify.dispatch(
        session,
        case_id=None,
        template_key=template_key,
        channel="email",
        user_id=user_id,
        context=context,
        # Not tied to one case's severity, and never urgent enough to bypass
        # quiet hours: a digest that woke somebody would defeat its purpose.
        severity=None,
        dedupe_override=f"{user_id}:{template_key}:{day}",
    )
    log.info(
        "caseless_notification_handled",
        template_key=template_key,
        status=outcome.status,
        user_id=str(user_id),
    )
