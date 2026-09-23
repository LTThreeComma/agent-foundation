"""Role assembly and bounded dependency probes for the Service foundation."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from importlib.metadata import version

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.connector.contracts import ConnectorProviderError
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from anyio.to_thread import run_sync
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from redis.asyncio import Redis
from sqlalchemy import text

from a13n_service.distribution import OSS, Distribution
from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import service_error_response, validation_error_response
from a13n_service.infra.ingress import BodyLimit
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.migrations.runner import heads, upgrade
from a13n_service.resources.connections.catalog_routes import provider_error_response
from a13n_service.resources.connections.oauth_maintenance import maintain_authorizations
from a13n_service.runs.maintenance import maintain
from a13n_service.runs.worker import Worker
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
    key_ring = KeyRing(active_key_id=config.encryption.active_key_id, keys=config.encryption.keys)
    model_catalog = ProviderCatalog(distribution.model_providers)
    tool_catalog = ProviderCatalog(distribution.tool_sources)
    endpoint_policy = EndpointPolicy.from_operator_allowlist(**config.providers.model_dump())
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
        redis = Redis.from_url(
            config.redis.url.get_secret_value(),
            socket_timeout=config.redis.timeout,
            socket_connect_timeout=config.redis.timeout,
            decode_responses=True,
        )
        app.state.objects = LocalObjects(config.objects.root, max_bytes=config.objects.max_bytes)
        app.state.settings = config
        app.state.admission = distribution.admission
        app.state.key_ring = key_ring
        app.state.model_catalog = model_catalog
        app.state.tool_catalog = tool_catalog
        app.state.endpoint_policy = endpoint_policy
        app.state.redis = redis
        app.state.authentication = config.auth
        app.state.storage = storage
        app.state.started = False
        background: list[asyncio.Task[None]] = []
        app.state.background = background
        try:
            async with asyncio.timeout(config.server.readiness_timeout):
                await check_schema(storage, expected)
            if role in {"all", "control"}:
                background.append(
                    asyncio.create_task(maintain_authorizations(storage, config.oauth), name="oauth-maintenance")
                )
                background.append(
                    asyncio.create_task(
                        maintain(storage, redis, config=config, policy=distribution.admission), name="run-maintenance"
                    )
                )
            if role in {"all", "worker"}:
                worker = Worker(
                    storage,
                    app.state.objects,
                    redis,
                    config=config,
                    catalog=model_catalog,
                    tool_catalog=tool_catalog,
                    keys=key_ring,
                    endpoint_policy=endpoint_policy,
                    admission=distribution.admission,
                )
                background.append(asyncio.create_task(worker.run(), name="run-worker"))
            app.state.started = True
            yield
        finally:
            app.state.started = False
            for task in background:
                task.cancel()
            try:
                async with asyncio.timeout(config.server.shutdown_timeout):
                    await asyncio.gather(*background, return_exceptions=True)
            finally:
                try:
                    await redis.aclose()
                finally:
                    await storage.close()

    app = FastAPI(
        title="a13n Service",
        version=version("a13n-service"),
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json" if role != "worker" else None,
        docs_url="/api/v1/docs" if role != "worker" else None,
        swagger_ui_oauth2_redirect_url="/api/v1/docs/oauth2-redirect" if role != "worker" else None,
        redoc_url=None,
    )

    app.add_middleware(BodyLimit, max_bytes=config.server.request_bytes, timeout=config.server.request_timeout)
    app.add_exception_handler(ServiceError, service_error_response)
    app.add_exception_handler(ConnectorProviderError, provider_error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": role}

    @app.get("/readyz")
    async def ready() -> JSONResponse:
        dependency = "runtime"
        try:
            if not getattr(app.state, "started", False) or any(task.done() for task in app.state.background):
                raise RuntimeError("not started")
            async with asyncio.timeout(config.server.readiness_timeout):
                dependency = "database"
                await check_schema(app.state.storage, expected)
                dependency = "redis"
                await app.state.redis.ping()
        except Exception:
            return JSONResponse({"status": "unavailable", "dependency": dependency}, status_code=503)
        return JSONResponse({"status": "ready", "role": role, "capabilities": ["bootstrap", "runs"]})

    seen = {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/api/v1/openapi.json"),
        ("GET", "/api/v1/docs"),
        ("GET", "/api/v1/docs/oauth2-redirect"),
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
