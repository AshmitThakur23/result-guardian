"""RFC 7807 problem+json error handling. Phase 0.5.

Never leak a stack trace in prod: an exception message can carry a patient
identifier, and the response may be logged by an intermediary.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings

log = structlog.get_logger(__name__)

CONTENT_TYPE = "application/problem+json"


def _problem(
    status: int, title: str, detail: str | None = None, **extra: Any
) -> JSONResponse:
    body: dict[str, Any] = {"type": "about:blank", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(status_code=status, content=body, media_type=CONTENT_TYPE)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        return _problem(exc.status_code, title=str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _problem(
            422,
            title="Validation failed",
            detail="The request body or parameters did not validate.",
            errors=[
                {"loc": list(e.get("loc", ())), "msg": e.get("msg", "")}
                for e in exc.errors()
            ],
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", path=request.url.path)
        if get_settings().is_prod:
            return _problem(500, title="Internal server error")
        return _problem(
            500,
            title="Internal server error",
            detail=f"{type(exc).__name__}: {exc}",
        )
