"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from memory_engine.exceptions import (
    ConfigError,
    IdempotencyConflict,
    SignatureInvalid,
)
from memory_engine.http.routes.identity import router as identity_router
from memory_engine.http.routes.ingest import router as ingest_router
from memory_engine.http.routes.mcp import router as mcp_router
from memory_engine.http.routes.personas import router as personas_router
from memory_engine.http.routes.recall import router as recall_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="memory_engine",
        version="0.1.0",
        docs_url="/docs",
    )
    app.include_router(recall_router, prefix="/v1")
    app.include_router(personas_router, prefix="/v1")
    app.include_router(mcp_router, prefix="/v1")
    app.include_router(identity_router, prefix="/v1")
    app.include_router(ingest_router, prefix="/v1")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Global exception handler: let domain exceptions bubble here.
    # Per Phase 6.5 scope: no per-endpoint try/except.
    @app.exception_handler(SignatureInvalid)
    async def _sig_invalid(_req: Request, exc: SignatureInvalid) -> JSONResponse:
        return JSONResponse(
            status_code=401, content={"error": "signature_invalid", "detail": str(exc)}
        )

    @app.exception_handler(IdempotencyConflict)
    async def _idem_conflict(_req: Request, exc: IdempotencyConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"error": "idempotency_conflict", "detail": str(exc)}
        )

    @app.exception_handler(ConfigError)
    async def _config_error(_req: Request, exc: ConfigError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"error": "config_error", "detail": str(exc)}
        )

    @app.exception_handler(HTTPException)
    async def _http_exc(_req: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"error": "http_error", "detail": exc.detail}
        )

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error in route", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": str(exc)},
        )

    return app


# Module-level instance for `uvicorn memory_engine.http.app:app` / `memory-engine serve`.
app = create_app()
