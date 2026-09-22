"""Exercise serialization and cancellation against owned PostgreSQL connections."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import monotonic

import pytest
from a13n_service.app import build_app
from a13n_service.distribution import OSS
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.migrations import runner
from a13n_service.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool


def test_competing_auto_migrations_start_on_empty_database(empty_database):
    start = Barrier(2)

    def launch(role):
        start.wait(timeout=10)
        with TestClient(build_app(role=role, settings=Settings(database=empty_database))) as client:
            return client.get("/readyz").json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(launch, ("all", "control")))
    assert all(result["status"] == "ready" for result in results)
    runner.check(empty_database, OSS)
    engine = create_engine(empty_database.url.get_secret_value(), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert tuple(connection.execute(text("SELECT version_num FROM alembic_version")).scalars()) == runner.heads(
                OSS
            )
            assert connection.scalar(text("SELECT count(*) FROM organizations")) == 0
    finally:
        engine.dispose()


def test_migration_lock_wait_is_bounded_and_can_recover(empty_database):
    settings = empty_database.model_copy(update={"migration_advisory_lock_timeout": 1})
    engine = create_engine(settings.url.get_secret_value(), poolclass=NullPool)
    try:
        with engine.connect() as holder:
            holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": runner.MIGRATION_LOCK_KEY})
            holder.commit()
            started = monotonic()
            with pytest.raises(DBAPIError, match="statement timeout"):
                runner.upgrade(settings, OSS)
            assert 0.8 <= monotonic() - started < 5
            assert holder.scalar(text("SELECT to_regclass('public.alembic_version')")) is None
        # Closing the holder releases its session lock; a failed contender leaves no lock behind.
        runner.upgrade(settings, OSS)
        runner.check(settings, OSS)
    finally:
        engine.dispose()


def test_runner_failure_releases_connection_and_lock(empty_database, monkeypatch):
    failed_pid = None

    def fail(config, target):
        nonlocal failed_pid
        failed_pid = config.attributes["connection"].scalar(text("SELECT pg_backend_pid()"))
        raise RuntimeError("injected migration failure")

    with monkeypatch.context() as patch:
        patch.setattr(runner.command, "upgrade", fail)
        with pytest.raises(RuntimeError, match="injected migration failure"):
            runner.upgrade(empty_database, OSS)
    engine = create_engine(empty_database.url.get_secret_value(), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE pid = :pid"), {"pid": failed_pid})
                == 0
            )
            assert connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": runner.MIGRATION_LOCK_KEY})
        runner.upgrade(empty_database, OSS)
        runner.check(empty_database, OSS)
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_cancelled_transaction_rolls_back_and_releases_pool(database):
    storage = Storage(database.url.get_secret_value(), pool_size=1)
    entered = asyncio.Event()
    backend_pid = None
    observer = Storage(database.url.get_secret_value(), pool_size=1)

    async def write_then_wait():
        nonlocal backend_pid
        async with transaction(storage) as session:
            backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.execute(
                text("INSERT INTO organizations (id, key, name) VALUES ('org_cancel', 'cancel', 'Cancel')")
            )
            entered.set()
            await session.execute(text("SELECT pg_sleep(30)"))

    task = asyncio.create_task(write_then_wait())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        # Observe the actual in-flight server query before cancellation, not just task scheduling.
        async with asyncio.timeout(5):
            while True:
                async with short_session(observer) as session:
                    query = await session.scalar(
                        text("SELECT query FROM pg_stat_activity WHERE pid = :pid AND state = 'active'"),
                        {"pid": backend_pid},
                    )
                if query == "SELECT pg_sleep(30)":
                    break
                await asyncio.sleep(0.01)
        task.cancel()
        started = monotonic()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=6)
        assert monotonic() - started < 6
        assert storage.engine.pool.checkedout() == 0
        async with asyncio.timeout(2), short_session(storage) as session:
            assert await session.scalar(text("SELECT count(*) FROM organizations WHERE id = 'org_cancel'")) == 0
            assert await session.scalar(text("SELECT 1")) == 1
    finally:
        task.cancel()
        await storage.close()
        await observer.close()
