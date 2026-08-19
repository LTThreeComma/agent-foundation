"""Fixture-owned integration infrastructure for foundation-service tests."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from alembic import command

# Tests never inherit application infrastructure from a developer's shell or .env.
for _variable in [key for key in os.environ if key.startswith("FOUNDATION_")]:
    os.environ.pop(_variable, None)

from converge_foundation_service.settings import ServiceSettings as _ServiceSettings  # noqa: E402

_ServiceSettings.model_config = {**_ServiceSettings.model_config, "env_file": None}


@pytest.fixture(scope="session")
def pg_url() -> Generator[str]:
    """Yield a disposable PostgreSQL 17 database owned by Testcontainers."""
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        yield f"postgresql+psycopg://{container.username}:{container.password}@{host}:{port}/{container.dbname}"


@pytest.fixture(scope="session")
def redis_url() -> Generator[str]:
    """Yield a disposable Redis 7 instance owned by Testcontainers."""
    from testcontainers.community.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def migrated_pg_url(pg_url: str) -> Generator[str]:
    """Apply and verify the complete migration history on disposable PostgreSQL."""
    from converge_foundation_service import cli
    from converge_foundation_service.settings import get_settings

    os.environ["FOUNDATION_DATABASE_URL"] = pg_url
    get_settings.cache_clear()
    try:
        command.upgrade(cli._alembic_config(), "head")
        command.current(cli._alembic_config(), check_heads=True)
        yield pg_url
    finally:
        get_settings.cache_clear()
        os.environ.pop("FOUNDATION_DATABASE_URL", None)
