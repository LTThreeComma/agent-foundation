"""Canonical async SQLAlchemy engine and session factory."""

from __future__ import annotations

from math import ceil

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from converge_foundation_service.settings import ServiceSettings

SessionFactory = async_sessionmaker[AsyncSession]


def _milliseconds(seconds: float) -> int:
    return max(1, round(seconds * 1000))


def _connect_args(settings: ServiceSettings) -> dict[str, object]:
    """Build PostgreSQL driver timeouts without leaking them into other dialects."""
    if make_url(settings.database_url).get_backend_name() != "postgresql":
        return {}
    return {
        "connect_timeout": max(1, ceil(settings.database_connect_timeout_seconds)),
        "options": f"-c statement_timeout={_milliseconds(settings.database_statement_timeout_seconds)}",
    }


def create_database_engine(settings: ServiceSettings) -> AsyncEngine:
    """Create the process-wide async engine from typed settings."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
        pool_pre_ping=True,
        connect_args=_connect_args(settings),
    )


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Create sessions that remain explicit after transaction commit."""
    return async_sessionmaker(engine, expire_on_commit=False)
