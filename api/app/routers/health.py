"""Health and version endpoints. Phase 0.5.

``/api/health`` returns 200 whenever NODE A itself is serviceable. NODE B
being unreachable is reported in the ``llm`` block and listed in
``degraded_features`` — it is never an error status, because the whole point
of the two-node design is that NODE B is optional to the workflow.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_session

router = APIRouter(tags=["health"])

# Phase 10.4 alerts when the worker has been silent longer than this.
WORKER_STALE_AFTER_S = 120


@router.get("/health")
async def health(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    degraded: list[str] = []

    # ── database ──────────────────────────────────────────────────
    db_ok = True
    worker_age: int | None = None
    try:
        await session.execute(text("SELECT 1"))
        result = await session.execute(
            text(
                "SELECT EXTRACT(EPOCH FROM (now() - MAX(last_beat_at)))::int "
                "FROM worker_health"
            )
        )
        worker_age = result.scalar()
    except Exception:
        db_ok = False
        degraded.append("database")

    if worker_age is None or worker_age > WORKER_STALE_AFTER_S:
        degraded.append("worker")

    # ── NODE B ────────────────────────────────────────────────────
    # Cached and non-blocking. See app/services/llm_probe.py.
    llm_status = await request.app.state.llm_probe.status()
    if not llm_status.reachable:
        degraded.append("llm_generation")

    body: dict[str, Any] = {
        # Only NODE A's own health decides this. An offline NODE B leaves
        # status "ok" -- tracking, timers and escalation are unaffected.
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "version": settings.version,
        "git_sha": settings.git_sha,
        "worker_heartbeat_age_s": worker_age,
        "llm": llm_status.to_dict(),
        "degraded_features": degraded,
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=body)


@router.get("/version")
async def version(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {"version": settings.version, "git_sha": settings.git_sha, "env": settings.env}
