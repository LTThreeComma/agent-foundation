"""FastAPI application factory and process lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import fail_after
from fastapi import FastAPI, HTTPException, Request, status
from sqlalchemy import text

from converge_foundation_service.db import SessionFactory, create_database_engine, create_session_factory, short_session
from converge_foundation_service.settings import ServiceSettings, get_settings

logger = logging.getLogger("converge_foundation_service.app")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: ServiceSettings = app.state.settings
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    logger.info(
        "service_started",
        extra={
            "event": "service_started",
            "service": settings.service_name,
            "role": settings.role.value,
            "build_version": settings.build_version,
        },
    )
    try:
        yield
    finally:
        await engine.dispose()
        logger.info(
            "service_stopped",
            extra={"event": "service_stopped", "service": settings.service_name, "role": settings.role.value},
        )


def create_app(settings: ServiceSettings | None = None) -> FastAPI:
    """Create an application without opening external resources."""
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title="Agent Foundation Service",
        version=resolved_settings.build_version,
        lifespan=_lifespan,
    )
    app.state.settings = resolved_settings

    @app.get("/healthz", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": resolved_settings.role.value}

    @app.get("/readyz", tags=["system"])
    async def readiness(request: Request) -> dict[str, str]:
        factory: SessionFactory = request.app.state.db_session_factory
        try:
            with fail_after(resolved_settings.database_readiness_timeout_seconds):
                async with short_session(factory) as session:
                    await session.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning(
                "database_readiness_failed",
                extra={"event": "database_readiness_failed", "role": resolved_settings.role.value},
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="database unavailable",
            ) from exc
        return {"status": "ready", "role": resolved_settings.role.value}

    return app


app = create_app()
