"""Short, authorized transactions for agent heads and immutable revisions."""

import hashlib
import json

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents.schemas import (
    AgentConfig,
    AgentCreate,
    AgentPage,
    AgentView,
    RevisionCreate,
    RevisionPage,
    RevisionView,
)
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.connections.scope import connection_scope, validate_tools
from a13n_service.resources.connections.service import resolve as resolve_connection
from a13n_service.resources.models.tables import ModelProviderRow, ModelRow
from a13n_service.tenancy.authorize import Principal, Scope, authorize
from a13n_service.tenancy.grants import workspace_scope


async def resolve_agent(session: AsyncSession, workspace_id: str, reference: str, *, lock: bool = False) -> AgentRow:
    query = (
        select(AgentRow)
        .where(AgentRow.workspace_id == workspace_id, (AgentRow.id == reference) | (AgentRow.key == reference))
        .order_by((AgentRow.id == reference).desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise ServiceError("not_found", "Agent was not found")
    return row


async def validate_configuration(session: AsyncSession, actor: Principal, scope: Scope, config: AgentConfig) -> None:
    model = await session.get(ModelRow, config.model_id)
    if (
        model is None
        or model.organization_id != scope.organization_id
        or model.workspace_id not in {None, scope.workspace_id}
    ):
        raise ServiceError("invalid_argument", "Agent model is outside the workspace")
    provider = await session.get(ModelProviderRow, model.provider_id)
    if provider is None or not model.enabled or not provider.enabled:
        raise ServiceError("disabled", "Agent model or provider is disabled")
    authorize(actor, Scope(model.organization_id, model.workspace_id), "read")
    authorize(actor, Scope(provider.organization_id, provider.workspace_id), "read")
    for selection in connection_scope(config).values():
        connection = await resolve_connection(session, actor, scope, selection.connection_id, verb="read")
        validate_tools(connection, selection)


async def add_revision(
    session: AsyncSession, head: AgentRow, config: AgentConfig, *, actor: Principal, note: str | None
) -> AgentRevisionRow:
    """Caller holds the head lock; new heads are private to their insertion transaction."""
    number = (
        await session.execute(
            select(func.coalesce(func.max(AgentRevisionRow.number), 0)).where(AgentRevisionRow.agent_id == head.id)
        )
    ).scalar_one()
    value = config.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    revision = AgentRevisionRow(
        id=new_object_id("apr"),
        organization_id=head.organization_id,
        workspace_id=head.workspace_id,
        agent_id=head.id,
        number=number + 1,
        config=value,
        digest=digest,
        note=note,
        created_by_id=actor.id,
    )
    session.add(revision)
    await session.flush()
    if head.default_revision_id is None:
        head.default_revision_id = revision.id
    return revision


async def create_agent(storage: Storage, actor: Principal, workspace_id: str, body: AgentCreate) -> AgentView:
    try:
        async with transaction(storage) as session:
            scope = await workspace_scope(session, actor, workspace_id, "write")
            assert scope.workspace_id is not None
            workspace_id = scope.workspace_id
            await validate_configuration(session, actor, scope, body.config)
            head = AgentRow(
                id=new_object_id("ap"),
                organization_id=scope.organization_id,
                workspace_id=workspace_id,
                key=body.key,
                name=body.name,
                description=body.description,
                labels=body.labels,
                source="custom",
                created_by_id=actor.id,
                updated_by_id=actor.id,
            )
            session.add(head)
            await session.flush()
            await add_revision(session, head, body.config, actor=actor, note=None)
            record(
                session,
                organization_id=scope.organization_id,
                workspace_id=workspace_id,
                actor_id=actor.id,
                action="agent.create",
                target_kind="agent",
                target_id=head.id,
            )
            await session.flush()
            await session.refresh(head)
            return AgentView.model_validate(head)
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise ServiceError("already_exists", "Agent key already exists") from None
        raise


async def create_revision(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    agent_id: str,
    body: RevisionCreate,
    *,
    if_match: str | None,
) -> RevisionView:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        head = await resolve_agent(session, workspace_id, agent_id, lock=True)
        require_match(if_match, head.id, head.version)
        if head.archived_at is not None or head.source != "custom":
            raise ServiceError("disabled", "Agent does not accept new revisions")
        await validate_configuration(session, actor, scope, body.config)
        revision = await add_revision(session, head, body.config, actor=actor, note=body.note)
        await session.execute(
            update(AgentRow)
            .where(AgentRow.id == head.id)
            .values(
                default_revision_id=revision.id if body.make_default else head.default_revision_id,
                updated_by_id=actor.id,
            )
        )
        record(
            session,
            organization_id=scope.organization_id,
            workspace_id=workspace_id,
            actor_id=actor.id,
            action="agent.revision.create",
            target_kind="agent_revision",
            target_id=revision.id,
        )
        return RevisionView.model_validate(revision)


async def get_agent(storage: Storage, actor: Principal, workspace_id: str, agent_id: str) -> AgentView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        row = await resolve_agent(session, workspace_id, agent_id)
        return AgentView.model_validate(row)


async def get_revision(
    storage: Storage, actor: Principal, workspace_id: str, agent_id: str, revision_id: str
) -> RevisionView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        head = await resolve_agent(session, workspace_id, agent_id)
        agent_id = head.id
        row = await session.get(AgentRevisionRow, revision_id)
        if row is None or row.workspace_id != workspace_id or row.agent_id != agent_id:
            raise ServiceError("not_found", "Agent revision was not found")
        return RevisionView.model_validate(row)


async def list_agents(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> AgentPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        after = cursors.id_position(cursor, "agents", workspace_id)
        rows = (
            await session.scalars(
                select(AgentRow)
                .where(AgentRow.workspace_id == workspace_id, AgentRow.id > after)
                .order_by(AgentRow.id)
                .limit(limit + 1)
            )
        ).all()
        return AgentPage(
            items=[AgentView.model_validate(row) for row in rows[:limit]],
            next_cursor=cursors.encode("agents", workspace_id, rows[limit - 1].id) if len(rows) > limit else None,
        )


async def list_revisions(
    storage: Storage, actor: Principal, workspace_id: str, agent_id: str, *, limit: int, cursor: str | None
) -> RevisionPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        head = await resolve_agent(session, workspace_id, agent_id)
        agent_id = head.id
        query = select(AgentRevisionRow).where(AgentRevisionRow.agent_id == agent_id)
        if cursor is not None:
            position = cursors.decode(cursor, "agent_revisions", agent_id)
            if len(position) != 1 or type(position[0]) is not int or not 0 < position[0] <= 2**31 - 1:
                raise ServiceError("invalid_cursor", "Invalid revision cursor")
            query = query.where(AgentRevisionRow.number < position[0])
        rows = (await session.scalars(query.order_by(AgentRevisionRow.number.desc()).limit(limit + 1))).all()
        return RevisionPage(
            items=[RevisionView.model_validate(row) for row in rows[:limit]],
            next_cursor=cursors.encode("agent_revisions", agent_id, rows[limit - 1].number)
            if len(rows) > limit
            else None,
        )


async def set_default(
    storage: Storage, actor: Principal, workspace_id: str, agent_id: str, revision_id: str, *, if_match: str | None
) -> AgentView:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        head = await resolve_agent(session, workspace_id, agent_id, lock=True)
        require_match(if_match, head.id, head.version)
        if head.archived_at is not None or head.source != "custom":
            raise ServiceError("disabled", "Agent does not accept a default change")
        revision = await session.get(AgentRevisionRow, revision_id)
        if revision is None or revision.agent_id != head.id:
            raise ServiceError("not_found", "Agent revision was not found")
        await validate_configuration(session, actor, scope, AgentConfig.model_validate(revision.config))
        head.default_revision_id = revision.id
        head.updated_by_id = actor.id
        record(
            session,
            organization_id=scope.organization_id,
            workspace_id=workspace_id,
            actor_id=actor.id,
            action="agent.revision.set_default",
            target_kind="agent",
            target_id=head.id,
        )
        await session.flush()
        await session.refresh(head)
        return AgentView.model_validate(head)
