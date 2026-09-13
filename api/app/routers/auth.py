"""``/api/auth`` — login, refresh, logout, password change, break-glass. 5.1.

Every failure path here commits before raising. That looks wrong at a glance
and is the only correct behaviour: the failure counter, the lockout and the
audit row are all written on the way to a 401, and a rollback would discard
them — handing an attacker unlimited free guesses against an account that is
supposed to lock after five.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_session
from app.schemas.auth import (
    BreakGlassRequest,
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    TokenResponse,
    UserOut,
)
from app.security import client_ip, require_role
from app.services import auth as auth_service
from app.services.auth import (
    AuthenticatedUser,
    AuthenticationError,
    PasswordPolicyError,
    TokenPair,
)
from app.services.rate_limit import login_limiter

router = APIRouter(prefix="/auth", tags=["auth"])

# One message for every login failure. Unknown code, wrong password, locked
# and deactivated are indistinguishable to the caller by design -- the audit
# log knows which, the attacker does not.
_LOGIN_FAILED = "Invalid employee code or password, or the account is unavailable."


def _to_response(pair: TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        must_change_password=pair.must_change_password,
        user=UserOut(
            id=pair.user.id,
            employee_code=pair.user.employee_code,
            full_name=pair.user.full_name,
            role=pair.user.role,
            department_id=pair.user.department_id,
            must_change_password=pair.user.must_change_password,
            break_glass=pair.user.break_glass,
        ),
    )


def _enforce_rate_limit(request: Request, settings: Settings) -> None:
    # Read per request: the setting is cached by `get_settings`, and taking it
    # here rather than at import time means a redeploy with a different value
    # takes effect without the limiter needing to be reconstructed.
    login_limiter.limit = settings.auth_rate_limit_per_minute
    key = client_ip(request) or "unknown"
    allowed, retry_after = login_limiter.check(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Try again shortly.",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in with employee code and password (Argon2id)",
)
async def login(
    request: Request,
    payload: LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """* **200** tokens issued
    * **401** any authentication failure — deliberately indistinguishable
    * **429** too many attempts from this address
    """
    _enforce_rate_limit(request, settings)
    try:
        pair = await auth_service.login(
            session,
            employee_code=payload.employee_code,
            password=payload.password,
            settings=settings,
            actor_ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AuthenticationError as exc:
        # Commit the counter and the audit row, then fail.
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_LOGIN_FAILED,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    await session.commit()
    return _to_response(pair)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate a refresh token — single use, reuse revokes everything",
)
async def refresh(
    request: Request,
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    _enforce_rate_limit(request, settings)
    try:
        pair = await auth_service.refresh(
            session,
            refresh_token=payload.refresh_token,
            settings=settings,
            actor_ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AuthenticationError as exc:
        # Reuse detection revokes the family; that write must survive the 401.
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is not valid. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    await session.commit()
    return _to_response(pair)


@router.post("/logout", response_model=LogoutResponse, summary="Revoke sessions")
async def logout(
    request: Request,
    payload: LogoutRequest,
    user: AuthenticatedUser = Depends(require_role()),
    session: AsyncSession = Depends(get_session),
) -> LogoutResponse:
    revoked = await auth_service.logout(
        session,
        refresh_token=payload.refresh_token,
        user_id=user.id,
        actor_ip=client_ip(request),
        all_sessions=payload.all_sessions,
    )
    await session.commit()
    return LogoutResponse(sessions_revoked=revoked)


@router.get("/me", response_model=UserOut, summary="Who am I?")
async def me(user: AuthenticatedUser = Depends(require_role())) -> UserOut:
    return UserOut(
        id=user.id,
        employee_code=user.employee_code,
        full_name=user.full_name,
        role=user.role,
        department_id=user.department_id,
        must_change_password=user.must_change_password,
        break_glass=user.break_glass,
    )


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change password — revokes every other session",
)
async def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    user: AuthenticatedUser = Depends(require_role()),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        await auth_service.change_password(
            session,
            user_id=user.id,
            current_password=payload.current_password,
            new_password=payload.new_password,
            actor_ip=client_ip(request),
        )
    except AuthenticationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect.",
        ) from exc
    except PasswordPolicyError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/break-glass",
    response_model=TokenResponse,
    summary="Emergency access outside your department — reason mandatory, logged",
)
async def break_glass(
    request: Request,
    payload: BreakGlassRequest,
    user: AuthenticatedUser = Depends(require_role("doctor", "unit_head")),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """Issues a second token that lifts department scoping.

    Restricted to clinicians: an admin and an auditor are already unscoped, so
    there is nothing to break, and a lab tech has no business reading another
    department's cases at all.
    """
    try:
        pair = await auth_service.break_glass(
            session,
            user=user,
            reason=payload.reason,
            settings=settings,
            actor_ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except PasswordPolicyError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await session.commit()
    return _to_response(pair)
