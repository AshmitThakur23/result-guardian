"""Result Guardian — NODE A API entrypoint. Phase 0.5."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.session import dispose_engine
from app.errors import register_exception_handlers
from app.logging import RequestIdMiddleware, configure_logging
from app.routers import encounters, health, users
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
    app.include_router(health.router, prefix="/api")
    app.include_router(encounters.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    return app


app = create_app()
