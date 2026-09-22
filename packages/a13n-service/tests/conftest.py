"""Each test owns disposable PostgreSQL, never application configuration."""

import pytest
from a13n_service.distribution import OSS
from a13n_service.migrations.runner import upgrade
from a13n_service.settings import Database
from pydantic import SecretStr
from testcontainers.postgres import PostgresContainer


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
