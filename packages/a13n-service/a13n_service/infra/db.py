"""One engine/session owner and short SQL transaction scopes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import anyio
from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Stamped:
    version: Mapped[int] = mapped_column(server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Storage:
    def __init__(self, url: str, *, pool_size: int = 5, connect_timeout: int = 5, statement_timeout: int = 10):
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=0,
            pool_timeout=connect_timeout,
            connect_args={
                "connect_timeout": connect_timeout,
                "options": f"-c statement_timeout={statement_timeout * 1000}",
            },
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self) -> None:
        with anyio.move_on_after(5, shield=True):
            await self.engine.dispose()


@asynccontextmanager
async def short_session(storage: Storage) -> AsyncIterator[AsyncSession]:
    session = storage.sessions()
    try:
        yield session
    finally:
        with anyio.move_on_after(5, shield=True):
            await session.close()


@asynccontextmanager
async def transaction(storage: Storage) -> AsyncIterator[AsyncSession]:
    async with short_session(storage) as session, session.begin():
        yield session


async def advisory_lock(session: AsyncSession, key: int) -> None:
    """Transaction-only lock for bounded SQL work, never external I/O."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
