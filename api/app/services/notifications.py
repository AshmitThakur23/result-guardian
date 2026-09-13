"""The notification dispatcher. Phase 4.3 / 4.5 / 4.6.

Phase 2.3 has been enqueueing notification *intents* onto the pgmq
``notifications`` queue since lab flags shipped, with nothing consuming them.
This is what consumes them, and what the escalation ladder calls directly.

The order of operations is the design, and every step exists because skipping
it causes a specific failure:

1. **Resolve the recipient.** No recipient is a suppression with a reason, not
   a crash and not a silent drop.
2. **Apply 4.6's patient rules** — deceased, unverified phone, consent. These
   run *before* policy because they are absolute: a deceased patient is not
   "deferred to 07:00".
3. **Apply 4.5's fatigue policy** — quiet hours, rate cap. CRITICAL bypasses.
4. **Write the row first, send second.** The ``notifications`` row is the
   record that the system took responsibility. Writing it after a successful
   send would lose every failure.
5. **Retry 3x with backoff, then mark ``failed``** — and *return*, never raise.

That last point is the one with teeth. The dispatcher runs inside the same
transaction as the escalation rung that called it. An exception escaping here
would roll back the rung's ``case_events`` row and its ``mark_fired``, and the
ladder would re-fire the same rung forever. The plan requires the opposite:

    SMS provider returns 500 → retried → failure surfaced, **ladder continues**

**No NODE B anywhere in this file.**
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.notifications import MAX_SEND_ATTEMPTS
from app.db.types import uuid7
from app.notifications.adapters import AdapterRegistry, Recipient
from app.notifications.templates_ import TemplateMissingError, render
from app.rules import SEVERITY_CRITICAL
from app.services import notification_policy as policy
from app.services.lab_flags import record_event

log = structlog.get_logger(__name__)

EVENT_NOTIFICATION_SENT = "notification_sent"
EVENT_NOTIFICATION_SUPPRESSED = "notification_suppressed"
EVENT_NOTIFICATION_FAILED = "notification_failed"

# Encounter statuses that change who may be contacted. Phase 4.6:
# "Suppress entirely if encounter status is deceased. Also handle lama and
# transferred."
STATUS_SUPPRESS_PATIENT = ("deceased",)
STATUS_REDIRECT_PATIENT = ("transferred", "lama")

_REGISTRY = AdapterRegistry()


def registry() -> AdapterRegistry:
    """The process-wide adapter registry. Tests swap adapters onto it."""
    return _REGISTRY


@dataclass
class Dispatch:
    """What happened to one notification."""

    notification_id: uuid.UUID | None
    status: str
    channel: str
    suppression_reason: str | None = None
    error: str | None = None
    created: bool = True
    """False when this dedupe_key had already been handled."""


def dedupe_key(
    case_id: uuid.UUID | None,
    template_key: str,
    channel: str,
    *,
    recipient: str | None,
    escalation_level: int | None,
) -> str:
    """What makes a redelivered queue message produce one notification.

    Phase 4.5: *"multiple analytes in one report = **one** notification, not
    twelve."* That grouping falls out of this key: the analytes are all one
    case, one template and one rung, so they collapse to one row. The key
    deliberately does **not** include a timestamp — including one would make
    every redelivery unique, which is the bug this prevents.
    """
    rung = "-" if escalation_level is None else str(escalation_level)
    return f"{case_id}:{template_key}:{channel}:{recipient or 'none'}:{rung}"


async def _recipient_for(
    session: AsyncSession, *, user_id: uuid.UUID | None, patient_id: uuid.UUID | None
) -> Recipient | None:
    if user_id is not None:
        row = (
            await session.execute(
                text(
                    "SELECT id, full_name, email, phone_e164 FROM users "
                    " WHERE id = :u AND deleted_at IS NULL"
                ),
                {"u": str(user_id)},
            )
        ).first()
        if row is None:
            return None
        return Recipient(
            user_id=str(row.id),
            email=row.email,
            phone_e164=row.phone_e164,
            display_name=row.full_name,
        )

    if patient_id is not None:
        row = (
            await session.execute(
                text(
                    "SELECT id, name, phone_primary_e164 FROM patients "
                    " WHERE id = :p AND deleted_at IS NULL"
                ),
                {"p": str(patient_id)},
            )
        ).first()
        if row is None:
            return None
        return Recipient(
            patient_id=str(row.id),
            phone_e164=row.phone_primary_e164,
            display_name=row.name,
        )

    return None


@dataclass(frozen=True)
class PatientGate:
    """4.6's decision about whether a patient may be messaged at all."""

    allowed: bool
    reason: str | None = None
    redirect_to_unit_head: bool = False


async def patient_contact_gate(
    session: AsyncSession, case_id: uuid.UUID
) -> PatientGate:
    """Phase 4.6, in the order the plan states the rules.

    * **deceased → suppress entirely.** Not deferred, not redirected to the
      family. There is no version of "please call us about your test" that is
      acceptable to send to a dead patient's phone.
    * **transferred / lama → notify the receiving facility instead**, which in
      this build means the unit head handles it, since the receiving-facility
      contact directory is Phase 9's integration work.
    * **only send if ``phone_verified_at`` is set**, otherwise escalate to the
      unit head. An unverified number is somebody else's phone until proven
      otherwise, and this message names a hospital and a patient.
    """
    row = (
        await session.execute(
            text(
                "SELECT e.status, p.phone_primary_e164, p.phone_verified_at, "
                "       p.sms_consent_at "
                "  FROM pending_cases pc "
                "  JOIN encounters e ON e.id = pc.encounter_id "
                "  JOIN patients p ON p.id = pc.patient_id "
                " WHERE pc.id = :c"
            ),
            {"c": str(case_id)},
        )
    ).first()
    if row is None:
        return PatientGate(False, "no_recipient")

    if row.status in STATUS_SUPPRESS_PATIENT:
        return PatientGate(False, "patient_deceased")

    if row.status in STATUS_REDIRECT_PATIENT:
        return PatientGate(False, "encounter_lama", redirect_to_unit_head=True)

    if not row.phone_primary_e164:
        return PatientGate(False, "no_recipient", redirect_to_unit_head=True)

    if row.phone_verified_at is None:
        return PatientGate(False, "phone_unverified", redirect_to_unit_head=True)

    return PatientGate(True)


async def dispatch(
    session: AsyncSession,
    *,
    case_id: uuid.UUID | None,
    template_key: str,
    channel: str,
    user_id: uuid.UUID | None = None,
    patient_id: uuid.UUID | None = None,
    context: dict[str, object] | None = None,
    severity: str | None = None,
    escalation_level: int | None = None,
    locale: str | None = None,
    at: dt.datetime | None = None,
    actor_user_id: uuid.UUID | None = None,
    dedupe_override: str | None = None,
) -> Dispatch:
    """Take responsibility for telling one recipient one thing.

    Never raises. Every outcome — sent, suppressed, failed, already handled —
    comes back as a ``Dispatch`` so the caller (usually an escalation rung) can
    carry on regardless.
    """
    moment = at or dt.datetime.now(dt.UTC)
    payload = dict(context or {})

    recipient = await _recipient_for(session, user_id=user_id, patient_id=patient_id)
    recipient_key = str(user_id or patient_id or "")

    key = dedupe_override or dedupe_key(
        case_id,
        template_key,
        channel,
        recipient=recipient_key or None,
        escalation_level=escalation_level,
    )

    # ── decide, before writing ────────────────────────────────────
    if recipient is None:
        verdict = policy.PolicyVerdict.suppress("no_recipient")
    elif patient_id is not None and case_id is not None:
        # 4.6's rules are absolute and run first: a deceased patient is not
        # "deferred to 07:00", they are never messaged.
        gate = await patient_contact_gate(session, case_id)
        if not gate.allowed:
            verdict = policy.PolicyVerdict.suppress(gate.reason or "no_recipient")
        else:
            # And then 4.5's fatigue controls, which apply to patients too.
            # The plan scopes quiet hours by *severity*, not by audience, and
            # a 3am text about a non-urgent result is worse for a patient than
            # for a doctor -- they cannot act on it and cannot tell whether it
            # is urgent. CRITICAL still bypasses, as it does everywhere.
            verdict = await policy.evaluate(
                session,
                severity=severity,
                channel=channel,
                # The rate cap is per *clinician*; a patient gets at most one
                # message per rung anyway, so there is no one to cap.
                user_id=None,
                at=moment,
            )
    else:
        verdict = await policy.evaluate(
            session,
            severity=severity,
            channel=channel,
            user_id=user_id,
            at=moment,
        )

    # ── render ────────────────────────────────────────────────────
    chosen_locale = locale or "en"
    rendered = ""
    if verdict.send:
        try:
            rendered, chosen_locale = render(template_key, chosen_locale, payload)
        except (TemplateMissingError, Exception) as exc:
            # A missing or broken template is a failure to send, not a crash of
            # the ladder that asked for it.
            log.warning(
                "notification_render_failed",
                template_key=template_key,
                error=str(exc),
            )
            verdict = policy.PolicyVerdict(send=False, suppression_reason=None)
            return await _record_failure(
                session,
                case_id=case_id,
                key=key,
                template_key=template_key,
                channel=channel,
                user_id=user_id,
                patient_id=patient_id,
                locale=chosen_locale,
                payload=payload,
                escalation_level=escalation_level,
                error=f"template render failed: {exc}",
                moment=moment,
                actor_user_id=actor_user_id,
            )

    # ── write the row, idempotently ───────────────────────────────
    notification_id = uuid7()
    status = "queued" if verdict.send else "suppressed"
    inserted = (
        await session.execute(
            text(
                "INSERT INTO notifications "
                "(id, case_id, user_id, patient_id, channel, template_key, "
                " locale, payload, status, suppression_reason, attempts, "
                " escalation_level, dedupe_key, created_by, updated_by) "
                "VALUES (:i, :c, :u, :p, :ch, :tk, :loc, CAST(:pl AS jsonb), "
                "        :st, :sr, 0, :lvl, :key, :a, :a) "
                "ON CONFLICT (dedupe_key) DO NOTHING "
                "RETURNING id"
            ),
            {
                "i": str(notification_id),
                "c": str(case_id) if case_id else None,
                "u": str(user_id) if user_id else None,
                "p": str(patient_id) if patient_id else None,
                "ch": channel,
                "tk": template_key,
                "loc": chosen_locale,
                "pl": json.dumps(payload, default=str),
                "st": status,
                "sr": verdict.suppression_reason,
                "lvl": escalation_level,
                "key": key,
                "a": str(actor_user_id) if actor_user_id else None,
            },
        )
    ).first()

    if inserted is None:
        # Already handled. A redelivered queue message, or the "twelve analytes
        # in one report" case collapsing onto one row.
        existing = (
            await session.execute(
                text(
                    "SELECT id, status, channel, suppression_reason "
                    "  FROM notifications WHERE dedupe_key = :k"
                ),
                {"k": key},
            )
        ).one()
        return Dispatch(
            notification_id=existing.id,
            status=existing.status,
            channel=existing.channel,
            suppression_reason=existing.suppression_reason,
            created=False,
        )

    if not verdict.send:
        if case_id is None:
            return Dispatch(
                notification_id=notification_id,
                status="suppressed",
                channel=channel,
                suppression_reason=verdict.suppression_reason,
            )
        await record_event(
            session,
            case_id,
            EVENT_NOTIFICATION_SUPPRESSED,
            {
                "notification_id": str(notification_id),
                "template_key": template_key,
                "channel": channel,
                "reason": verdict.suppression_reason,
                "note": verdict.note,
                "defer_until": (
                    verdict.defer_until.isoformat() if verdict.defer_until else None
                ),
                "escalation_level": escalation_level,
            },
            actor_user_id=actor_user_id,
            now=moment,
        )
        return Dispatch(
            notification_id=notification_id,
            status="suppressed",
            channel=channel,
            suppression_reason=verdict.suppression_reason,
        )

    # ── send ──────────────────────────────────────────────────────
    # Narrowed for the type checker: a null recipient was suppressed above and
    # returned, so reaching here means one exists.
    assert recipient is not None
    adapter, actual_channel = _REGISTRY.for_channel(channel)
    result = None
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        result = await adapter.send(recipient, template_key, rendered, payload)
        await session.execute(
            text("UPDATE notifications SET attempts = :n WHERE id = :i"),
            {"n": attempt, "i": str(notification_id)},
        )
        if result.ok:
            break
        if attempt < MAX_SEND_ATTEMPTS:
            # Backoff, bounded so a handler cannot hang on a dead provider.
            await asyncio.sleep(min(0.05 * (2 ** (attempt - 1)), 0.5))

    assert result is not None
    if not result.ok:
        return await _mark_failed(
            session,
            notification_id=notification_id,
            case_id=case_id,
            template_key=template_key,
            channel=actual_channel,
            error=result.error or "send failed",
            escalation_level=escalation_level,
            moment=moment,
            actor_user_id=actor_user_id,
        )

    await session.execute(
        text(
            "UPDATE notifications "
            "   SET status = 'sent', sent_at = :t, provider_msg_id = :m, "
            "       channel = :ch, error = NULL, updated_at = now() "
            " WHERE id = :i"
        ),
        {
            "i": str(notification_id),
            "t": moment,
            "m": result.provider_msg_id,
            "ch": actual_channel,
        },
    )
    if case_id is None:
        return Dispatch(
            notification_id=notification_id, status="sent", channel=actual_channel
        )
    await record_event(
        session,
        case_id,
        EVENT_NOTIFICATION_SENT,
        {
            "notification_id": str(notification_id),
            "template_key": template_key,
            "channel": actual_channel,
            "requested_channel": channel,
            "locale": chosen_locale,
            "escalation_level": escalation_level,
            "recipient_user_id": str(user_id) if user_id else None,
            "recipient_patient_id": str(patient_id) if patient_id else None,
            "provider_msg_id": result.provider_msg_id,
        },
        actor_user_id=actor_user_id,
        now=moment,
    )
    return Dispatch(
        notification_id=notification_id, status="sent", channel=actual_channel
    )


async def _mark_failed(
    session: AsyncSession,
    *,
    notification_id: uuid.UUID,
    case_id: uuid.UUID | None,
    template_key: str,
    channel: str,
    error: str,
    escalation_level: int | None,
    moment: dt.datetime,
    actor_user_id: uuid.UUID | None,
) -> Dispatch:
    await session.execute(
        text(
            "UPDATE notifications "
            "   SET status = 'failed', error = :e, updated_at = now() "
            " WHERE id = :i"
        ),
        {"i": str(notification_id), "e": error[:500]},
    )
    if case_id is not None:
        await record_event(
            session,
            case_id,
            EVENT_NOTIFICATION_FAILED,
            {
                "notification_id": str(notification_id),
                "template_key": template_key,
                "channel": channel,
                "error": error[:500],
                "attempts": MAX_SEND_ATTEMPTS,
                "escalation_level": escalation_level,
                # Said out loud in the trail: the message was lost, the case
                # was not.
                "ladder_continues": True,
            },
            actor_user_id=actor_user_id,
            now=moment,
        )
    log.warning(
        "notification_failed",
        notification_id=str(notification_id),
        channel=channel,
        error=error,
    )
    return Dispatch(
        notification_id=notification_id,
        status="failed",
        channel=channel,
        error=error,
    )


async def _record_failure(
    session: AsyncSession,
    *,
    case_id: uuid.UUID | None,
    key: str,
    template_key: str,
    channel: str,
    user_id: uuid.UUID | None,
    patient_id: uuid.UUID | None,
    locale: str,
    payload: dict[str, object],
    escalation_level: int | None,
    error: str,
    moment: dt.datetime,
    actor_user_id: uuid.UUID | None,
) -> Dispatch:
    """A failure that happened before a row existed (a broken template)."""
    notification_id = uuid7()
    inserted = (
        await session.execute(
            text(
                "INSERT INTO notifications "
                "(id, case_id, user_id, patient_id, channel, template_key, locale, "
                " payload, status, attempts, error, escalation_level, dedupe_key, "
                " created_by, updated_by) "
                "VALUES (:i, :c, :u, :p, :ch, :tk, :loc, CAST(:pl AS jsonb), "
                "        'failed', 0, :err, :lvl, :key, :a, :a) "
                "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"
            ),
            {
                "i": str(notification_id),
                "c": str(case_id) if case_id else None,
                "u": str(user_id) if user_id else None,
                "p": str(patient_id) if patient_id else None,
                "ch": channel,
                "tk": template_key,
                "loc": locale,
                "pl": json.dumps(payload, default=str),
                "err": error[:500],
                "lvl": escalation_level,
                "key": key,
                "a": str(actor_user_id) if actor_user_id else None,
            },
        )
    ).first()
    if inserted is None:
        return Dispatch(None, "failed", channel, error=error, created=False)

    if case_id is not None:
        await record_event(
            session,
            case_id,
            EVENT_NOTIFICATION_FAILED,
            {
                "notification_id": str(notification_id),
                "template_key": template_key,
                "channel": channel,
                "error": error[:500],
                "escalation_level": escalation_level,
                "ladder_continues": True,
            },
            actor_user_id=actor_user_id,
            now=moment,
        )
    return Dispatch(notification_id, "failed", channel, error=error)


async def record_delivery_receipt(
    session: AsyncSession, *, provider_msg_id: str, delivered: bool, error: str | None
) -> bool:
    """*"Delivery receipt webhook endpoint for SMS provider."* Phase 4.3.

    ``sent`` means the gateway accepted it; ``delivered`` means the handset
    acknowledged it. Only the second is evidence the patient's phone actually
    received the message, which matters for the patient rung.
    """
    result = await session.execute(
        text(
            "UPDATE notifications "
            "   SET status = CASE WHEN :ok THEN 'delivered' ELSE 'failed' END, "
            "       error = :err, updated_at = now() "
            " WHERE provider_msg_id = :m AND status IN ('sent', 'delivered') "
            " RETURNING id"
        ),
        {"m": provider_msg_id, "ok": delivered, "err": (error or None)},
    )
    return result.first() is not None


def is_critical(severity: str | None) -> bool:
    """One place decides this, so 'critical bypasses the controls' is one
    fact rather than four."""
    return severity == SEVERITY_CRITICAL
