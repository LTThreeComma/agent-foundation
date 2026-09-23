"""Durable Session activity, after domain locks and before the event cursor lock."""

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.runs.tables import SessionRow


async def touch(session: AsyncSession, workspace_id: str, session_ids: Iterable[str]) -> None:
    identities = sorted(set(session_ids))
    if not identities:
        return
    rows = (
        await session.scalars(
            select(SessionRow)
            .where(SessionRow.workspace_id == workspace_id, SessionRow.id.in_(identities))
            .order_by(SessionRow.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(rows) != len(identities):
        raise ValueError("Session activity must belong to the mutation workspace")
    # NO KEY UPDATE permits concurrent child FK checks; time is read only after the locks.
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    for row in rows:
        row.updated_at = max(row.updated_at, now)
    await session.flush()
