import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from importlib.metadata import version

import pytest
from a13n_service.app import build_app
from a13n_service.distribution import OSS
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.migrations.runner import check, heads, migration_connection, upgrade
from a13n_service.settings import Settings, load_settings
from a13n_service.tenancy.bootstrap import BootstrapInput, bootstrap
from alembic import command
from argon2 import PasswordHasher
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def test_configuration_precedence_and_unknown_keys(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("A13N_"):
            monkeypatch.delenv(key)
    config = tmp_path / "settings.toml"
    config.write_text("[server]\nport=9123\n")
    monkeypatch.setenv("A13N_SETTINGS_FILE", str(config))
    monkeypatch.setenv("A13N_SERVER__PORT", "9124")
    assert load_settings().server.port == 9124
    monkeypatch.setenv("A13N_SERVER__PORRT", "9125")
    with pytest.raises(ValidationError):
        load_settings()
    monkeypatch.delenv("A13N_SERVER__PORRT")
    config.write_text("[objects]\npath='unused'\n")
    with pytest.raises(ValidationError):
        load_settings()


def test_schema_roundtrip_and_metadata_parity(database):
    assert len(heads(OSS)) == 1
    check(database, OSS)
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "base")
    upgrade(database, OSS)
    check(database, OSS)


def test_session_activity_migration_restores_only_session_stamp(database):
    def inspect(config):
        connection = config.attributes["connection"]
        triggers = dict(
            connection.execute(
                text(
                    "SELECT c.relname, p.proname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid "
                    "WHERE NOT t.tgisinternal AND c.relname IN ('sessions', 'threads', 'runs') "
                    "AND p.proname IN ('stamp_resource', 'stamp_session')"
                )
            ).all()
        )
        indexes = set(
            connection.scalars(
                text(
                    "SELECT indexname FROM pg_indexes WHERE indexname IN "
                    "('ix_sessions_workspace_activity', 'ix_runs_workspace_session_created')"
                )
            )
        )
        return triggers, indexes

    with migration_connection(database, OSS) as config:
        before = inspect(config)
        assert before[0] == {"sessions": "stamp_session", "threads": "stamp_resource", "runs": "stamp_resource"}
        assert len(before[1]) == 2
        # Inspection opens a transaction; finish it before Alembic owns the DDL transaction.
        config.attributes["connection"].commit()
        command.downgrade(config, "611cf188440e")
        triggers, indexes = inspect(config)
        assert triggers == dict.fromkeys(("sessions", "threads", "runs"), "stamp_resource")
        assert not indexes
        assert config.attributes["connection"].scalar(text("SELECT to_regproc('stamp_session')")) is None
        config.attributes["connection"].commit()
        command.upgrade(config, "head")
        assert inspect(config) == before
    check(database, OSS)


@pytest.mark.anyio
async def test_bootstrap_race_password_audit_and_database_guards(database):
    storage = Storage(database.url.get_secret_value())
    request = BootstrapInput(email="admin@example.com", password=SecretStr("a sufficiently long password"))
    try:
        results = await asyncio.gather(bootstrap(storage, request), bootstrap(storage, request), return_exceptions=True)
        assert sum(isinstance(result, ValueError) for result in results) == 1
        async with short_session(storage) as session:
            for table in ("organizations", "workspaces", "principals", "passwords", "grants", "audit_events"):
                assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == 1
            encoded = await session.scalar(text("SELECT hash FROM passwords"))
            assert PasswordHasher().verify(encoded, request.password.get_secret_value())
            assert await session.scalar(text("SELECT role FROM grants WHERE workspace_id IS NULL")) == "admin"
        async with transaction(storage) as session:
            await session.execute(text("UPDATE organizations SET name = 'Renamed'"))
        async with short_session(storage) as session:
            assert await session.scalar(text("SELECT version FROM organizations")) == 2
        for statement in ("DELETE FROM audit_events", "UPDATE audit_events SET outcome = 'failed'"):
            with pytest.raises(DBAPIError, match="immutable"):
                async with transaction(storage) as session:
                    await session.execute(text(statement))
        async with short_session(storage) as session:
            assert await session.scalar(text("SELECT count(*) FROM audit_events")) == 1
        assert storage.engine.pool.checkedout() == 0
    finally:
        await storage.close()


@pytest.mark.parametrize("role", ["all", "control", "worker"])
def test_role_startup_and_probes(database, redis_url, role):
    settings = Settings(database=database, redis={"url": redis_url})
    router = APIRouter()
    router.add_api_route("/api/v1/example", lambda: {"ok": True})
    with TestClient(build_app(replace(OSS, routers=(router,)), role=role, settings=settings)) as client:
        assert client.get("/healthz").json() == {"status": "ok", "role": role}
        assert client.get("/readyz").status_code == 200
        assert client.get("/api/v1/runs").status_code == 404
        assert client.get("/docs/oauth2-redirect").status_code == 404
        for path in ("/api/v1/openapi.json", "/api/v1/docs", "/api/v1/docs/oauth2-redirect", "/api/v1/example"):
            assert client.get(path).status_code == (404 if role == "worker" else 200)
        if role != "worker":
            assert client.get("/api/v1/openapi.json").json()["info"]["version"] == version("a13n-service")


def test_worker_never_migrates_incompatible_schema(database, redis_url):
    with migration_connection(database, OSS) as config:
        command.downgrade(config, "base")
    with pytest.raises(RuntimeError, match="incompatible"):
        with TestClient(build_app(role="worker", settings=Settings(database=database))):
            pytest.fail("worker started against an empty schema")
    # Control is the migration owner and repairs the same disposable database.
    with TestClient(
        build_app(role="control", settings=Settings(database=database, redis={"url": redis_url}))
    ) as client:
        assert client.get("/readyz").status_code == 200


def test_duplicate_composition_refuses_startup():
    with pytest.raises(ValueError, match="Duplicate table"):
        build_app(replace(OSS, tables=OSS.tables + OSS.tables))
    router = APIRouter()
    router.add_api_route("/healthz", lambda: {}, methods=["GET"])
    with pytest.raises(ValueError, match="Duplicate route"):
        build_app(replace(OSS, routers=(router,)))


def test_installed_cli_without_legacy_and_bootstrap(database):
    assert importlib.util.find_spec("a13n_service_legacy") is None
    environment = {k: v for k, v in os.environ.items() if not k.startswith("A13N_")}
    environment["A13N_DATABASE__URL"] = database.url.get_secret_value()
    command_line = [str(__import__("pathlib").Path(sys.executable).parent / "a13n-service")]
    result = subprocess.run([*command_line, "--help"], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "bootstrap" in result.stdout and "migrate" in result.stdout
    result = subprocess.run([*command_line, "--version"], env=environment, capture_output=True, text=True)
    assert result.returncode == 0 and version("a13n-service") in result.stdout
    arguments = [*command_line, "bootstrap", "--email", "admin@example.com", "--password", "long bootstrap secret"]
    result = subprocess.run(arguments, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["principal_id"].startswith("usr_")
    assert "long bootstrap secret" not in result.stdout + result.stderr
    assert subprocess.run(arguments, env=environment, capture_output=True).returncode != 0
