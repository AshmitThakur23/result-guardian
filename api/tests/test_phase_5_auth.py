"""Phase 5.1 — authentication, authorisation and row-level scoping.

Every bullet of 5.1 has a test here, and several have two: one that the
control works, and one that it cannot be walked around.

    Login with ``employee_code`` + password (**Argon2id**)
    JWT access token (**15 min**) + refresh token (**12h**, rotated, stored
    hashed in ``sessions``)
    Force password change on first login; password policy
    Account lockout after **5 failures for 15 min**
    RBAC dependency on **every** endpoint
    Row-level scoping: a doctor sees their department's cases; auditor sees
    all but **read-only**
    Break-glass access with mandatory reason, **logged loudly**
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services import auth as auth_service
from app.services.rate_limit import RateLimiter
from tests._phase5 import (
    TEST_PASSWORD,
    auth_header,
    bearer,
    build_world,
    extra_case,
    login,
)

pytestmark = pytest.mark.integration


# ── password hashing ───────────────────────────────────────────────────


def test_the_hash_is_argon2id() -> None:
    """*"Login with employee_code + password (**Argon2id**)."*"""
    hashed = auth_service.hash_password("Correct-Horse-Battery-9")
    assert hashed.startswith("$argon2id$")


def test_the_same_password_hashes_differently_each_time() -> None:
    """A per-password salt. Identical hashes would leak shared passwords."""
    a = auth_service.hash_password("Correct-Horse-Battery-9")
    b = auth_service.hash_password("Correct-Horse-Battery-9")
    assert a != b
    assert auth_service.verify_password(a, "Correct-Horse-Battery-9")
    assert auth_service.verify_password(b, "Correct-Horse-Battery-9")


def test_a_wrong_password_does_not_verify() -> None:
    hashed = auth_service.hash_password("Correct-Horse-Battery-9")
    assert auth_service.verify_password(hashed, "wrong") is False


def test_a_corrupt_hash_returns_false_rather_than_raising() -> None:
    """A mangled column value must fail the login, not 500 the endpoint."""
    assert auth_service.verify_password("not-a-hash", "anything") is False


@pytest.mark.parametrize(
    ("password", "expected_fragment"),
    [
        ("short1A", "at least 12"),
        ("alllowercase123", "upper and lower"),
        ("NoDigitsInHereAtAll", "must contain a digit"),
    ],
)
def test_the_password_policy_rejects(password: str, expected_fragment: str) -> None:
    with pytest.raises(auth_service.PasswordPolicyError) as caught:
        auth_service.check_password_policy(password)
    assert expected_fragment in str(caught.value)


def test_the_password_may_not_contain_the_employee_code() -> None:
    with pytest.raises(auth_service.PasswordPolicyError):
        auth_service.check_password_policy(
            "MyDOC12345Password1", employee_code="DOC12345"
        )


def test_a_good_password_passes() -> None:
    auth_service.check_password_policy("Ward-Round-2026x", employee_code="DOC1")


def test_the_refresh_token_is_stored_hashed_not_raw() -> None:
    token = "some-refresh-token"
    hashed = auth_service.hash_refresh_token(token)
    assert hashed != token
    assert len(hashed) == 64


# ── login ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_correct_login_issues_both_tokens(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    body = await login(client, f"DOC{ids['tag']}")

    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["role"] == "doctor"
    # 15 minutes, per the plan.
    assert body["expires_in"] == get_settings().jwt_access_ttl_minutes * 60


@pytest.mark.asyncio
async def test_the_access_token_expires_in_fifteen_minutes(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    body = await login(client, f"DOC{ids['tag']}")
    claims = jwt.decode(
        body["access_token"], get_settings().jwt_secret, algorithms=["HS256"]
    )
    lifetime = claims["exp"] - claims["iat"]
    assert lifetime == 15 * 60


@pytest.mark.asyncio
async def test_the_refresh_token_is_not_stored_in_the_clear(
    client: Any, session: AsyncSession
) -> None:
    """A stolen database dump must not be a stack of working tokens."""
    ids = await build_world(session)
    body = await login(client, f"DOC{ids['tag']}")

    raw = (
        await session.execute(
            text("SELECT count(*) FROM sessions WHERE refresh_token_hash = :t"),
            {"t": body["refresh_token"]},
        )
    ).scalar_one()
    assert raw == 0

    hashed = (
        await session.execute(
            text("SELECT count(*) FROM sessions WHERE refresh_token_hash = :t"),
            {"t": auth_service.hash_refresh_token(body["refresh_token"])},
        )
    ).scalar_one()
    assert hashed == 1


@pytest.mark.asyncio
async def test_the_refresh_token_lives_twelve_hours(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    body = await login(client, f"DOC{ids['tag']}")
    row = (
        await session.execute(
            text(
                "SELECT issued_at, expires_at FROM sessions "
                " WHERE refresh_token_hash = :t"
            ),
            {"t": auth_service.hash_refresh_token(body["refresh_token"])},
        )
    ).one()
    hours = (row.expires_at - row.issued_at).total_seconds() / 3600
    assert abs(hours - get_settings().jwt_refresh_ttl_hours) < 0.01


@pytest.mark.asyncio
async def test_every_login_failure_looks_identical(
    client: Any, session: AsyncSession
) -> None:
    """No account-enumeration oracle.

    Unknown code, wrong password and deactivated account must be one response.
    Anything else tells an attacker which employee codes are live accounts.
    """
    ids = await build_world(session)
    await session.execute(
        text("UPDATE users SET is_active = false WHERE id = :u"),
        {"u": ids["labtech"]},
    )
    await session.commit()

    responses = [
        await client.post(
            "/api/auth/login",
            json={"employee_code": "NOSUCHCODE", "password": TEST_PASSWORD},
        ),
        await client.post(
            "/api/auth/login",
            json={"employee_code": f"DOC{ids['tag']}", "password": "wrong-password-1A"},
        ),
        await client.post(
            "/api/auth/login",
            json={"employee_code": f"LAB{ids['tag']}", "password": TEST_PASSWORD},
        ),
    ]
    assert {r.status_code for r in responses} == {401}
    assert len({r.json()["title"] for r in responses}) == 1


@pytest.mark.asyncio
async def test_the_audit_log_records_which_failure_it_actually_was(
    client: Any, session: AsyncSession
) -> None:
    """The caller learns nothing; the operator learns everything."""
    ids = await build_world(session)
    await client.post(
        "/api/auth/login",
        json={"employee_code": f"DOC{ids['tag']}", "password": "wrong-password-1A"},
    )

    reason = (
        await session.execute(
            text(
                "SELECT after->>'reason' AS reason FROM audit_log "
                " WHERE action = 'auth.login_failed' AND entity_id = :u "
                " ORDER BY seq DESC LIMIT 1"
            ),
            {"u": ids["doctor"]},
        )
    ).scalar()
    assert reason == "bad_password"


# ── lockout ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_five_failures_lock_the_account_for_fifteen_minutes(
    client: Any, session: AsyncSession
) -> None:
    """*"Account lockout after 5 failures for 15 min."*"""
    ids = await build_world(session)
    code = f"DOC{ids['tag']}"

    for _ in range(auth_service.MAX_FAILED_LOGINS):
        response = await client.post(
            "/api/auth/login",
            json={"employee_code": code, "password": "wrong-password-1A"},
        )
        assert response.status_code == 401

    row = (
        await session.execute(
            text("SELECT failed_login_count, locked_until FROM users WHERE id = :u"),
            {"u": ids["doctor"]},
        )
    ).one()
    assert row.failed_login_count == 5
    assert row.locked_until is not None
    minutes = (row.locked_until - dt.datetime.now(dt.UTC)).total_seconds() / 60
    assert 14 <= minutes <= 15.1


@pytest.mark.asyncio
async def test_the_correct_password_is_refused_while_locked(
    client: Any, session: AsyncSession
) -> None:
    """The lock has to actually lock, or the counter is decoration."""
    ids = await build_world(session)
    code = f"DOC{ids['tag']}"

    for _ in range(auth_service.MAX_FAILED_LOGINS):
        await client.post(
            "/api/auth/login",
            json={"employee_code": code, "password": "wrong-password-1A"},
        )

    response = await client.post(
        "/api/auth/login", json={"employee_code": code, "password": TEST_PASSWORD}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_the_failure_counter_survives_the_401(
    client: Any, session: AsyncSession
) -> None:
    """The router commits before raising.

    A rollback on the failure path would reset the counter every time and
    hand an attacker unlimited free guesses at an account that is supposed to
    lock after five.
    """
    ids = await build_world(session)
    code = f"DOC{ids['tag']}"

    for expected in (1, 2, 3):
        await client.post(
            "/api/auth/login",
            json={"employee_code": code, "password": "wrong-password-1A"},
        )
        count = (
            await session.execute(
                text("SELECT failed_login_count FROM users WHERE id = :u"),
                {"u": ids["doctor"]},
            )
        ).scalar_one()
        assert count == expected


@pytest.mark.asyncio
async def test_a_success_clears_the_counter(client: Any, session: AsyncSession) -> None:
    """Four mistypes then a success is a person, not an attack."""
    ids = await build_world(session)
    code = f"DOC{ids['tag']}"

    for _ in range(4):
        await client.post(
            "/api/auth/login",
            json={"employee_code": code, "password": "wrong-password-1A"},
        )
    await login(client, code)

    row = (
        await session.execute(
            text("SELECT failed_login_count, locked_until FROM users WHERE id = :u"),
            {"u": ids["doctor"]},
        )
    ).one()
    assert row.failed_login_count == 0
    assert row.locked_until is None


@pytest.mark.asyncio
async def test_locking_is_audited(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session)
    code = f"DOC{ids['tag']}"
    for _ in range(auth_service.MAX_FAILED_LOGINS):
        await client.post(
            "/api/auth/login",
            json={"employee_code": code, "password": "wrong-password-1A"},
        )

    locked = (
        await session.execute(
            text(
                "SELECT count(*) FROM audit_log "
                " WHERE action = 'auth.account_locked' AND entity_id = :u"
            ),
            {"u": ids["doctor"]},
        )
    ).scalar_one()
    assert locked == 1


# ── rate limiting (5.7) ────────────────────────────────────────────────


def test_the_rate_limiter_allows_then_refuses() -> None:
    limiter = RateLimiter(limit=3, window_seconds=60.0)
    assert [limiter.check("1.2.3.4", now=0.0)[0] for _ in range(3)] == [True] * 3
    allowed, retry_after = limiter.check("1.2.3.4", now=0.0)
    assert allowed is False
    assert 0 < retry_after <= 60


def test_the_window_reopens() -> None:
    limiter = RateLimiter(limit=2, window_seconds=60.0)
    limiter.check("a", now=0.0)
    limiter.check("a", now=0.0)
    assert limiter.check("a", now=0.0)[0] is False
    assert limiter.check("a", now=61.0)[0] is True


def test_one_address_does_not_exhaust_anothers_allowance() -> None:
    limiter = RateLimiter(limit=1, window_seconds=60.0)
    assert limiter.check("a", now=0.0)[0] is True
    assert limiter.check("a", now=0.0)[0] is False
    assert limiter.check("b", now=0.0)[0] is True


@pytest.mark.asyncio
async def test_login_is_rate_limited_at_the_configured_number(
    client: Any, session: AsyncSession
) -> None:
    """The limit is a **setting**, and the endpoint honours it.

    The number is deliberately not hardcoded in the limiter -- the dev stack
    raises it so the E2E suite can log in repeatedly -- so this test sets its
    own and asserts the endpoint reads it, rather than asserting a constant
    that depends on which compose file happens to be in play.
    """
    from app.config import Settings, get_settings

    await build_world(session)
    limit = 5
    client._transport.app.dependency_overrides[get_settings] = lambda: Settings(
        auth_rate_limit_per_minute=limit
    )

    statuses = []
    for index in range(limit + 3):
        response = await client.post(
            "/api/auth/login",
            json={"employee_code": f"NOSUCH{index}", "password": TEST_PASSWORD},
        )
        statuses.append(response.status_code)

    assert 429 in statuses, "the auth endpoint must be rate limited"
    # The first `limit` attempts get through to a normal 401; the next is
    # refused. Anything else means the setting is being ignored.
    assert statuses[:limit] == [401] * limit
    assert statuses[limit] == 429


# ── refresh rotation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_rotates_the_token(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session)
    first = await login(client, f"DOC{ids['tag']}")

    response = await client.post(
        "/api/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]

    old = (
        await session.execute(
            text(
                "SELECT revoked_at, revoked_reason, rotated_to_id FROM sessions "
                " WHERE refresh_token_hash = :t"
            ),
            {"t": auth_service.hash_refresh_token(first["refresh_token"])},
        )
    ).one()
    assert old.revoked_at is not None
    assert old.revoked_reason == "rotated"
    assert old.rotated_to_id is not None


@pytest.mark.asyncio
async def test_reusing_a_rotated_token_revokes_the_whole_family(
    client: Any, session: AsyncSession
) -> None:
    """A replayed refresh token is a bug or a theft, and there is no way to
    tell which from here. Revoking everything is the cheap outcome."""
    ids = await build_world(session)
    first = await login(client, f"DOC{ids['tag']}")
    second = (
        await client.post(
            "/api/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
    ).json()

    replay = await client.post(
        "/api/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert replay.status_code == 401

    # The successor is dead too, not just the replayed token.
    after = await client.post(
        "/api/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert after.status_code == 401

    live = (
        await session.execute(
            text(
                "SELECT count(*) FROM sessions "
                " WHERE user_id = :u AND revoked_at IS NULL"
            ),
            {"u": ids["doctor"]},
        )
    ).scalar_one()
    assert live == 0


@pytest.mark.asyncio
async def test_an_unknown_refresh_token_is_refused(
    client: Any, session: AsyncSession
) -> None:
    await build_world(session)
    response = await client.post(
        "/api/auth/refresh", json={"refresh_token": "not-a-real-token"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_an_expired_refresh_token_is_refused(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    body = await login(client, f"DOC{ids['tag']}")
    await session.execute(
        text(
            # Both ends move: `ck_sessions_window_ordered` requires
            # expires_at > issued_at, so backdating only one violates it.
            "UPDATE sessions SET issued_at = now() - interval '13 hours', "
            "       expires_at = now() - interval '1 hour' "
            " WHERE refresh_token_hash = :t"
        ),
        {"t": auth_service.hash_refresh_token(body["refresh_token"])},
    )
    await session.commit()

    response = await client.post(
        "/api/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_the_session(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session)
    body = await login(client, f"DOC{ids['tag']}")
    headers = auth_header(body["access_token"])

    response = await client.post(
        "/api/auth/logout",
        json={"refresh_token": body["refresh_token"]},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["sessions_revoked"] == 1

    refresh = await client.post(
        "/api/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert refresh.status_code == 401


# ── forced password change ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_user_who_must_change_their_password_cannot_use_the_system(
    client: Any, session: AsyncSession
) -> None:
    """*"Force password change on first login."* "Force" has to mean force."""
    ids = await build_world(session)
    await session.execute(
        text("UPDATE users SET must_change_password = true WHERE id = :u"),
        {"u": ids["doctor"]},
    )
    await session.commit()

    body = await login(client, f"DOC{ids['tag']}")
    assert body["must_change_password"] is True
    headers = auth_header(body["access_token"])

    blocked = await client.get("/api/worklist", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json().get("must_change_password") is True


@pytest.mark.asyncio
async def test_changing_the_password_is_still_reachable(
    client: Any, session: AsyncSession
) -> None:
    """The exemption has to exist or the account is bricked."""
    ids = await build_world(session)
    await session.execute(
        text("UPDATE users SET must_change_password = true WHERE id = :u"),
        {"u": ids["doctor"]},
    )
    await session.commit()

    body = await login(client, f"DOC{ids['tag']}")
    headers = auth_header(body["access_token"])

    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200

    changed = await client.post(
        "/api/auth/change-password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": "Ward-Round-2026x",
        },
        headers=headers,
    )
    assert changed.status_code == 204

    # And now the system opens up.
    after = await login(client, f"DOC{ids['tag']}", "Ward-Round-2026x")
    assert after["must_change_password"] is False
    assert (
        await client.get("/api/worklist", headers=auth_header(after["access_token"]))
    ).status_code == 200


@pytest.mark.asyncio
async def test_changing_the_password_logs_every_other_session_out(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    first = await login(client, f"DOC{ids['tag']}")
    second = await login(client, f"DOC{ids['tag']}")

    await client.post(
        "/api/auth/change-password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": "Ward-Round-2026x",
        },
        headers=auth_header(second["access_token"]),
    )

    for tokens in (first, second):
        response = await client.post(
            "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_weak_new_password_is_refused(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    body = await login(client, f"DOC{ids['tag']}")
    response = await client.post(
        "/api/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": "short1A"},
        headers=auth_header(body["access_token"]),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_the_new_password_must_differ_from_the_old_one(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    body = await login(client, f"DOC{ids['tag']}")
    response = await client.post(
        "/api/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
        headers=auth_header(body["access_token"]),
    )
    assert response.status_code == 422


# ── RBAC ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_token_is_a_401(client: Any, session: AsyncSession) -> None:
    await build_world(session)
    assert (await client.get("/api/worklist")).status_code == 401


@pytest.mark.asyncio
async def test_a_forged_token_is_a_401(client: Any, session: AsyncSession) -> None:
    """Signed with the wrong key."""
    await build_world(session)
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": "admin",
            "exp": int((dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).timestamp()),
        },
        "not-the-real-secret",
        algorithm="HS256",
    )
    response = await client.get("/api/worklist", headers=auth_header(forged))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_an_expired_token_is_a_401(client: Any, session: AsyncSession) -> None:
    ids = await build_world(session)
    expired = jwt.encode(
        {
            "sub": ids["doctor"],
            "role": "doctor",
            "iat": int((dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).timestamp()),
            "exp": int((dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)).timestamp()),
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    response = await client.get("/api/worklist", headers=auth_header(expired))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_the_none_algorithm_is_refused(
    client: Any, session: AsyncSession
) -> None:
    """The classic JWT bypass. ``algorithms=["HS256"]`` is what stops it."""
    ids = await build_world(session)
    unsigned = jwt.encode(
        {
            "sub": ids["admin"],
            "role": "admin",
            "exp": int((dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).timestamp()),
        },
        key="",
        algorithm="none",
    )
    response = await client.get("/api/admin/users", headers=auth_header(unsigned))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_deactivated_user_loses_access_immediately(
    client: Any, session: AsyncSession
) -> None:
    """Revocation that waits fifteen minutes for token expiry is not
    revocation."""
    ids = await build_world(session)
    body = await login(client, f"DOC{ids['tag']}")
    headers = auth_header(body["access_token"])
    assert (await client.get("/api/worklist", headers=headers)).status_code == 200

    await session.execute(
        text("UPDATE users SET is_active = false WHERE id = :u"),
        {"u": ids["doctor"]},
    )
    await session.commit()

    assert (await client.get("/api/worklist", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_the_role_comes_from_the_database_not_the_token(
    client: Any, session: AsyncSession
) -> None:
    """A demotion must take effect now, not at the next refresh."""
    ids = await build_world(session)
    body = await login(client, f"ADM{ids['tag']}")
    headers = auth_header(body["access_token"])
    assert (await client.get("/api/admin/users", headers=headers)).status_code == 200

    await session.execute(
        text("UPDATE users SET role = 'doctor' WHERE id = :u"), {"u": ids["admin"]}
    )
    await session.commit()

    assert (await client.get("/api/admin/users", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_a_doctor_cannot_reach_the_admin_endpoints(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    assert (await client.get("/api/admin/users", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_a_lab_tech_cannot_close_a_case(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    headers = await bearer(client, ids, "labtech")
    response = await client.post(
        f"/api/cases/{ids['case']}/close",
        json={
            "closure_reason": "action_taken",
            "closure_note": "Patient recalled and reviewed",
        },
        headers=headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_auditor_is_read_only_on_every_endpoint(
    client: Any, session: AsyncSession
) -> None:
    """*"auditor sees all but read-only."*

    Enforced ahead of the role allow-list, so no future endpoint can grant an
    auditor write access by listing "auditor" in its decorator by accident.
    """
    ids = await build_world(session)
    headers = await bearer(client, ids, "auditor")

    assert (await client.get("/api/audit", headers=headers)).status_code == 200
    assert (await client.get("/api/worklist", headers=headers)).status_code == 200

    writes = [
        await client.post(
            f"/api/cases/{ids['case']}/close",
            json={"closure_reason": "action_taken", "closure_note": "Reviewed today"},
            headers=headers,
        ),
        await client.post(
            f"/api/cases/{ids['case']}/notes",
            json={"note": "a note"},
            headers=headers,
        ),
        await client.post(
            "/api/auth/break-glass",
            json={"reason": "Needed access to another ward tonight"},
            headers=headers,
        ),
    ]
    assert all(r.status_code == 403 for r in writes), [r.status_code for r in writes]


# ── row-level scoping ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_doctor_sees_their_own_departments_cases(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    mine = await extra_case(session, ids, severity="follow_up", hours_old=2)
    theirs = await extra_case(
        session,
        ids,
        severity="critical",
        hours_old=2,
        owner=ids["other_doctor"],
        department_id=ids["other_dept"],
    )

    headers = await bearer(client, ids, "doctor")
    rows = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    visible = {r["case_id"] for r in rows}

    assert ids["case"] in visible
    assert mine in visible
    assert theirs not in visible, "another department's case must not appear"


@pytest.mark.asyncio
async def test_an_out_of_scope_case_is_a_404_not_a_403(
    client: Any, session: AsyncSession
) -> None:
    """403 would confirm the case exists for a patient the caller named."""
    ids = await build_world(session)
    theirs = await extra_case(
        session,
        ids,
        severity="critical",
        hours_old=1,
        owner=ids["other_doctor"],
        department_id=ids["other_dept"],
    )

    headers = await bearer(client, ids, "doctor")
    response = await client.get(f"/api/cases/{theirs}/detail", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_doctor_cannot_close_a_case_outside_their_scope(
    client: Any, session: AsyncSession
) -> None:
    """The read path scopes in SQL; the write path has to check separately."""
    ids = await build_world(session)
    theirs = await extra_case(
        session,
        ids,
        severity="follow_up",
        hours_old=1,
        owner=ids["other_doctor"],
        department_id=ids["other_dept"],
    )

    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        f"/api/cases/{theirs}/close",
        json={
            "closure_reason": "not_clinically_relevant",
            "closure_note": "Not relevant to this patient's admission",
        },
        headers=headers,
    )
    assert response.status_code == 404

    still_open = (
        await session.execute(
            text("SELECT closed_at FROM pending_cases WHERE id = :c"), {"c": theirs}
        )
    ).scalar()
    assert still_open is None


@pytest.mark.asyncio
async def test_an_auditor_sees_every_department(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    theirs = await extra_case(
        session,
        ids,
        severity="critical",
        hours_old=1,
        owner=ids["other_doctor"],
        department_id=ids["other_dept"],
    )

    headers = await bearer(client, ids, "auditor")
    rows = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    visible = {r["case_id"] for r in rows}
    assert {ids["case"], theirs} <= visible


@pytest.mark.asyncio
async def test_a_doctor_with_no_department_sees_only_their_own_cases(
    client: Any, session: AsyncSession
) -> None:
    """A misconfigured account degrades to "no rows", never to "all rows"."""
    ids = await build_world(session)
    colleagues = await extra_case(
        session, ids, severity="critical", hours_old=1, owner=ids["head"]
    )
    await session.execute(
        text("UPDATE users SET department_id = NULL WHERE id = :u"),
        {"u": ids["doctor"]},
    )
    await session.commit()

    headers = await bearer(client, ids, "doctor")
    rows = (await client.get("/api/worklist", headers=headers)).json()["rows"]
    visible = {r["case_id"] for r in rows}

    assert ids["case"] in visible, "their own case must stay reachable"
    assert colleagues not in visible


# ── break-glass ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_break_glass_lifts_the_department_scope(
    client: Any, session: AsyncSession
) -> None:
    ids = await build_world(session)
    theirs = await extra_case(
        session,
        ids,
        severity="critical",
        hours_old=1,
        owner=ids["other_doctor"],
        department_id=ids["other_dept"],
    )
    headers = await bearer(client, ids, "doctor")

    assert (
        await client.get(f"/api/cases/{theirs}/detail", headers=headers)
    ).status_code == 404

    elevated = await client.post(
        "/api/auth/break-glass",
        json={"reason": "On call for Surgery tonight; ward phoned about this result."},
        headers=headers,
    )
    assert elevated.status_code == 200
    bg_headers = auth_header(elevated.json()["access_token"])

    assert (
        await client.get(f"/api/cases/{theirs}/detail", headers=bg_headers)
    ).status_code == 200


@pytest.mark.asyncio
async def test_break_glass_requires_a_real_reason(
    client: Any, session: AsyncSession
) -> None:
    """A box someone can fill with "." is not a control."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    response = await client.post(
        "/api/auth/break-glass", json={"reason": "."}, headers=headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_break_glass_is_logged_loudly(client: Any, session: AsyncSession) -> None:
    """*"logged loudly"* — its own action, its own column, its own index."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "doctor")
    reason = "Covering Surgery overnight, ward called about this patient"

    await client.post("/api/auth/break-glass", json={"reason": reason}, headers=headers)

    row = (
        await session.execute(
            text(
                "SELECT action, break_glass_reason, actor_user_id FROM audit_log "
                " WHERE action = 'auth.break_glass' ORDER BY seq DESC LIMIT 1"
            )
        )
    ).one()
    assert row.break_glass_reason == reason
    assert str(row.actor_user_id) == ids["doctor"]


@pytest.mark.asyncio
async def test_a_lab_tech_cannot_break_glass(
    client: Any, session: AsyncSession
) -> None:
    """No business reading another department's clinical cases at all."""
    ids = await build_world(session)
    headers = await bearer(client, ids, "labtech")
    response = await client.post(
        "/api/auth/break-glass",
        json={"reason": "I would like to look at everything please"},
        headers=headers,
    )
    assert response.status_code == 403
