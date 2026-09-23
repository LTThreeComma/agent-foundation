"""Assembly: one application per process role, built from one distribution.

`all` serves the API, runs control sweeps and executes runs; `control` omits execution; `worker` executes
runs only and never migrates.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from functools import partial
from importlib.metadata import version

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
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.infra.objects.s3 import open_s3
from a13n_service.infra.outbox import deliver, purge_settled
from a13n_service.infra.sweeps import Sweep, run_sweeps
from a13n_service.migrations.runner import heads, upgrade
from a13n_service.providers.registry import Registry
from a13n_service.runs.runtime import Runtime
from a13n_service.settings import Objects, ProcessRole, Settings, load_settings

EXEMPT_ROUTES = frozenset(
    {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/api/v1/openapi.json"),
        ("GET", "/api/v1/docs"),
        ("GET", "/api/v1/docs/oauth2-redirect"),
    }
)


async def check_schema(storage: Storage, expected: tuple[str, ...]) -> None:
    async with short_session(storage) as session:
        actual = tuple(
            (await session.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num"))).scalars()
        )
    if actual != tuple(sorted(expected)) or not expected:
        raise RuntimeError("Database schema is incompatible; run a13n-service migrate")


async def open_objects(stack: AsyncExitStack, config: Objects) -> ObjectStore:
    if config.backend == "local":
        return LocalObjects(config.root, max_bytes=config.max_bytes, timeout=config.timeout)
    assert config.bucket is not None
    return await stack.enter_async_context(
        open_s3(
            bucket=config.bucket,
            prefix=config.prefix,
            region=config.region,
            endpoint_url=config.endpoint_url,
            access_key_id=config.access_key_id.get_secret_value() if config.access_key_id else None,
            secret_access_key=config.secret_access_key.get_secret_value() if config.secret_access_key else None,
            max_bytes=config.max_bytes,
            timeout=config.timeout,
        )
    )


def control_sweeps(distribution: Distribution, runtime: Runtime) -> list[Sweep]:
    control = runtime.settings.control
    handlers = {kind: factory(runtime) for kind, factory in distribution.deliveries.items()}
    owner = f"control-{id(runtime):x}"
    return [
        *(factory(runtime) for factory in distribution.sweeps),
        Sweep(
            name="deliver_outbox",
            every=control.scan_seconds,
            run=partial(deliver, runtime.storage, handlers, owner=owner, limit=control.outbox_batch, lease_seconds=60),
            timeout=120,
        ),
        Sweep(
            name="purge_outbox",
            every=3600,
            run=partial(
                purge_settled,
                runtime.storage,
                older_than=timedelta(days=control.outbox_retention_days),
                limit=control.sweep_batch,
            ),
            timeout=60,
        ),
    ]


async def open_runtime(stack: AsyncExitStack, distribution: Distribution, config: Settings) -> Runtime:
    database = config.database
    storage = Storage(
        database.url.get_secret_value(),
        pool_size=database.pool_size,
        connect_timeout=database.connect_timeout,
        statement_timeout=database.statement_timeout,
    )
    stack.push_async_callback(storage.close)
    redis = Redis.from_url(
        config.redis.url.get_secret_value(),
        socket_timeout=config.redis.timeout,
        socket_connect_timeout=config.redis.timeout,
        decode_responses=True,
    )
    stack.push_async_callback(redis.aclose)
    return Runtime(
        storage=storage,
        objects=await open_objects(stack, config.objects),
        redis=redis,
        keys=KeyRing(active_key_id=config.encryption.active_key_id, keys=config.encryption.keys),
        settings=config,
        registry=Registry.of(distribution.providers),
        admission=distribution.admission,
    )


def build_app(
    distribution: Distribution = OSS, *, role: ProcessRole = "all", settings: Settings | None = None
) -> FastAPI:
    config = settings or load_settings(extensions=distribution.settings)
    if role not in {"all", "control", "worker"}:
        raise ValueError(f"Unknown process role: {role}")
    Registry.of(distribution.providers)
    expected = heads(distribution)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if role != "worker" and config.database.auto_migrate:
            await run_sync(partial(upgrade, config.database, distribution))
        async with AsyncExitStack() as stack:
            runtime = await open_runtime(stack, distribution, config)
            app.state.runtime = runtime
            # Resource services take only what they use; these aliases keep their routes short.
            app.state.storage, app.state.objects, app.state.redis = runtime.storage, runtime.objects, runtime.redis
            app.state.settings, app.state.key_ring = config, runtime.keys
            app.state.started = False
            background: list[asyncio.Task[None]] = []
            app.state.background = background
            try:
                async with asyncio.timeout(config.server.readiness_timeout):
                    await check_schema(runtime.storage, expected)
                if role in {"all", "control"}:
                    sweeps = control_sweeps(distribution, runtime)
                    background.append(asyncio.create_task(run_sweeps(sweeps), name="control-sweeps"))
                app.state.started = True
                yield
            finally:
                app.state.started = False
                for task in background:
                    task.cancel()
                async with asyncio.timeout(config.server.shutdown_timeout):
                    await asyncio.gather(*background, return_exceptions=True)

    serves_api = role != "worker"
    app = FastAPI(
        title="a13n Service",
        version=version("a13n-service"),
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json" if serves_api else None,
        docs_url="/api/v1/docs" if serves_api else None,
        swagger_ui_oauth2_redirect_url="/api/v1/docs/oauth2-redirect" if serves_api else None,
        redoc_url=None,
    )
    app.add_middleware(BodyLimit, max_bytes=config.server.request_bytes, timeout=config.server.request_timeout)
    app.add_exception_handler(ServiceError, service_error_response)
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
        return JSONResponse({"status": "ready", "role": role})

    seen = set(EXEMPT_ROUTES)
    for router in distribution.routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                raise ValueError("Distribution routers must declare direct HTTP API routes")
            for method in route.methods or ():
                if (method, route.path) in seen:
                    raise ValueError(f"Duplicate route: {method} {route.path}")
                seen.add((method, route.path))
        if serves_api:
            app.include_router(router)
    return app
