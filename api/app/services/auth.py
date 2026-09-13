"""Login, tokens and lockout. Phase 5.1.

    Login with ``employee_code`` + password (**Argon2id**)
    JWT access token (**15 min**) + refresh token (**12h**, rotated, stored
    hashed in ``sessions``)
    Force password change on first login; password policy
    Account lockout after **5 failures for 15 min**

Two decisions worth stating, because both are load-bearing:

**The failure counter lives on the user, not the IP.** The thing being
protected is the account; an attacker who rotates source addresses walks
straight past an IP-keyed counter. The cost is that someone can lock a
colleague out by guessing at their code — which is why the lock is 15 minutes
and not permanent, and why every lock is audited so a pattern of them is
visible.

**Login is deliberately uniform in its failure.** Unknown code, wrong
password, deactivated account and locked account all return the same 401 with
the same message. Anything else is an account-enumeration oracle: a hospital
directory of employee codes is not secret, but confirming which ones are live
accounts hands an attacker the list to spray. The *audit log* records exactly
which of the four it was — the operator learns the difference, the caller does
not.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, cast

import jwt
import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services import audit

log = structlog.get_logger(__name__)

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 12
JWT_ALGORITHM = "HS256"

# Argon2id at library defaults, which are the RFC 9106 low-memory profile
# (64 MiB, t=3, p=4). Deliberately not tuned down: a login happens a few times
# per shift, so ~50ms of hashing is invisible to the user and expensive for
# anyone working through a stolen dump.
_hasher = PasswordHasher()


class AuthenticationError(Exception):
    """Login failed. Carries the real reason for the audit log only."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PasswordPolicyError(Exception):
    """The proposed password is not acceptable."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def hash_refresh_token(token: str) -> str:
    """SHA-256, not Argon2.

    A refresh token is 256 bits of ``secrets`` output, not a human-chosen
    password: there is no dictionary to attack, so a slow hash buys nothing
    and would be paid on every refresh.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_password_policy(password: str, *, employee_code: str | None = None) -> None:
    """*"Force password change on first login; password policy."*

    Length first and loudest. Composition rules produce ``Passw0rd!`` and
    stop there, so the floor is 12 characters, with the classes as a weak
    secondary check rather than the whole policy.
    """
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"must be at least {MIN_PASSWORD_LENGTH} characters")
    if not re.search(r"[a-z]", password) or not re.search(r"[A-Z]", password):
        problems.append("must contain both upper and lower case letters")
    if not re.search(r"\d", password):
        problems.append("must contain a digit")
    if employee_code and employee_code.lower() in password.lower():
        problems.append("must not contain the employee code")
    if problems:
        raise PasswordPolicyError("Password " + "; ".join(problems) + ".")


@dataclass(frozen=True)
class AuthenticatedUser:
    """Who the caller is, as far as any endpoint is concerned."""

    id: uuid.UUID
    employee_code: str
    full_name: str
    role: str
    department_id: uuid.UUID | None
    must_change_password: bool
    # Set only when the token was minted through the break-glass path, so an
    # endpoint can refuse it and the audit row can say so.
    break_glass: bool = False


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    must_change_password: bool
    user: AuthenticatedUser


def _access_token(
    user: AuthenticatedUser, settings: Settings, *, session_id: uuid.UUID
) -> tuple[str, int]:
    now = dt.datetime.now(dt.UTC)
    ttl = dt.timedelta(minutes=settings.jwt_access_ttl_minutes)
    claims: dict[str, Any] = {
        "sub": str(user.id),
        "code": user.employee_code,
        "role": user.role,
        "dept": str(user.department_id) if user.department_id else None,
        "mcp": user.must_change_password,
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if user.break_glass:
        claims["bg"] = True
    return jwt.encode(claims, settings.jwt_secret, algorithm=JWT_ALGORITHM), int(
        ttl.total_seconds()
    )


def decode_access_token(token: str, settings: Settings) -> AuthenticatedUser:
    """Verify and unpack an access token.

    ``require_exp`` matters: without it a token minted without an ``exp``
    claim — which this code never does, but a forgery attempt would — would
    verify forever.
    """
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError(f"invalid_token:{type(exc).__name__}") from exc

    dept = claims.get("dept")
    return AuthenticatedUser(
        id=uuid.UUID(str(claims["sub"])),
        employee_code=str(claims.get("code", "")),
        full_name="",
        role=str(claims.get("role", "")),
        department_id=uuid.UUID(str(dept)) if dept else None,
        must_change_password=bool(claims.get("mcp", False)),
        break_glass=bool(claims.get("bg", False)),
    )


async def _load_user(session: AsyncSession, employee_code: str) -> Any:
    return (
        await session.execute(
            text(
                "SELECT id, employee_code, full_name, role, department_id, "
                "       password_hash, is_active, must_change_password, "
                "       failed_login_count, locked_until "
                "  FROM users "
                " WHERE employee_code = :code AND deleted_at IS NULL "
                "   FOR UPDATE"
            ),
            {"code": employee_code},
        )
    ).first()


async def _register_failure(
    session: AsyncSession,
    row: Any,
    *,
    reason: str,
    actor_ip: str | None,
    now: dt.datetime,
) -> None:
    """Count the failure, lock at the threshold, audit either way."""
    count = int(row.failed_login_count) + 1
    locked_until: dt.datetime | None = None
    if count >= MAX_FAILED_LOGINS:
        locked_until = now + dt.timedelta(minutes=LOCKOUT_MINUTES)

    await session.execute(
        text(
            "UPDATE users SET failed_login_count = :n, locked_until = :until, "
            "       updated_at = now() WHERE id = :id"
        ),
        {"n": count, "until": locked_until, "id": str(row.id)},
    )
    await audit.append(
        session,
        action=audit.ACTION_LOGIN_FAILED,
        entity_type="user",
        entity_id=str(row.id),
        actor_user_id=row.id,
        actor_ip=actor_ip,
        after={"reason": reason, "failed_login_count": count},
        occurred_at=now,
    )
    if locked_until is not None:
        await audit.append(
            session,
            action=audit.ACTION_ACCOUNT_LOCKED,
            entity_type="user",
            entity_id=str(row.id),
            actor_user_id=row.id,
            actor_ip=actor_ip,
            after={"locked_until": locked_until, "failures": count},
            occurred_at=now,
        )
        log.warning(
            "account_locked",
            user_id=str(row.id),
            employee_code=row.employee_code,
            failures=count,
        )


async def login(
    session: AsyncSession,
    *,
    employee_code: str,
    password: str,
    settings: Settings,
    actor_ip: str | None = None,
    user_agent: str | None = None,
) -> TokenPair:
    """Authenticate and issue a token pair. **Does not commit.**

    The caller commits — which is what makes the failure counter and its audit
    row durable even on the paths that end in an exception, since the router
    commits before translating ``AuthenticationError`` into a 401. A rollback
    there would hand an attacker unlimited free guesses.
    """
    now = dt.datetime.now(dt.UTC)
    row = await _load_user(session, employee_code)

    if row is None:
        # Spend the time anyway. Returning instantly for an unknown code and
        # slowly for a known one is a timing oracle that enumerates the staff
        # directory; hashing a throwaway keeps both paths the same shape.
        _hasher.hash(password)
        await audit.append(
            session,
            action=audit.ACTION_LOGIN_FAILED,
            entity_type="user",
            entity_id=employee_code[:64],
            actor_ip=actor_ip,
            after={"reason": "unknown_employee_code"},
            occurred_at=now,
        )
        raise AuthenticationError("unknown_employee_code")

    if row.locked_until is not None and row.locked_until > now:
        await audit.append(
            session,
            action=audit.ACTION_LOGIN_FAILED,
            entity_type="user",
            entity_id=str(row.id),
            actor_user_id=row.id,
            actor_ip=actor_ip,
            after={"reason": "account_locked", "locked_until": row.locked_until},
            occurred_at=now,
        )
        raise AuthenticationError("account_locked")

    if not row.is_active:
        await audit.append(
            session,
            action=audit.ACTION_LOGIN_FAILED,
            entity_type="user",
            entity_id=str(row.id),
            actor_user_id=row.id,
            actor_ip=actor_ip,
            after={"reason": "account_inactive"},
            occurred_at=now,
        )
        raise AuthenticationError("account_inactive")

    if not row.password_hash or not verify_password(row.password_hash, password):
        await _register_failure(
            session, row, reason="bad_password", actor_ip=actor_ip, now=now
        )
        raise AuthenticationError("bad_password")

    # Success. Clear the counter — five failures then a success is a person
    # who mistyped, not an attack in progress.
    await session.execute(
        text(
            "UPDATE users SET failed_login_count = 0, locked_until = NULL, "
            "       last_login_at = :now, updated_at = now() WHERE id = :id"
        ),
        {"now": now, "id": str(row.id)},
    )

    user = AuthenticatedUser(
        id=row.id,
        employee_code=row.employee_code,
        full_name=row.full_name,
        role=row.role,
        department_id=row.department_id,
        must_change_password=bool(row.must_change_password),
    )
    pair = await _issue_session(
        session,
        user,
        settings=settings,
        actor_ip=actor_ip,
        user_agent=user_agent,
        now=now,
    )
    await audit.append(
        session,
        action=audit.ACTION_LOGIN_SUCCEEDED,
        entity_type="user",
        entity_id=str(row.id),
        actor_user_id=row.id,
        actor_ip=actor_ip,
        after={"must_change_password": user.must_change_password},
        occurred_at=now,
    )
    return pair


async def _issue_session(
    session: AsyncSession,
    user: AuthenticatedUser,
    *,
    settings: Settings,
    actor_ip: str | None,
    user_agent: str | None,
    now: dt.datetime,
    predecessor_id: uuid.UUID | None = None,
) -> TokenPair:
    """Mint a refresh token, store only its hash, and return both tokens."""
    refresh_token = secrets.token_urlsafe(32)
    session_id = uuid.uuid4()
    expires_at = now + dt.timedelta(hours=settings.jwt_refresh_ttl_hours)

    await session.execute(
        text(
            "INSERT INTO sessions (id, user_id, refresh_token_hash, issued_at, "
            "                      expires_at, user_agent, ip_address, "
            "                      created_at, updated_at) "
            "VALUES (:id, :uid, :hash, :now, :exp, :ua, CAST(:ip AS inet), "
            "        :now, :now)"
        ),
        {
            "id": str(session_id),
            "uid": str(user.id),
            "hash": hash_refresh_token(refresh_token),
            "now": now,
            "exp": expires_at,
            "ua": (user_agent or None) and user_agent[:300],
            "ip": actor_ip,
        },
    )
    if predecessor_id is not None:
        await session.execute(
            text(
                "UPDATE sessions SET rotated_to_id = :new, revoked_at = :now, "
                "       revoked_reason = 'rotated', updated_at = :now "
                " WHERE id = :old"
            ),
            {"new": str(session_id), "old": str(predecessor_id), "now": now},
        )

    access_token, ttl = _access_token(user, settings, session_id=session_id)
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ttl,
        must_change_password=user.must_change_password,
        user=user,
    )


async def refresh(
    session: AsyncSession,
    *,
    refresh_token: str,
    settings: Settings,
    actor_ip: str | None = None,
    user_agent: str | None = None,
) -> TokenPair:
    """Rotate a refresh token. **Single use.**

    A token that has already been rotated is presented only by a bug or a
    theft, and there is no way to tell which from here. So the whole family is
    revoked: the legitimate holder is logged out and has to sign in again,
    which is a far cheaper outcome than letting a stolen token keep minting
    access tokens for twelve hours.
    """
    now = dt.datetime.now(dt.UTC)
    row = (
        await session.execute(
            text(
                "SELECT s.id, s.user_id, s.expires_at, s.revoked_at, s.rotated_to_id, "
                "       u.employee_code, u.full_name, u.role, u.department_id, "
                "       u.is_active, u.must_change_password "
                "  FROM sessions s JOIN users u ON u.id = s.user_id "
                " WHERE s.refresh_token_hash = :hash AND s.deleted_at IS NULL "
                "   FOR UPDATE OF s"
            ),
            {"hash": hash_refresh_token(refresh_token)},
        )
    ).first()

    if row is None:
        raise AuthenticationError("unknown_refresh_token")

    if row.revoked_at is not None or row.rotated_to_id is not None:
        await _revoke_family(session, row.user_id, reason="reuse_detected", now=now)
        await audit.append(
            session,
            action=audit.ACTION_LOGIN_FAILED,
            entity_type="session",
            entity_id=str(row.id),
            actor_user_id=row.user_id,
            actor_ip=actor_ip,
            after={"reason": "refresh_token_reuse", "action": "all_sessions_revoked"},
            occurred_at=now,
        )
        log.warning("refresh_token_reuse", user_id=str(row.user_id))
        raise AuthenticationError("refresh_token_reuse")

    if row.expires_at <= now:
        raise AuthenticationError("refresh_token_expired")
    if not row.is_active:
        raise AuthenticationError("account_inactive")

    user = AuthenticatedUser(
        id=row.user_id,
        employee_code=row.employee_code,
        full_name=row.full_name,
        role=row.role,
        department_id=row.department_id,
        must_change_password=bool(row.must_change_password),
    )
    pair = await _issue_session(
        session,
        user,
        settings=settings,
        actor_ip=actor_ip,
        user_agent=user_agent,
        now=now,
        predecessor_id=row.id,
    )
    await audit.append(
        session,
        action=audit.ACTION_TOKEN_REFRESHED,
        entity_type="session",
        entity_id=str(row.id),
        actor_user_id=row.user_id,
        actor_ip=actor_ip,
        occurred_at=now,
    )
    return pair


async def _revoke_family(
    session: AsyncSession, user_id: uuid.UUID, *, reason: str, now: dt.datetime
) -> int:
    result = await session.execute(
        text(
            "UPDATE sessions SET revoked_at = :now, revoked_reason = :why, "
            "       updated_at = :now "
            " WHERE user_id = :uid AND revoked_at IS NULL AND deleted_at IS NULL"
        ),
        {"now": now, "why": reason[:40], "uid": str(user_id)},
    )
    # `AsyncSession.execute` is typed as returning `Result`, which has no
    # `rowcount`; an UPDATE actually returns a `CursorResult`, which does.
    return cast("CursorResult[Any]", result).rowcount or 0


async def logout(
    session: AsyncSession,
    *,
    refresh_token: str | None,
    user_id: uuid.UUID,
    actor_ip: str | None = None,
    all_sessions: bool = False,
) -> int:
    """Revoke this session, or every session for the user."""
    now = dt.datetime.now(dt.UTC)
    if all_sessions or refresh_token is None:
        revoked = await _revoke_family(session, user_id, reason="logout", now=now)
    else:
        result = await session.execute(
            text(
                "UPDATE sessions SET revoked_at = :now, revoked_reason = 'logout', "
                "       updated_at = :now "
                " WHERE refresh_token_hash = :hash AND user_id = :uid "
                "   AND revoked_at IS NULL"
            ),
            {
                "now": now,
                "hash": hash_refresh_token(refresh_token),
                "uid": str(user_id),
            },
        )
        revoked = cast("CursorResult[Any]", result).rowcount or 0

    await audit.append(
        session,
        action=audit.ACTION_LOGOUT,
        entity_type="user",
        entity_id=str(user_id),
        actor_user_id=user_id,
        actor_ip=actor_ip,
        after={"sessions_revoked": revoked, "all_sessions": all_sessions},
        occurred_at=now,
    )
    return revoked


async def change_password(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    current_password: str,
    new_password: str,
    actor_ip: str | None = None,
) -> None:
    """Change a password and log every other session out.

    Revoking the other sessions is the point of changing a password after a
    suspected compromise — leaving them live would make the change cosmetic.
    """
    now = dt.datetime.now(dt.UTC)
    row = (
        await session.execute(
            text(
                "SELECT id, employee_code, password_hash FROM users "
                " WHERE id = :id AND deleted_at IS NULL FOR UPDATE"
            ),
            {"id": str(user_id)},
        )
    ).first()
    if row is None:
        raise AuthenticationError("unknown_user")
    if not row.password_hash or not verify_password(
        row.password_hash, current_password
    ):
        raise AuthenticationError("bad_password")
    if new_password == current_password:
        raise PasswordPolicyError("The new password must differ from the current one.")

    check_password_policy(new_password, employee_code=row.employee_code)

    await session.execute(
        text(
            "UPDATE users SET password_hash = :hash, must_change_password = false, "
            "       password_changed_at = :now, failed_login_count = 0, "
            "       locked_until = NULL, updated_at = :now, updated_by = :id "
            " WHERE id = :id"
        ),
        {"hash": hash_password(new_password), "now": now, "id": str(user_id)},
    )
    await _revoke_family(session, user_id, reason="password_changed", now=now)
    await audit.append(
        session,
        action=audit.ACTION_PASSWORD_CHANGED,
        entity_type="user",
        entity_id=str(user_id),
        actor_user_id=user_id,
        actor_ip=actor_ip,
        after={"other_sessions_revoked": True},
        occurred_at=now,
    )


MIN_BREAK_GLASS_REASON = 20


async def break_glass(
    session: AsyncSession,
    *,
    user: AuthenticatedUser,
    reason: str,
    settings: Settings,
    actor_ip: str | None = None,
    user_agent: str | None = None,
) -> TokenPair:
    """*"Break-glass access with mandatory reason, **logged loudly**."*

    Issues a second token pair carrying ``bg: true``, which lifts the
    department scoping in :mod:`app.security` for a clinician who needs a case
    outside their own unit — a night-duty doctor covering another ward, a
    consultant called about a patient they did not discharge.

    "Loudly" is four things, not one: an audit row with a dedicated
    ``break_glass_reason`` column, a partial index so listing them is one
    query, a WARNING in the structured log, and the reason floor below. A
    free-text box someone can fill with ``"."`` is not a control, so twenty
    characters is the minimum — enough that the writer has to say something,
    short enough not to stop a real emergency.
    """
    if len(reason.strip()) < MIN_BREAK_GLASS_REASON:
        raise PasswordPolicyError(
            f"Break-glass reason must be at least {MIN_BREAK_GLASS_REASON} "
            "characters and describe why access outside your department is "
            "needed."
        )
    now = dt.datetime.now(dt.UTC)
    elevated = AuthenticatedUser(
        id=user.id,
        employee_code=user.employee_code,
        full_name=user.full_name,
        role=user.role,
        department_id=user.department_id,
        must_change_password=user.must_change_password,
        break_glass=True,
    )
    pair = await _issue_session(
        session,
        elevated,
        settings=settings,
        actor_ip=actor_ip,
        user_agent=user_agent,
        now=now,
    )
    await audit.append(
        session,
        action=audit.ACTION_BREAK_GLASS,
        entity_type="user",
        entity_id=str(user.id),
        actor_user_id=user.id,
        actor_ip=actor_ip,
        after={"role": user.role, "department_id": user.department_id},
        break_glass_reason=reason.strip(),
        occurred_at=now,
    )
    log.warning(
        "break_glass_access",
        user_id=str(user.id),
        employee_code=user.employee_code,
        reason=reason.strip(),
    )
    return pair
