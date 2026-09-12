"""Staff directory. Read-only. Phase 1.3 addendum for the 1.4 gate screen.

Exists so Step 2's responsible-doctor select has something to search. Full
user management -- create, deactivate, roles, passwords -- is Phase 5.4 and is
deliberately absent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.directory import UserSummary
from app.services.directory import list_users

router = APIRouter(prefix="/users", tags=["directory"])


@router.get("", response_model=list[UserSummary], summary="Search staff")
async def search_users(
    role: str | None = Query(default=None, description="e.g. doctor, unit_head"),
    q: str | None = Query(default=None, description="name or employee code"),
    active_only: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[UserSummary]:
    """Read-only. Returns identity and role only -- never credentials.

    No availability is reported: that needs duty_roster / user_absences, which
    arrive in Phase 4.1. `is_active` is the only liveness the schema has.
    """
    return await list_users(
        session, role=role, query=q, active_only=active_only, limit=limit
    )
