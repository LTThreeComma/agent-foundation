"""Complete candidate resolution and behavior-relevant dependency observations."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import AgentConfig, ResolvedRevisionContent
from a13n_service.agents.toolsets import web_selection
from a13n_service.connectivity.connections.models import ConnectionRecord
from a13n_service.connectivity.connectors.models import ConnectorProviderRecord
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.digests import digest_request
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentTemplateRevisionRecord
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.memory.domain import memory_provider_ids
from a13n_service.memory.models import MemoryProviderRecord
from a13n_service.models.models import ModelProviderRecord, ModelRecord
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord
from a13n_service.temporal import assume_utc
from a13n_service.web.domain import provider_selections
from a13n_service.web.models import WebProviderRecord


async def dependency_digest(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    config: AgentConfig,
    resolved: ResolvedRevisionContent,
) -> str:
    """Keep observations distinct from the immutable Agent authoring digest."""
    # Resolution holds shared Model/Provider locks. Observe the complete generation
    # in one query rather than carrying a second authoring result through the command.
    model_ids = [resolved.resolved_model.model_id]
    if config.reviewer is not None:
        model_ids.append(config.reviewer.model)
    media_keys = tuple(key.casefold() for key in config.media_understanding.selections().values())
    rows = (
        await session.execute(
            select(ModelRecord, ModelProviderRecord)
            .join(ModelProviderRecord, ModelProviderRecord.id == ModelRecord.provider_id)
            .where(
                ModelRecord.organization_id == organization_id,
                visible_workspace(ModelRecord.workspace_id, workspace_id),
                or_(ModelRecord.id.in_(model_ids), ModelRecord.normalized_key.in_(media_keys)),
            )
        )
    ).all()
    by_id = {model.id: (model, provider) for model, provider in rows}
    by_key = {model.normalized_key: (model, provider) for model, provider in rows}
    model_observations = [
        {
            "model": model.to_resource().model_dump(mode="json"),
            "provider_updated_at": assume_utc(provider.updated_at).isoformat(),
        }
        for model, provider in [*(by_id[model_id] for model_id in model_ids), *(by_key[key] for key in media_keys)]
    ]
    skills = []
    for selected in resolved.resolved_skills:
        record = await session.get(SkillRecord, selected.skill_id)
        revision = (
            None
            if record is None
            else await session.scalar(
                select(SkillRevisionRecord).where(
                    SkillRevisionRecord.skill_id == selected.skill_id,
                    SkillRevisionRecord.version
                    == (selected.version if selected.version is not None else record.version),
                )
            )
        )
        skills.append(
            {"skill_id": selected.skill_id, "revision_digest": None if revision is None else revision.content_digest}
        )
    dependencies = []

    async def observe(record_type, resource_id: str) -> None:
        # Compare safe generations, never credential values or ciphertext hashes.
        row = (
            await session.execute(
                select(record_type.updated_at, record_type.enabled, record_type.credential_generation).where(
                    record_type.id == resource_id
                )
            )
        ).one_or_none()
        dependencies.append(
            {
                "kind": record_type.__tablename__,
                "id": resource_id,
                "updated_at": None if row is None else assume_utc(row.updated_at).isoformat(),
                "enabled": None if row is None else row.enabled,
                "credential_generation": None if row is None else row.credential_generation,
            }
        )

    for _, selection in provider_selections(web_selection(config.toolsets)):
        await observe(WebProviderRecord, selection.provider_id)
    if config.memory is not None:
        for provider_id in memory_provider_ids(config.memory):
            await observe(MemoryProviderRecord, provider_id)
    for selection in config.subagents.values():
        if selection.environment.template_revision_id is not None:
            provider_id = await session.scalar(
                select(EnvironmentTemplateRevisionRecord.provider_id).where(
                    EnvironmentTemplateRevisionRecord.id == selection.environment.template_revision_id
                )
            )
            if provider_id is not None:
                await observe(EnvironmentProviderRecord, provider_id)
    connections = []
    selections = await ConnectivitySelectionResolver.resolve_in_session(
        session,
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        connection_tools=config.connection_tools,
    )
    for selection in selections:
        row = (
            await session.execute(
                select(
                    ConnectionRecord.updated_at,
                    ConnectionRecord.version,
                    ConnectionRecord.authorization_generation,
                    ConnectionRecord.credential_generation,
                    ConnectionRecord.status,
                ).where(ConnectionRecord.id == selection.connection_id)
            )
        ).one_or_none()
        connections.append(
            {
                "selection": selection.model_dump(mode="json"),
                "updated_at": None if row is None else assume_utc(row.updated_at).isoformat(),
                "version": None if row is None else row.version,
                "authorization_generation": None if row is None else row.authorization_generation,
                "credential_generation": None if row is None else row.credential_generation,
                "status": None if row is None else row.status,
            }
        )
        if selection.connector_provider_id is not None:
            await observe(ConnectorProviderRecord, selection.connector_provider_id)
    return digest_request(
        {
            "resolved": resolved.model_dump(mode="json"),
            "models": model_observations,
            "skills": skills,
            "connections": connections,
            "dependencies": dependencies,
        }
    )
