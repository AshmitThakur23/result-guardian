"""The SMS provider's delivery-receipt webhook. Phase 4.3.

**The one endpoint with no human behind it.** A provider callback cannot
carry a bearer token, so it sits outside the RBAC applied to every other
router — and that makes it the one endpoint worth being explicit about.

An unauthenticated receipt endpoint lets anyone who can reach NODE A assert
that a patient's message was delivered. That is not a data leak; it is worse
in one specific way: it **falsifies the evidence** that a patient was
reached, which is exactly what Phase 5.6's patient-contact metric and a NABH
reviewer rely on.

So it takes an optional shared secret, ``RG_WEBHOOK_SECRET``:

* **secret configured** — the header must match, or the request is refused;
* **secret not configured** — the request is accepted and a **warning is
  logged on every call**, because that is the Phase 4 behaviour and a later
  phase must not break an earlier one by making a working integration start
  failing on upgrade.

Configure the secret before a pilot. The runbook says so, and this docstring
is the other half of that record.
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_session
from app.schemas.ownership import DeliveryReceipt
from app.services.notifications import record_delivery_receipt

log = structlog.get_logger(__name__)

router = APIRouter(tags=["notifications"])


def _check_secret(provided: str | None, settings: Settings) -> None:
    expected = settings.webhook_secret
    if not expected:
        log.warning(
            "webhook_unauthenticated",
            endpoint="/notifications/delivery-receipt",
            detail=(
                "RG_WEBHOOK_SECRET is not set, so anyone who can reach this "
                "port can assert a delivery. Set it before a pilot."
            ),
        )
        return
    # Constant-time: a timing-distinguishable comparison on a shared secret is
    # a slow but real oracle.
    if provided is None or not hmac.compare_digest(provided, expected):
        log.warning("webhook_secret_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook secret.",
        )


@router.post(
    "/notifications/delivery-receipt",
    summary="SMS provider delivery receipt webhook",
)
async def delivery_receipt(
    payload: DeliveryReceipt,
    x_webhook_secret: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """*"Delivery receipt webhook endpoint for SMS provider."*

    ``sent`` means the gateway accepted the message; ``delivered`` means the
    handset acknowledged it. Only the second is evidence the patient's phone
    received anything, which is what the patient rung actually cares about.

    Returns 200 with ``matched: false`` for an unknown id rather than 404: a
    provider retrying a receipt for a message we no longer hold should not be
    told to keep retrying.
    """
    _check_secret(x_webhook_secret, settings)

    matched = await record_delivery_receipt(
        session,
        provider_msg_id=payload.provider_msg_id,
        delivered=payload.delivered,
        error=payload.error,
    )
    await session.commit()
    return {"matched": matched, "provider_msg_id": payload.provider_msg_id}
