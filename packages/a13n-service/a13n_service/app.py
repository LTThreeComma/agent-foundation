"""Role assembly and bounded dependency probes for the Service foundation."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from importlib.metadata import version

from anyio.to_thread import run_sync
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import text

from a13n_service.distribution import OSS, Distribution
from a13n_service.infra.db import Storage, short_session
from a13n_service.migrations.runner import heads, upgrade
from a13n_service.settings import ProcessRole, Settings


async def check_schema(storage: Storage, expected: tuple[str, ...]) -> None:
    async with short_session(storage) as session:
        actual = tuple(
            (await session.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num"))).scalars()
        )
    if actual != tuple(sorted(expected)) or not expected:
        raise RuntimeError("Database schema is incompatible; run a13n-service migrate")


def build_app(
    distribution: Distribution = OSS, *, role: ProcessRole = "all", settings: Settings | None = None
) -> FastAPI:
    config = settings or Settings()
    if role not in {"all", "control", "worker"}:
        raise ValueError(f"Unknown process role: {role}")
    distribution.metadata()
    expected = heads(distribution)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if role != "worker" and config.database.auto_migrate:
            await run_sync(partial(upgrade, config.database, distribution))
        database = config.database
        storage = Storage(
            database.url.get_secret_value(),
            pool_size=database.pool_size,
            connect_timeout=database.connect_timeout,
            statement_timeout=database.statement_timeout,
        )
        app.state.storage = storage
        app.state.started = False
        try:
            async with asyncio.timeout(config.server.readiness_timeout):
                await check_schema(storage, expected)
            app.state.started = True
            yield
        finally:
            app.state.started = False
            await storage.close()

    app = FastAPI(
        title="a13n Service",
        version=version("a13n-service"),
        lifespan=lifespan,
        openapi_url="/api/openapi.json" if role != "worker" else None,
        docs_url="/api/docs" if role != "worker" else None,
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect" if role != "worker" else None,
        redoc_url=None,
    )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": role}

    @app.get("/readyz")
    async def ready() -> JSONResponse:
        try:
            if not getattr(app.state, "started", False):
                raise RuntimeError("not started")
            async with asyncio.timeout(config.server.readiness_timeout):
                await check_schema(app.state.storage, expected)
        except Exception:
            return JSONResponse({"status": "unavailable", "dependency": "database"}, status_code=503)
        return JSONResponse({"status": "ready", "role": role, "capabilities": ["bootstrap"]})

    seen = {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/api/openapi.json"),
        ("GET", "/api/docs"),
        ("GET", "/api/docs/oauth2-redirect"),
    }
    for router in distribution.routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                raise ValueError("Distribution routers must declare direct HTTP API routes")
            for method in route.methods or ():
                identity = (method, route.path)
                if identity in seen:
                    raise ValueError(f"Duplicate route: {method} {route.path}")
                seen.add(identity)
        if role != "worker":
            app.include_router(router)
    return app
