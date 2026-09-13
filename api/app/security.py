"""RBAC dependencies and row-level scoping. Phase 5.1.

    RBAC dependency on **every** endpoint — ``require_role("doctor","unit_head")``
    Row-level scoping: a doctor sees their department's cases; auditor sees all
    but **read-only**

Three layers, each doing one job:

1. :func:`current_user` — is this a valid token at all?
2. :func:`require_role` — is this role allowed to call this endpoint?
3. :func:`scope_clause` — which *rows* may this caller see?

Layer 3 is the one that is easy to skip and expensive to skip. Role checks
guard verbs; without a row filter, a doctor with a legitimate ``GET /cases``
right reads the whole hospital. The filter is returned as SQL rather than
applied in Python on purpose — filtering after the fetch means the rows were
already read, paginated and counted wrong.

**The auditor is read-only and it is enforced here, not by convention.**
:func:`require_role` refuses an auditor on any non-GET request regardless of
which roles the endpoint named, so no future endpoint can grant one write
access by listing ``"auditor"`` in its decorator by accident.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_session
from app.services.auth import (
    AuthenticatedUser,
    AuthenticationError,
    decode_access_token,
)

log = structlog.get_logger(__name__)

ROLE_DOCTOR = "doctor"
ROLE_UNIT_HEAD = "unit_head"
ROLE_LAB_TECH = "lab_tech"
ROLE_ADMIN = "admin"
ROLE_AUDITOR = "auditor"

# Roles that may see every department's rows without break-glass. A unit head
# is scoped to their own department; an admin and an auditor are hospital-wide
# by the nature of the job.
UNSCOPED_ROLES = frozenset({ROLE_ADMIN, ROLE_AUDITOR})

# Methods an auditor may use. GET and HEAD only -- "sees all but read-only".
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Endpoints reachable while `must_change_password` is set. Everything else is
# refused until the password is changed, which is what makes "force" in
# "force password change on first login" mean something.
PASSWORD_CHANGE_EXEMPT_PATHS = frozenset(
    {
        "/api/auth/change-password",
        "/api/auth/logout",
        "/api/auth/me",
    }
)

_bearer = HTTPBearer(auto_error=False, description="Bearer access token")


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedUser:
    """Resolve the bearer token into a user.

    The token is verified cryptographically *and* checked against the
    database: a user deactivated or deleted five minutes ago still holds a
    valid-looking access token for up to fifteen, and revocation that waits
    for expiry is not revocation. One indexed primary-key lookup per request
    is the right price for that.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorised("Missing bearer token")

    try:
        claimed = decode_access_token(credentials.credentials, settings)
    except AuthenticationError as exc:
        raise _unauthorised("Invalid or expired token") from exc

    from sqlalchemy import text as sql_text

    row = (
        await session.execute(
            sql_text(
                "SELECT id, employee_code, full_name, role, department_id, "
                "       is_active, must_change_password "
                "  FROM users WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": str(claimed.id)},
        )
    ).first()
    if row is None or not row.is_active:
        raise _unauthorised("Account is no longer active")

    user = AuthenticatedUser(
        id=row.id,
        employee_code=row.employee_code,
        full_name=row.full_name,
        # The database is the authority on role and department, not the token.
        # A demotion must take effect now, not at the next refresh.
        role=row.role,
        department_id=row.department_id,
        must_change_password=bool(row.must_change_password),
        break_glass=claimed.break_glass,
    )
    request.state.user = user
    return user


def require_role(
    *roles: str,
) -> Callable[..., Coroutine[Any, Any, AuthenticatedUser]]:
    """The dependency that goes on **every** endpoint.

    ``roles`` is the allow-list. An empty list means "any authenticated user",
    which is used only where the endpoint is about the caller themselves
    (``/auth/me``, ``/auth/logout``).
    """
    allowed = frozenset(roles)

    async def _dependency(
        request: Request, user: AuthenticatedUser = Depends(current_user)
    ) -> AuthenticatedUser:
        # Read-only first: an auditor never writes, whatever the endpoint says.
        if user.role == ROLE_AUDITOR and request.method not in READ_ONLY_METHODS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The auditor role is read-only.",
            )

        if allowed and user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This endpoint requires one of: {', '.join(sorted(allowed))}. "
                    f"Your role is {user.role}."
                ),
            )

        if (
            user.must_change_password
            and request.url.path.rstrip("/") not in PASSWORD_CHANGE_EXEMPT_PATHS
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "title": "Password change required",
                    "detail": (
                        "You must change your password before using the system."
                    ),
                    "must_change_password": True,
                },
            )
        return user

    return _dependency


class Scope:
    """A row filter, expressed as SQL a query can append.

    Returned rather than applied so the caller decides the alias — the same
    scope has to work against ``pending_cases pc``, a joined worklist view and
    a COUNT(*), and a hardcoded table name would only work for one of them.
    """

    def __init__(self, clause: str, params: dict[str, Any], *, unscoped: bool) -> None:
        self.clause = clause
        self.params = params
        self.unscoped = unscoped

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return not self.unscoped


def scope_clause(
    user: AuthenticatedUser,
    *,
    department_expr: str = "e.department_id",
    owner_expr: str = "pc.current_owner_id",
) -> Scope:
    """Which case rows this caller may see.

    Both filters are passed as **SQL expressions**, not column names, because
    a case has no department of its own — it inherits the one on its
    ``encounters`` row, so every scoped query has to join and the alias
    differs per query.

    * **admin / auditor** — everything.
    * **unit head** — their department, per ADR 0004: the unit head owns the
      roster and the overdue list for their unit.
    * **doctor** — their department. The plan says *"a doctor sees their
      department's cases"*, not only their own: a doctor covering a colleague's
      list must be able to open the case they were just phoned about, and a
      filter narrower than the plan would turn a routine handover into a
      break-glass event.
    * **break-glass** — everything, and the audit row already says why.
    * **no department** — nothing. A doctor with a NULL ``department_id`` is a
      misconfigured account, and the safe reading of a missing scope is "no
      rows", never "all rows".
    """
    if user.role in UNSCOPED_ROLES or user.break_glass:
        return Scope("TRUE", {}, unscoped=True)

    if user.department_id is None:
        log.warning(
            "user_has_no_department",
            user_id=str(user.id),
            employee_code=user.employee_code,
            role=user.role,
        )
        # Still allow their own cases through: an unassigned doctor who owns a
        # case must be able to act on it, or the case is stranded.
        return Scope(
            f"{owner_expr} = :scope_user_id",
            {"scope_user_id": str(user.id)},
            unscoped=False,
        )

    return Scope(
        f"({department_expr} = :scope_department_id "
        f" OR {owner_expr} = :scope_user_id)",
        {
            "scope_department_id": str(user.department_id),
            "scope_user_id": str(user.id),
        },
        unscoped=False,
    )


def assert_can_see(
    user: AuthenticatedUser,
    *,
    department_id: uuid.UUID | None,
    owner_id: uuid.UUID | None,
) -> None:
    """The single-row form of :func:`scope_clause`.

    Raises **404, not 403**. Telling an out-of-scope caller "this exists but
    is not yours" confirms that a case exists for a patient they named, which
    is itself the disclosure the scoping is there to prevent.
    """
    if user.role in UNSCOPED_ROLES or user.break_glass:
        return
    if owner_id is not None and owner_id == user.id:
        return
    if department_id is not None and department_id == user.department_id:
        return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")


def client_ip(request: Request) -> str | None:
    """The caller's address for the audit log.

    ``X-Forwarded-For`` is trusted because the only deployment is behind
    Caddy on NODE A's own host — nothing else can reach the API port. That
    assumption is written down here so it is re-examined if the API is ever
    exposed directly, where the header is attacker-controlled.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None
