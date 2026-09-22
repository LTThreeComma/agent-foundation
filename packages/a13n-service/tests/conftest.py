"""Each test owns disposable PostgreSQL, never application configuration."""

import pytest
from a13n_service.distribution import OSS
from a13n_service.migrations.runner import upgrade
from a13n_service.settings import Database
from pydantic import SecretStr
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@pytest.fixture
def empty_database():
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as postgres:
        settings = Database(url=SecretStr(postgres.get_connection_url()))
        yield settings


@pytest.fixture
def database(empty_database):
    upgrade(empty_database, OSS)
    return empty_database


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def redis_url():
    with RedisContainer("redis:8-alpine") as redis:
        yield f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"


@pytest.fixture
def model_url(request):
    if getattr(request, "param", None) == "live":
        from dev.fixtures.model import model_process

        with model_process(port=0) as url:
            yield url
    else:
        yield "http://127.0.0.1:7007/v1"


@pytest.fixture
async def public_service(database, redis_url, tmp_path, model_url, agent_configurator, request):
    """A configured public agent, using the same resource APIs as an external client."""
    from types import SimpleNamespace

    import httpx
    from a13n_service.app import build_app
    from a13n_service.settings import Settings
    from a13n_service.tenancy.bootstrap import BootstrapInput, bootstrap

    app = build_app(
        role="control",
        settings=Settings(
            database=database,
            redis={"url": redis_url},
            control={"inbox_count": 3, "scan_seconds": getattr(request, "param", {}).get("scan_seconds", 60)},
            objects={"root": tmp_path / "objects"},
            providers={"private_cidrs": ["127.0.0.0/8"], "http_origins": [model_url.removesuffix("/v1")]},
        ),
    )
    async with app.router.lifespan_context(app):
        initialized = await bootstrap(
            app.state.storage,
            BootstrapInput(
                email="user@example.com",
                password=SecretStr("test-password"),
            ),
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://service.test") as client:
            response = await client.post(
                "/api/v1/auth/login", json={"email": "user@example.com", "password": "test-password"}
            )
            assert response.status_code == 200, response.text
            client.headers["x-csrf-token"] = response.json()["csrf_token"]
            workspace_path = f"/api/v1/workspaces/{initialized.workspace_id}"
            agent_id = await agent_configurator(client, initialized)
            yield SimpleNamespace(
                app=app,
                client=client,
                initialized=initialized,
                workspace_path=workspace_path,
                agent_id=agent_id,
            )


@pytest.fixture
def agent_configurator(model_url):
    async def configure(client, initialized):
        base = f"/api/v1/organizations/{initialized.organization_id}"
        response = await client.post(
            base + "/model-providers",
            json={
                "workspace_id": initialized.workspace_id,
                "type": "openai",
                "name": "Local",
                "config": {"auth_mode": "none", "base_url": model_url},
            },
        )
        assert response.status_code == 201, response.text
        response = await client.post(
            base + "/models",
            json={
                "workspace_id": initialized.workspace_id,
                "provider_id": response.json()["id"],
                "key": "scripted",
                "name": "Scripted",
                "config": {
                    "model_name": "scripted",
                    "model_api": "openai.chat_completions",
                    "context_window": 8192,
                },
            },
        )
        assert response.status_code == 201, response.text
        workspace_path = f"/api/v1/workspaces/{initialized.workspace_id}"
        response = await client.post(
            workspace_path + "/agents",
            json={
                "key": "assistant",
                "name": "Assistant",
                "config": {"model_id": response.json()["id"]},
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return configure


@pytest.fixture
def mcp_url(tmp_path):
    from dev.fixtures.process import fixture_process

    with fixture_process("dev.fixtures.mcp", arguments=("--database", str(tmp_path / "mcp.sqlite"))) as url:
        yield url
