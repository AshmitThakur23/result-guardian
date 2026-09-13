"""Phase 5.1 — *"RBAC dependency on **every** endpoint."*

**This is the test that stops the hole coming back.**

The Phase 5 audit found 31 clinical endpoints reachable with no credential at
all: every Phase 1-4 route. The dashboard was behind a login, the API was not,
and a gated single-page app is not an access control — anyone who could reach
NODE A's port could read a patient record or discharge a patient.

Adding the dependency fixed those. This test is the part that matters
afterwards: it walks the **published route table** and sends a real
unauthenticated request to every endpoint, failing if any of them answers with
anything other than 401 — except the handful on an explicit allow-list, each
of which has to justify itself in writing.

**Black-box on purpose.** An earlier draft of this test inspected the
dependency graph and passed while the endpoints were still open, because
router-level dependencies are attached at inclusion time and are not visible
on the router object it was reading. Asserting the *behaviour* cannot be
fooled that way.

A new endpoint added in a hurry fails this test. That is the point.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# The only endpoints that may be reached without a credential, and why.
#
# Adding to this set is a security decision, not a convenience. Each entry
# names the reason it cannot require a token.
PUBLIC_ENDPOINTS: dict[tuple[str, str], str] = {
    ("GET", "/api/health"): (
        "Probed by the container runtime and by a browser that has not signed "
        "in. Returns no patient data."
    ),
    ("GET", "/api/version"): "Build metadata. No patient data.",
    ("POST", "/api/auth/login"): "How a token is obtained in the first place.",
    ("POST", "/api/auth/refresh"): (
        "Presents a refresh token, which is itself the credential."
    ),
    ("POST", "/api/notifications/delivery-receipt"): (
        "An SMS provider callback. No user exists to hold a token, so it "
        "authenticates with the optional RG_WEBHOOK_SECRET shared secret "
        "instead -- see app/routers/webhooks.py."
    ),
}

_PARAM = re.compile(r"\{[^}]+\}")


def _published() -> list[tuple[str, str]]:
    """Every (method, path) the app publishes, from its own OpenAPI schema."""
    from app.main import create_app

    schema = create_app().openapi()
    return [
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS"}
    ]


def _concrete(path: str) -> str:
    """Fill path parameters with a well-formed UUID.

    A malformed parameter would 422 before the dependency runs, and a 422
    would read as "protected" when it is not.
    """
    return _PARAM.sub(str(uuid.uuid4()), path)


@pytest.mark.asyncio
async def test_no_endpoint_answers_an_unauthenticated_request(client: Any) -> None:
    reachable: list[str] = []

    for method, path in _published():
        if (method, path) in PUBLIC_ENDPOINTS:
            continue
        response = await client.request(method, _concrete(path), json={})
        if response.status_code != 401:
            reachable.append(f"{method} {path} -> {response.status_code}")

    assert not reachable, (
        "These endpoints answered an unauthenticated request:\n  "
        + "\n  ".join(sorted(reachable))
        + "\n\nAdd `Depends(require_role(...))` to the route or to its router "
        "in app/main.py. If it genuinely cannot take a token, add it to "
        "PUBLIC_ENDPOINTS with a written reason."
    )


def test_the_public_allow_list_is_small_and_all_of_it_exists() -> None:
    """An allow-list that drifts out of date stops being a decision.

    Every entry must still correspond to a published route — otherwise a
    removed endpoint leaves a stale exemption that silently covers a *future*
    route added at the same path.
    """
    live = set(_published())
    stale = sorted(f"{m} {p}" for (m, p) in PUBLIC_ENDPOINTS if (m, p) not in live)
    assert not stale, f"PUBLIC_ENDPOINTS names routes that no longer exist: {stale}"

    # A sanity ceiling. If this ever needs raising, the raise is the review.
    assert len(PUBLIC_ENDPOINTS) <= 6


@pytest.mark.asyncio
async def test_a_phase_1_read_refuses_an_unauthenticated_caller(client: Any) -> None:
    """The Phase 5 audit's actual finding, as a named assertion.

    Before the fix this returned patient data to anybody who asked.
    """
    response = await client.get("/api/patients", params={"q": "Rao"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_phase_1_write_refuses_an_unauthenticated_caller(client: Any) -> None:
    """Discharging a patient with no credential was possible until Phase 5."""
    response = await client.post(f"/api/encounters/{uuid.uuid4()}/discharge")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_health_stays_open(client: Any) -> None:
    """Exit Gate 0 requires 200 from an unauthenticated probe. Still true."""
    response = await client.get("/api/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_webhook_accepts_when_no_secret_is_configured(client: Any) -> None:
    """Phase 4's behaviour is preserved.

    THE ONE RULE: a later phase must not break an earlier one. A hospital
    upgrading to Phase 5 with a working SMS integration must not find its
    delivery receipts start failing.
    """
    response = await client.post(
        "/api/notifications/delivery-receipt",
        json={"provider_msg_id": "no-such-message", "delivered": True},
    )
    assert response.status_code == 200
    assert response.json()["matched"] is False


@pytest.mark.asyncio
async def test_the_webhook_refuses_a_wrong_secret_when_one_is_configured(
    client: Any,
) -> None:
    """And the control works once an operator turns it on."""
    from app.config import Settings, get_settings

    app = client._transport.app
    app.dependency_overrides[get_settings] = lambda: Settings(
        webhook_secret="the-real-secret"
    )
    try:
        refused = await client.post(
            "/api/notifications/delivery-receipt",
            json={"provider_msg_id": "x", "delivered": True},
            headers={"X-Webhook-Secret": "guessed"},
        )
        assert refused.status_code == 401

        missing = await client.post(
            "/api/notifications/delivery-receipt",
            json={"provider_msg_id": "x", "delivered": True},
        )
        assert missing.status_code == 401

        accepted = await client.post(
            "/api/notifications/delivery-receipt",
            json={"provider_msg_id": "x", "delivered": True},
            headers={"X-Webhook-Secret": "the-real-secret"},
        )
        assert accepted.status_code == 200
    finally:
        app.dependency_overrides.pop(get_settings, None)
