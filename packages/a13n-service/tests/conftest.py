"""Shared test infrastructure: one PostgreSQL and one Redis per session, a fresh database per test.

The schema is built once from metadata plus table rules into a template database; each test clones it with
`CREATE DATABASE ... TEMPLATE`, which takes milliseconds. `test_migrations.py` proves the generated
revisions build the same schema, so tests using the template exercise the migrated schema too.
"""

import base64
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
from a13n_service.app import build_app, open_runtime
from a13n_service.distribution import OSS
from a13n_service.migrations.runner import heads
from a13n_service.runs.runtime import Runtime
from a13n_service.settings import Database, Settings
from a13n_service.tenancy.bootstrap import BootstrapInput, Bootstrapped, bootstrap
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

TEMPLATE = "a13n_template"
EMAIL = "admin@example.com"
PASSWORD = "test-password-1234"


def database_url(base: str, name: str) -> str:
    return make_url(base).set(database=name).render_as_string(hide_password=False)


def _admin(base: str) -> Engine:
    return create_engine(database_url(base, "postgres"), isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def template_database(postgres_url: str) -> str:
    admin = _admin(postgres_url)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{TEMPLATE}"'))
    admin.dispose()
    engine = create_engine(database_url(postgres_url, TEMPLATE))
    with engine.begin() as connection:
        OSS.metadata().create_all(connection)
        for statement in OSS.rules():
            connection.execute(text(statement))
        # The application checks the recorded head before serving; the template is at head by construction.
        connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)"))
        for head in heads(OSS):
            connection.execute(text("INSERT INTO alembic_version VALUES (:head)"), {"head": head})
    engine.dispose()
    return TEMPLATE


def _scratch_database(postgres_url: str, template: str | None) -> Iterator[Database]:
    name = f"t_{uuid4().hex}"
    admin = _admin(postgres_url)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else "")))
    try:
        yield Database(url=SecretStr(database_url(postgres_url, name)), auto_migrate=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def empty_database(postgres_url: str) -> Iterator[Database]:
    """A database without schema, for migration tests."""
    yield from _scratch_database(postgres_url, None)


@pytest.fixture
def database(postgres_url: str, template_database: str) -> Iterator[Database]:
    yield from _scratch_database(postgres_url, template_database)


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    # One Redis for the session; fixtures that use it flush it first.
    with RedisContainer("redis:8-alpine") as redis:
        yield f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(database: Database, redis_url: str, tmp_path: Path) -> Settings:
    return Settings(
        database=database,
        redis={"url": redis_url},
        objects={"root": tmp_path / "objects"},
        encryption={"active_key_id": "test", "keys": {"test": base64.b64encode(b"k" * 32).decode()}},
        providers={"private_cidrs": ["127.0.0.0/8"], "require_https": False},
    )


@pytest.fixture
async def runtime(settings: Settings) -> AsyncIterator[Runtime]:
    async with AsyncExitStack() as stack:
        runtime = await open_runtime(stack, OSS, settings)
        await runtime.redis.flushdb()
        yield runtime


@pytest.fixture
async def tenant(runtime: Runtime) -> Bootstrapped:
    return await bootstrap(runtime.storage, BootstrapInput(email=EMAIL, password=SecretStr(PASSWORD)))


@pytest.fixture
async def service(settings: Settings) -> AsyncIterator[SimpleNamespace]:
    """The control-role application with a bootstrapped tenant and a logged-in administrator."""
    app = build_app(OSS, role="control", settings=settings)
    async with app.router.lifespan_context(app):
        await app.state.redis.flushdb()
        tenant = await bootstrap(app.state.storage, BootstrapInput(email=EMAIL, password=SecretStr(PASSWORD)))
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://service.test"
        ) as client:
            response = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
            assert response.status_code == 200, response.text
            client.headers["x-csrf-token"] = response.json()["csrf_token"]
            yield SimpleNamespace(
                app=app,
                runtime=app.state.runtime,
                client=client,
                tenant=tenant,
                workspace=f"/api/v1/workspaces/{tenant.workspace_id}",
                organization=f"/api/v1/organizations/{tenant.organization_id}",
            )
