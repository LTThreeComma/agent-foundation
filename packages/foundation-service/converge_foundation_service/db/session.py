"""Short-lived async database session helpers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from anyio import move_on_after
from sqlalchemy.ext.asyncio import AsyncSession

from converge_foundation_service.db.engine import SessionFactory

_CLEANUP_TIMEOUT_SECONDS = 5.0
logger = logging.getLogger("converge_foundation_service.db.session")


async def _cleanup(operation: Callable[[], Awaitable[None]], operation_name: str) -> None:
    """Shield one cleanup operation while keeping shutdown bounded."""
    with move_on_after(_CLEANUP_TIMEOUT_SECONDS, shield=True) as scope:
        await operation()
    if scope.cancel_called:
        logger.warning(
            "database_session_cleanup_timed_out",
            extra={"event": "database_session_cleanup_timed_out", "operation": operation_name},
        )


async def _rollback_and_close(session: AsyncSession) -> None:
    try:
        await _cleanup(session.rollback, "rollback")
    finally:
        await _cleanup(session.close, "close")


@asynccontextmanager
async def short_session(factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """Yield a fresh read-oriented session and always release its transaction."""
    session = factory()
    try:
        yield session
    finally:
        await _rollback_and_close(session)


@asynccontextmanager
async def transaction(factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """Commit one short unit of work, or roll it back on any failure."""
    session = factory()
    try:
        yield session
        await session.commit()
    except BaseException:
        await _rollback_and_close(session)
        raise
    else:
        await _cleanup(session.close, "close")
