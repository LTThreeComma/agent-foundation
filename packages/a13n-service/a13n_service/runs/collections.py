"""Bounded durable conversation navigation; no display or object reads."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import literal, select, tuple_

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.schemas import RunView
from a13n_service.runs.tables import RunRow, SessionRow, ThreadRow
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.grants import workspace_scope


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    labels: dict[str, str]
    created_at: datetime
    version: int


class ThreadView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    session_id: str
    origin: str
    current_run_id: str | None
    head_run_id: str | None
    last_run_id: str | None
    labels: dict[str, str]
    created_at: datetime
    version: int


class SessionPage(BaseModel):
    items: list[SessionView]
    next_cursor: str | None


class ThreadPage(BaseModel):
    items: list[ThreadView]
    next_cursor: str | None


class RunPage(BaseModel):
    items: list[RunView]
    next_cursor: str | None


async def list_sessions(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> SessionPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        owner = workspace_id
        query = select(SessionRow).where(SessionRow.workspace_id == workspace_id)

        if cursor is not None:
            timestamp, identity = cursors.time_position(cursor, "sessions", owner)
            query = query.where(
                tuple_(SessionRow.created_at, SessionRow.id) < tuple_(literal(timestamp), literal(identity))
            )
        rows = (
            await session.scalars(query.order_by(SessionRow.created_at.desc(), SessionRow.id.desc()).limit(limit + 1))
        ).all()
        return SessionPage(
            items=[SessionView.model_validate(row) for row in rows[:limit]],
            next_cursor=cursors.encode("sessions", owner, rows[limit - 1].created_at.isoformat(), rows[limit - 1].id)
            if len(rows) > limit
            else None,
        )


async def get_session(storage: Storage, actor: Principal, workspace_id: str, identity: str) -> SessionView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await session.get(SessionRow, identity)
        if row is None or row.workspace_id != scope.workspace_id:
            raise ServiceError("not_found", "Session was not found")
        return SessionView.model_validate(row)


async def list_threads(
    storage: Storage, actor: Principal, workspace_id: str, *, session_id: str | None, limit: int, cursor: str | None
) -> ThreadPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        owner = workspace_id + ":" + (session_id or "*")
        query = select(ThreadRow).where(ThreadRow.workspace_id == workspace_id)

        if session_id is not None:
            parent = await session.get(SessionRow, session_id)
            if parent is None or parent.workspace_id != workspace_id:
                raise ServiceError("not_found", "Session was not found")
            query = query.where(ThreadRow.session_id == session_id)

        if cursor is not None:
            timestamp, identity = cursors.time_position(cursor, "threads", owner)
            query = query.where(
                tuple_(ThreadRow.created_at, ThreadRow.id) < tuple_(literal(timestamp), literal(identity))
            )
        rows = (
            await session.scalars(query.order_by(ThreadRow.created_at.desc(), ThreadRow.id.desc()).limit(limit + 1))
        ).all()
        return ThreadPage(
            items=[ThreadView.model_validate(row) for row in rows[:limit]],
            next_cursor=cursors.encode("threads", owner, rows[limit - 1].created_at.isoformat(), rows[limit - 1].id)
            if len(rows) > limit
            else None,
        )


async def get_thread(storage: Storage, actor: Principal, workspace_id: str, identity: str) -> ThreadView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await session.get(ThreadRow, identity)
        if row is None or row.workspace_id != scope.workspace_id:
            raise ServiceError("not_found", "Thread was not found")
        return ThreadView.model_validate(row)


async def list_runs(
    storage: Storage, actor: Principal, workspace_id: str, *, thread_id: str | None, limit: int, cursor: str | None
) -> RunPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        owner = workspace_id + ":" + (thread_id or "*")
        query = select(RunRow).where(RunRow.workspace_id == workspace_id)

        if thread_id is not None:
            parent = await session.get(ThreadRow, thread_id)
            if parent is None or parent.workspace_id != workspace_id:
                raise ServiceError("not_found", "Thread was not found")
            query = query.where(RunRow.thread_id == thread_id)

        if cursor is not None:
            timestamp, identity = cursors.time_position(cursor, "runs", owner)
            query = query.where(tuple_(RunRow.created_at, RunRow.id) < tuple_(literal(timestamp), literal(identity)))
        rows = (
            await session.scalars(query.order_by(RunRow.created_at.desc(), RunRow.id.desc()).limit(limit + 1))
        ).all()
        return RunPage(
            items=[RunView.model_validate(row) for row in rows[:limit]],
            next_cursor=cursors.encode("runs", owner, rows[limit - 1].created_at.isoformat(), rows[limit - 1].id)
            if len(rows) > limit
            else None,
        )
