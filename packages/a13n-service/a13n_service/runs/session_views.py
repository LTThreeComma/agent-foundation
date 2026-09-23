"""Scoped SQL summaries for the original Console's durable Session navigation."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import case, column, func, literal, select, true, tuple_
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import ObjectId
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.runs.schemas import RunStatus, RunTrigger
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.grants import workspace_scope


class SessionFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    q: str | None = Field(default=None, max_length=72)
    agent_id: ObjectId | None = None
    status: tuple[RunStatus, ...] = Field(default=(), max_length=6)
    trigger: tuple[RunTrigger, ...] = Field(default=(), max_length=5)
    updated_after: datetime | None = None
    updated_before: datetime | None = None

    @field_validator("q")
    @classmethod
    def normalize_query(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("status", "trigger")
    @classmethod
    def normalize_set(cls, value: tuple) -> tuple:
        return tuple(sorted(set(value)))

    @field_validator("updated_after", "updated_before")
    @classmethod
    def utc_bound(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Activity bounds must include a timezone")
            return value.astimezone(UTC)
        return None

    @model_validator(mode="after")
    def ordered_bounds(self) -> SessionFilters:
        if self.updated_after and self.updated_before and self.updated_after >= self.updated_before:
            raise ValueError("Activity bounds must be ordered")
        return self

    def cursor_owner(self, workspace_id: str) -> str:
        digest = hashlib.sha256(self.model_dump_json().encode()).hexdigest()
        return f"{workspace_id}:{digest}:activity-desc"


class SessionQuery(SessionFilters):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=2048)

    def filters(self) -> SessionFilters:
        return SessionFilters.model_validate(self.model_dump(exclude={"limit", "cursor"}))


class SessionPreview(BaseModel):
    thread_id: str
    run_id: str
    agent_id: str
    agent_name: str
    run_status: RunStatus
    trigger: RunTrigger
    input_text: str | None


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    labels: dict[str, str]
    created_at: datetime
    updated_at: datetime
    version: int
    run_count: int
    preview: SessionPreview | None
    selected_thread_id: str | None


class SessionPage(BaseModel):
    items: list[SessionView]
    next_cursor: str | None


def summary_query(workspace_id: str, filters: SessionFilters):
    """Aggregate in SQL; only the selected source entry contributes a bounded excerpt."""
    run_scope = (RunRow.workspace_id == workspace_id, RunRow.session_id == SessionRow.id)
    totals = (
        select(func.count(RunRow.id).label("run_count")).where(*run_scope).correlate(SessionRow).lateral("run_totals")
    )
    exact_thread = (
        select(ThreadRow.id)
        .where(ThreadRow.workspace_id == workspace_id, ThreadRow.session_id == SessionRow.id, ThreadRow.id == filters.q)
        .correlate(SessionRow)
        .scalar_subquery()
    )
    selected = select(
        RunRow.id, RunRow.thread_id, RunRow.agent_id, RunRow.status, RunRow.trigger, RunRow.source_entry_id
    ).where(*run_scope)
    if filters.q:
        selected = selected.where((SessionRow.id == filters.q) | (RunRow.thread_id == filters.q))
    preview = (
        selected.order_by(RunRow.created_at.desc(), RunRow.id.desc()).limit(1).correlate(SessionRow).lateral("preview")
    )
    blocks = (
        func.jsonb_array_elements(InboxEntryRow.payload["content"])
        .table_valued(column("value", JSONB), with_ordinality="position")
        .render_derived()
    )
    excerpt = (
        select(
            func.left(
                func.string_agg(
                    func.left(blocks.c.value["text"].astext, 512), aggregate_order_by(literal("\n"), blocks.c.position)
                ),
                512,
            )
        )
        .select_from(blocks)
        .where(blocks.c.value["type"].astext == "text")
        .correlate(InboxEntryRow)
        .scalar_subquery()
    )
    activity = SessionRow.updated_at.label("activity")
    query = (
        select(
            SessionRow,
            activity,
            totals.c.run_count,
            exact_thread.label("exact_thread"),
            preview.c.id.label("run_id"),
            preview.c.thread_id,
            preview.c.agent_id,
            preview.c.status.label("run_status"),
            preview.c.trigger,
            AgentRow.name.label("agent_name"),
            case((InboxEntryRow.kind == "message", excerpt), else_=None).label("input_text"),
        )
        .select_from(SessionRow)
        .join(totals, true())
        .outerjoin(preview, true())
        .outerjoin(AgentRow, (AgentRow.id == preview.c.agent_id) & (AgentRow.workspace_id == workspace_id))
        .outerjoin(
            InboxEntryRow,
            (InboxEntryRow.id == preview.c.source_entry_id) & (InboxEntryRow.workspace_id == workspace_id),
        )
        .where(SessionRow.workspace_id == workspace_id)
    )
    if filters.q:
        query = query.where((SessionRow.id == filters.q) | exact_thread.is_not(None))
    if filters.agent_id:
        query = query.where(preview.c.agent_id == filters.agent_id)
    if filters.status:
        query = query.where(preview.c.status.in_(filters.status))
    if filters.trigger:
        query = query.where(preview.c.trigger.in_(filters.trigger))
    if filters.updated_after:
        query = query.where(activity >= filters.updated_after)
    if filters.updated_before:
        query = query.where(activity < filters.updated_before)
    return query, activity


def project(row) -> SessionView:
    resource = row[0]
    preview = (
        SessionPreview(
            thread_id=row.thread_id,
            run_id=row.run_id,
            agent_id=row.agent_id,
            agent_name=row.agent_name,
            run_status=row.run_status,
            trigger=row.trigger,
            input_text=row.input_text,
        )
        if row.run_id is not None
        else None
    )
    return SessionView(
        id=resource.id,
        workspace_id=resource.workspace_id,
        labels=resource.labels,
        created_at=resource.created_at,
        updated_at=row.activity,
        version=resource.version,
        run_count=row.run_count,
        preview=preview,
        selected_thread_id=row.exact_thread or (preview.thread_id if preview else None),
    )


async def read_page(
    session: AsyncSession, workspace_id: str, filters: SessionFilters, *, limit: int, cursor: str | None
) -> SessionPage:
    query, activity = summary_query(workspace_id, filters)
    owner = filters.cursor_owner(workspace_id)
    if cursor:
        timestamp, identity = cursors.time_position(cursor, "sessions", owner)
        query = query.where(tuple_(activity, SessionRow.id) < tuple_(literal(timestamp), literal(identity)))
    rows = (await session.execute(query.order_by(activity.desc(), SessionRow.id.desc()).limit(limit + 1))).all()
    items = [project(row) for row in rows[:limit]]
    return SessionPage(
        items=items,
        next_cursor=cursors.encode("sessions", owner, items[-1].updated_at.isoformat(), items[-1].id)
        if len(rows) > limit
        else None,
    )


async def list_sessions(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None, filters: SessionFilters
) -> SessionPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        return await read_page(session, scope.workspace_id, filters, limit=limit, cursor=cursor)


async def get_session(storage: Storage, actor: Principal, workspace_id: str, identity: str) -> SessionView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        page = await read_page(session, scope.workspace_id, SessionFilters(q=identity), limit=1, cursor=None)
        if not page.items or page.items[0].id != identity:
            raise ServiceError("not_found", "Session was not found")
        return page.items[0]
