"""Result Guardian — NODE A API entrypoint. Phase 0.5."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.session import dispose_engine
from app.errors import register_exception_handlers
from app.logging import RequestIdMiddleware, configure_logging
from app.routers import (
    admin,
    audit,
    auth,
    cases,
    documents,
    encounters,
    health,
    lab_flags,
    orders,
    ownership,
    patients,
    reports,
    users,
    webhooks,
    worklist,
)
from app.security import require_role
from app.services.llm_probe import LlmProbe

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    # One probe instance for the process so the 30s cache is actually shared.
    app.state.llm_probe = LlmProbe(settings)

    log.info(
        "api_starting",
        env=settings.env,
        version=settings.version,
        llm_host=settings.llm_base_url,
        llm_enabled=settings.llm_enabled,
    )
    # Deliberately NOT probing NODE B at startup: the API must boot with NODE B
    # off. Reachability is discovered lazily on the first /api/health call.
    yield
    await dispose_engine()
    log.info("api_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Result Guardian API",
        version=settings.version,
        docs_url=None if settings.is_prod else "/api/docs",
        openapi_url=None if settings.is_prod else "/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    # ── unauthenticated, deliberately ─────────────────────────────
    # Health is probed by the container runtime and by a browser that has not
    # signed in yet; login and refresh are how a token is obtained at all.
    # Everything else below is behind RBAC.
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    # The SMS provider's delivery-receipt callback. No user token is possible;
    # it authenticates with an optional shared secret instead. See the module.
    app.include_router(webhooks.router, prefix="/api")

    # ── Phases 1-4, retro-fitted with RBAC in Phase 5.1 ───────────
    #
    # 5.1 says *"RBAC dependency on **every** endpoint"*, and until Phase 5
    # these routers had none: anyone who could reach NODE A's port could read
    # a patient record or discharge a patient. Applying the dependency at
    # `include_router` covers every route in the router at once, including
    # any added later — which is what stops this regressing the next time
    # somebody adds an endpoint in a hurry.
    #
    # The role set here is the **widest** any route in the router needs; the
    # narrower writes carry their own `require_role` as well, and both run.
    # `require_role` refuses an auditor on every non-GET regardless, so the
    # auditor appearing in these lists grants read access only.
    clinical = ("doctor", "unit_head", "lab_tech", "admin", "auditor")

    app.include_router(
        encounters.router,
        prefix="/api",
        dependencies=[Depends(require_role(*clinical))],
    )
    app.include_router(
        lab_flags.router, prefix="/api", dependencies=[Depends(require_role(*clinical))]
    )
    app.include_router(
        orders.router, prefix="/api", dependencies=[Depends(require_role(*clinical))]
    )
    app.include_router(
        cases.router, prefix="/api", dependencies=[Depends(require_role(*clinical))]
    )
    app.include_router(
        patients.router, prefix="/api", dependencies=[Depends(require_role(*clinical))]
    )
    # The staff directory. Any signed-in user may read it -- assigning a
    # colleague requires being able to find them.
    app.include_router(
        users.router, prefix="/api", dependencies=[Depends(require_role())]
    )
    # Roster, absences and the Phase 4 metrics.
    app.include_router(
        ownership.router, prefix="/api", dependencies=[Depends(require_role(*clinical))]
    )

    # ── Phase 5 ───────────────────────────────────────────────────
    # These routers carry `require_role` on each route already.
    app.include_router(worklist.router, prefix="/api")
    app.include_router(audit.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    app.include_router(reports.router, prefix="/api")
    # Phase 6. Every route declares its own roles, the same way worklist,
    # audit and admin do -- the review queue, the page images and the retry
    # button each answer to a different set of people.
    app.include_router(documents.router, prefix="/api")
    return app


app = create_app()
