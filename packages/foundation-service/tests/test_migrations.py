from pathlib import Path
from time import monotonic

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from converge_foundation_service import cli
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool


def test_migration_layout_loads_with_at_most_one_head() -> None:
    scripts = ScriptDirectory.from_config(cli._alembic_config())

    assert len(scripts.get_heads()) <= 1
    assert Path(scripts.dir).name == "migrations"


def test_migration_history_runs_on_postgresql(migrated_pg_url: str) -> None:
    engine = create_engine(migrated_pg_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_database()"))
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended('converge-foundation-service:public:alembic', 0))")
            )
            assert acquired is True
            connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended('converge-foundation-service:public:alembic', 0))")
            )
    finally:
        engine.dispose()


def test_migration_advisory_wait_has_independent_timeout(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(pg_url, poolclass=NullPool)
    with engine.connect() as lock_holder:
        lock_holder.execute(
            text("SELECT pg_advisory_lock(hashtextextended('converge-foundation-service:public:alembic', 0))")
        )
        lock_holder.commit()
        monkeypatch.setenv("FOUNDATION_DATABASE_URL", pg_url)
        monkeypatch.setenv("FOUNDATION_MIGRATION_ADVISORY_LOCK_TIMEOUT_SECONDS", "0.25")
        monkeypatch.setenv("FOUNDATION_MIGRATION_LOCK_TIMEOUT_SECONDS", "0.01")

        started = monotonic()
        with pytest.raises(OperationalError):
            command.upgrade(cli._alembic_config(), "head")
        elapsed = monotonic() - started

        assert 0.15 <= elapsed < 2.0
        assert (
            lock_holder.scalar(
                text("SELECT pg_advisory_unlock(hashtextextended('converge-foundation-service:public:alembic', 0))")
            )
            is True
        )
        lock_holder.commit()
    engine.dispose()
