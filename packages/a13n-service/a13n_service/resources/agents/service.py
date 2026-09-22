"""Short, authorized transactions for agent heads and immutable revisions."""

import hashlib
import json

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents.schemas import AgentConfig, AgentCreate, AgentView, RevisionCreate, RevisionView
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.models.tables import ModelProviderRow, ModelRow
from a13n_service.tenancy.authorize import Principal, Scope, authorize
from a13n_service.tenancy.grants import workspace_scope


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
        head = await session.get(AgentRow, agent_id, with_for_update=True)
        if head is None or head.workspace_id != workspace_id:
            raise ServiceError("not_found", "Agent was not found")
        scope = await workspace_scope(session, actor, workspace_id, "write")
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
        await workspace_scope(session, actor, workspace_id, "read")
        row = await session.get(AgentRow, agent_id)
        if row is None or row.workspace_id != workspace_id:
            raise ServiceError("not_found", "Agent was not found")
        return AgentView.model_validate(row)


async def get_revision(
    storage: Storage, actor: Principal, workspace_id: str, agent_id: str, revision_id: str
) -> RevisionView:
    async with short_session(storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
        row = await session.get(AgentRevisionRow, revision_id)
        if row is None or row.workspace_id != workspace_id or row.agent_id != agent_id:
            raise ServiceError("not_found", "Agent revision was not found")
        return RevisionView.model_validate(row)
