"""Request and response bodies for ``/api/auth``. Phase 5.1."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_code: str = Field(min_length=1, max_length=64)
    # Capped, not for policy but for safety: Argon2 will happily hash a 10 MB
    # string and spend real CPU doing it, which makes an unbounded password
    # field a denial-of-service vector on an endpoint that is reachable before
    # authentication. Phase 5.7's "input size limits" starts here.
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: uuid.UUID
    employee_code: str
    full_name: str
    role: str
    department_id: uuid.UUID | None = None
    must_change_password: bool = False
    break_glass: bool = False


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool
    user: UserOut


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1, max_length=512)


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = Field(default=None, max_length=512)
    all_sessions: bool = False


class LogoutResponse(BaseModel):
    sessions_revoked: int


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class BreakGlassRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The floor is enforced in the service too -- this one gives the UI a 422
    # with a field location instead of a generic error.
    reason: str = Field(min_length=20, max_length=1000)
