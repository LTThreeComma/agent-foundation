"""Alembic environment with bounded PostgreSQL migration serialization."""

from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool, text

from converge_foundation_service.db.models import Base
from converge_foundation_service.settings import ServiceSettings

config = context.config
if config.config_file_name is not None and not logging.getLogger("alembic").handlers:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")
settings = ServiceSettings()
target_metadata = Base.metadata
_MIGRATION_LOCK_NAME = "converge-foundation-service:public:alembic"


def _milliseconds(seconds: float) -> str:
    return f"{max(1, round(seconds * 1000))}ms"


def _set_timeout(connection: Connection, setting: str, value: str) -> None:
    allowed = {"lock_timeout", "statement_timeout", "idle_in_transaction_session_timeout"}
    if setting not in allowed:
        raise ValueError(f"unsupported PostgreSQL timeout: {setting}")
    connection.execute(
        text("SELECT set_config(:setting, :value, false)"),
        {"setting": setting, "value": value},
    )


def _acquire_migration_lock(connection: Connection) -> None:
    advisory_wait = _milliseconds(settings.migration_advisory_lock_timeout_seconds)
    statement_timeout = _milliseconds(settings.migration_statement_timeout_seconds)
    lock_timeout = _milliseconds(settings.migration_lock_timeout_seconds)
    idle_timeout = _milliseconds(settings.migration_idle_transaction_timeout_seconds)

    # Keep DDL lock_timeout independent from advisory-lock serialization. A
    # waiting replica may queue for the advisory lock, while DDL still fails
    # quickly if an application transaction blocks a table lock.
    _set_timeout(connection, "lock_timeout", "0")
    _set_timeout(connection, "statement_timeout", advisory_wait)
    _set_timeout(connection, "idle_in_transaction_session_timeout", idle_timeout)
    connection.execute(
        text("SELECT pg_advisory_lock(hashtextextended(:lock_name, 0))"),
        {"lock_name": _MIGRATION_LOCK_NAME},
    )
    connection.commit()

    _set_timeout(connection, "lock_timeout", lock_timeout)
    _set_timeout(connection, "statement_timeout", statement_timeout)
    _set_timeout(connection, "idle_in_transaction_session_timeout", idle_timeout)
    connection.commit()
    logger.info(
        "Acquired migration lock; advisory_wait=%s lock_timeout=%s statement_timeout=%s idle_timeout=%s",
        advisory_wait,
        lock_timeout,
        statement_timeout,
        idle_timeout,
    )


def _release_migration_lock(connection: Connection) -> None:
    if connection.in_transaction():
        connection.rollback()
    connection.execute(
        text("SELECT pg_advisory_unlock(hashtextextended(:lock_name, 0))"),
        {"lock_name": _MIGRATION_LOCK_NAME},
    )
    connection.commit()


def _configure(connection: Connection | None = None) -> None:
    context.configure(
        connection=connection,
        url=None if connection is not None else settings.database_url,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        literal_binds=connection is None,
        dialect_opts={"paramstyle": "named"} if connection is None else None,
        transaction_per_migration=True,
    )


def run_migrations_offline() -> None:
    """Render migrations without opening a database connection."""
    _configure()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations through a dedicated unpooled sync connection."""
    connectable = create_engine(settings.database_url, poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            lock_acquired = False
            try:
                if connection.dialect.name == "postgresql":
                    _acquire_migration_lock(connection)
                    lock_acquired = True
                _configure(connection)
                with context.begin_transaction():
                    context.run_migrations()
            finally:
                if lock_acquired:
                    _release_migration_lock(connection)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
