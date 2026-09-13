"""Notification channel adapters. Phase 4.3.

    Adapter interface: ``send(channel, recipient, template, context) ->
    ProviderResult``
    Implementations: ``InAppAdapter`` (DB row + polling), ``SmtpAdapter``,
    ``SmsAdapter`` (MSG91 / Gupshup — **DLT-registered templates required in
    India**), ``NullAdapter`` (tests)

The interface is narrow on purpose. An adapter's whole job is *"try to hand
this to a provider and say what happened"*. It does not decide whether to send
— quiet hours, rate caps, deduplication and patient suppression are all
decided before an adapter is reached, in ``services/notifications.py``, because
those are policy and this is plumbing.

**An adapter never raises to its caller.** It returns a ``ProviderResult`` with
``ok=False`` and an error string. That is not politeness: the escalation ladder
runs in the same transaction as the send, and an exception escaping here would
roll back the rung's ``case_events`` row alongside it. The plan is explicit
that the opposite must happen — *"SMS provider returns 500 → retried → failure
surfaced, **ladder continues**"*. A provider outage must cost a message, never
the safety state.

**No NODE B.** None of these adapters talk to the inference node.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import httpx
import structlog

log = structlog.get_logger(__name__)

# The DLT note in the plan is a legal requirement, not an implementation hint:
# in India an SMS to a subscriber must use a template registered with the
# telecom regulator through the operator. An unregistered template is rejected
# by the gateway, so `template_key` here must match a DLT-registered template
# id in production. Recorded on the result so a rejection is diagnosable.
DLT_TEMPLATE_HEADER = "X-RG-DLT-Template"


@dataclass(frozen=True)
class ProviderResult:
    """What a provider did with one message."""

    ok: bool
    provider_msg_id: str | None = None
    error: str | None = None
    detail: dict[str, object] = field(default_factory=dict)

    @classmethod
    def success(
        cls, provider_msg_id: str | None = None, **detail: object
    ) -> ProviderResult:
        return cls(ok=True, provider_msg_id=provider_msg_id, detail=detail)

    @classmethod
    def failure(cls, error: str, **detail: object) -> ProviderResult:
        return cls(ok=False, error=error[:500], detail=detail)


@dataclass(frozen=True)
class Recipient:
    """Who to reach, and how to address them."""

    user_id: str | None = None
    patient_id: str | None = None
    email: str | None = None
    phone_e164: str | None = None
    display_name: str | None = None


class NotificationAdapter(Protocol):
    """`send(channel, recipient, template, context) -> ProviderResult`."""

    channel: str

    async def send(
        self,
        recipient: Recipient,
        template_key: str,
        rendered: str,
        context: dict[str, object],
    ) -> ProviderResult: ...


class InAppAdapter:
    """*"DB row + polling."*

    The notification row **is** the delivery. It is written by the dispatcher
    before any adapter runs, so there is nothing further to do and this always
    succeeds — which is exactly why in-app is the channel everything degrades
    to when a provider is missing. A hospital with no SMS contract still has a
    working escalation ladder; it just reaches people on screen.
    """

    channel = "in_app"

    async def send(
        self,
        recipient: Recipient,
        template_key: str,
        rendered: str,
        context: dict[str, object],
    ) -> ProviderResult:
        return ProviderResult.success(detail={"delivered_as": "in_app_row"})


class NullAdapter:
    """Accepts everything, sends nothing. For tests and for a channel a
    hospital has not configured.

    Records what it *would* have sent so a test can assert on content without
    a provider, and so a dry-run deployment is inspectable.
    """

    def __init__(self, channel: str = "sms") -> None:
        self.channel = channel
        self.sent: list[tuple[Recipient, str, str]] = []

    async def send(
        self,
        recipient: Recipient,
        template_key: str,
        rendered: str,
        context: dict[str, object],
    ) -> ProviderResult:
        self.sent.append((recipient, template_key, rendered))
        return ProviderResult.success(
            provider_msg_id=f"null-{len(self.sent)}",
            detail={"adapter": "null", "channel": self.channel},
        )


class FailingAdapter:
    """Always fails. Exists so the plan's *"provider returns 500 → ladder
    continues"* requirement is testable without a real outage."""

    def __init__(self, channel: str = "sms", error: str = "provider returned 500"):
        self.channel = channel
        self.error = error
        self.attempts = 0

    async def send(
        self,
        recipient: Recipient,
        template_key: str,
        rendered: str,
        context: dict[str, object],
    ) -> ProviderResult:
        self.attempts += 1
        return ProviderResult.failure(self.error, attempt=self.attempts)


class SmtpAdapter:
    """Email over SMTP.

    Synchronous ``smtplib`` deliberately: it runs inside the worker's handler,
    and the alternative — an async SMTP client — would add a dependency for a
    channel that sends a handful of messages a minute. The timeout is what
    keeps a hung mail server from holding the handler open.
    """

    channel = "email"

    def __init__(
        self,
        host: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        sender: str = "result-guardian@localhost",
        timeout: float = 10.0,
        use_tls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.timeout = timeout
        self.use_tls = use_tls

    async def send(
        self,
        recipient: Recipient,
        template_key: str,
        rendered: str,
        context: dict[str, object],
    ) -> ProviderResult:
        if not recipient.email:
            return ProviderResult.failure("no email address for this recipient")

        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = recipient.email
        message["Subject"] = str(context.get("subject") or "Result Guardian")
        message.set_content(rendered)

        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                if self.use_tls:
                    smtp.starttls()
                if self.username:
                    smtp.login(self.username, self.password or "")
                smtp.send_message(message)
        except Exception as exc:
            # Never raises onward -- see the module docstring.
            log.warning("smtp_send_failed", error=str(exc), to=recipient.email)
            return ProviderResult.failure(f"{type(exc).__name__}: {exc}")

        return ProviderResult.success(detail={"to": recipient.email})


class SmsAdapter:
    """SMS over an HTTP gateway (MSG91 / Gupshup shaped).

    ⚠️ **DLT-registered templates are required in India.** ``template_key`` is
    sent as the registered template id; a gateway will reject anything else,
    and that rejection is surfaced rather than retried blindly.
    """

    channel = "sms"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        sender_id: str = "RGUARD",
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.sender_id = sender_id
        self.timeout = timeout

    async def send(
        self,
        recipient: Recipient,
        template_key: str,
        rendered: str,
        context: dict[str, object],
    ) -> ProviderResult:
        if not recipient.phone_e164:
            return ProviderResult.failure("no phone number for this recipient")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/send",
                    json={
                        "sender": self.sender_id,
                        "to": recipient.phone_e164,
                        "message": rendered,
                        "template_id": template_key,
                    },
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        DLT_TEMPLATE_HEADER: template_key,
                    },
                )
        except Exception as exc:
            log.warning("sms_send_failed", error=str(exc))
            return ProviderResult.failure(f"{type(exc).__name__}: {exc}")

        if response.status_code >= 400:
            return ProviderResult.failure(
                f"provider returned {response.status_code}",
                status_code=response.status_code,
                body=response.text[:300],
            )

        try:
            body = response.json()
        except ValueError:
            body = {}
        provider_id = body.get("message_id") or body.get("id")
        return ProviderResult.success(
            provider_msg_id=str(provider_id) if provider_id else None,
            detail={"status_code": response.status_code},
        )


class AdapterRegistry:
    """Which adapter serves which channel, with a documented degradation.

    A channel with no configured adapter falls back to **in_app** rather than
    failing. That is the degradation ladder applied to notification: a hospital
    without an SMS contract still gets every rung of the escalation, delivered
    on screen. Losing the channel must never mean losing the message.
    """

    def __init__(self, adapters: dict[str, NotificationAdapter] | None = None) -> None:
        self._adapters: dict[str, NotificationAdapter] = {"in_app": InAppAdapter()}
        if adapters:
            self._adapters.update(adapters)

    def register(self, adapter: NotificationAdapter) -> None:
        self._adapters[adapter.channel] = adapter

    def has(self, channel: str) -> bool:
        return channel in self._adapters

    def for_channel(self, channel: str) -> tuple[NotificationAdapter, str]:
        """Returns the adapter and the channel it will actually deliver on."""
        adapter = self._adapters.get(channel)
        if adapter is not None:
            return adapter, channel
        log.info("notification_channel_unconfigured", channel=channel)
        return self._adapters["in_app"], "in_app"
