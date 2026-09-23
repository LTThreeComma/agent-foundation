"""Thread lookup under tenant scope, thread settings and the per-thread lock every run write starts from."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import conflict, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.schemas import McpHeaders
from a13n_service.runs.tables import RunRow, SessionRow, ThreadRow


async def get_thread(session: AsyncSession, workspace_id: str, thread_id: str, *, lock: bool = False) -> ThreadRow:
    query = select(ThreadRow).where(ThreadRow.workspace_id == workspace_id, ThreadRow.id == thread_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    thread = await session.scalar(query)
    if thread is None:
        raise not_found("thread", thread_id)
    return thread


async def get_run(session: AsyncSession, workspace_id: str, run_id: str, *, lock: bool = False) -> RunRow:
    query = select(RunRow).where(RunRow.workspace_id == workspace_id, RunRow.id == run_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    run = await session.scalar(query)
    if run is None:
        raise not_found("run", run_id)
    return run


def require_open(thread: ThreadRow) -> None:
    if thread.archived_at is not None:
        raise conflict("thread", thread.id, "archived")


def new_session(organization_id: str, workspace_id: str, created_by_id: str) -> SessionRow:
    return SessionRow(
        id=new_object_id("sess"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        labels={},
        created_by_id=created_by_id,
    )


def new_thread(
    session_row: SessionRow,
    *,
    mcp_headers: McpHeaders,
    origin: str = "new",
    origin_thread_id: str | None = None,
    origin_run_id: str | None = None,
    origin_tool_call_id: str | None = None,
) -> ThreadRow:
    return ThreadRow(
        id=new_object_id("thread"),
        organization_id=session_row.organization_id,
        workspace_id=session_row.workspace_id,
        session_id=session_row.id,
        origin=origin,
        origin_thread_id=origin_thread_id,
        origin_run_id=origin_run_id,
        origin_tool_call_id=origin_tool_call_id,
        mcp_headers=dict(mcp_headers),
        labels={},
    )


async def refresh_version(session: AsyncSession, thread: ThreadRow) -> ThreadRow:
    """Inbox triggers bump the thread version in SQL; reload it before returning the thread ETag."""
    await session.flush()
    await session.refresh(thread)
    return thread
