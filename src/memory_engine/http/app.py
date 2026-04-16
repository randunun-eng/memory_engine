"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from memory_engine.http.routes.recall import router as recall_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="memory_engine",
        version="0.1.0",
        docs_url="/docs",
    )
    app.include_router(recall_router, prefix="/v1")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
