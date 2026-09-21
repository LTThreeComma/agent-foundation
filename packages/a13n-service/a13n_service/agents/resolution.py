"""Agent Revision resolution inside its owning management transaction."""

from __future__ import annotations

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory import MemoryProviderDefinition
from a13n_harness.providers.web.builtins import built_in_web_providers
from a13n_harness.providers.web.definition import WebProviderDefinition
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.selection_resolution import ConnectivitySelectionError, ConnectivitySelectionResolver
from a13n_service.environments.authoring import authorize_template
from a13n_service.iam import AuthenticatedActor, authorize_agent, authorize_agent_skill_binding, authorize_workspace
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.memory.domain import memory_provider_ids
from a13n_service.memory.resources import MemoryProviderError, require_memory_configuration
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.selection import InvocationModelSelection
from a13n_service.web.domain import ScrapeSelection, provider_selections
from a13n_service.web.resources import WebProviderError, require_operation
from a13n_service.web.resources import require_provider as require_web_provider

from .domain import (
    AgentConfig,
    ResolvedAgentModel,
    ResolvedRevisionContent,
    ResolvedSkillBinding,
    ResolvedSubagentEdge,
)
from .errors import AgentError, agent_revision_create_failed
from .models import AgentRecord, AgentRevisionRecord
from .skill_resolution import SkillSelectionInvalid, resolve_skill_bindings
from .toolsets import web_selection
from .validation import AgentConfigValidationError, AgentProtocolPolicy, validate_agent_config

MAX_SUBAGENT_DEPTH = 16
MAX_SUBAGENT_NODES = 256


class AgentResolver:
    """Resolve durable bindings once, retaining locks until the management write commits."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model_selector: AcceptedModelSelector,
        *,
        connectivity_resolver: ConnectivitySelectionResolver | None = None,
        protocol_policy: AgentProtocolPolicy | None = None,
        web_provider_catalog: ProviderCatalog[WebProviderDefinition] | None = None,
        memory_provider_catalog: ProviderCatalog[MemoryProviderDefinition] | None = None,
    ) -> None:
        self._sessions = sessions
        self._model_selector = model_selector
        self._connectivity_resolver = connectivity_resolver or ConnectivitySelectionResolver(sessions)
        self._protocol_policy = protocol_policy or AgentProtocolPolicy()
        self._web_provider_catalog = web_provider_catalog or ProviderCatalog(built_in_web_providers())
        self._memory_provider_catalog = (
            memory_provider_catalog if memory_provider_catalog is not None else ProviderCatalog()
        )

    async def resolve(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        config: AgentConfig,
        creation: bool = False,
    ) -> ResolvedRevisionContent:
        self._validate_local_config(config)
        await _authorize_revision(session, actor=actor, workspace_id=workspace_id, agent_id=agent_id, creation=creation)
        await authorize_template(
            session,
            actor=actor,
            workspace_id=workspace_id,
            template_id=config.default_environment_template_id,
        )
        models = InvocationModelSelection(
            self._sessions,
            self._model_selector,
            organization_id=organization_id,
            workspace_id=workspace_id,
            session=session,
        )
        model = await models.prepare(model_key=config.model.model_key, settings=config.model.settings)
        if config.reviewer is not None:
            await models.prepare(model_id=config.reviewer.model, settings=config.reviewer.model_settings or {})
        await models.resolve(config.media_understanding, include_defaults=False)
        if config.memory is not None:
            if memory_provider_ids(config.memory):
                await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.memory_provider_read,
                )
            await require_memory_configuration(
                session,
                selection=config.memory,
                organization_id=organization_id,
                workspace_id=workspace_id,
                catalog=self._memory_provider_catalog,
            )
        for operation, selection in provider_selections(web_selection(config.toolsets)):
            provider = await require_web_provider(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                provider_id=selection.provider_id,
                eligible=True,
                catalog=self._web_provider_catalog,
            )
            require_operation(
                provider,
                operation,
                self._web_provider_catalog,
                selection=selection if isinstance(selection, ScrapeSelection) else None,
            )
        skills = await self._resolve_skills(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            config=config,
        )
        subagents = await self._resolve_subagents(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            config=config,
        )
        try:
            await self._connectivity_resolver.resolve_in_session(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connection_tools=config.connection_tools,
                lock=True,
            )
        except ConnectivitySelectionError as error:
            raise agent_revision_create_failed(error.code, path=error.path) from error
        return ResolvedRevisionContent(
            resolved_model=ResolvedAgentModel(
                model_id=model.resource.id,
                model_key=model.resource.key,
                settings=config.model.settings,
                characteristics=config.model.characteristics,
            ),
            resolved_skills=skills,
            connection_tools=config.connection_tools,
            resolved_subagents=subagents,
        )

    def _validate_local_config(self, config: AgentConfig) -> None:
        if config.input_adapter.adapter_key != "native" or config.input_adapter.config:
            raise agent_revision_create_failed("input_adapter_unsupported", path="input_adapter")
        for index, skill in enumerate(config.skills):
            if skill.skill_key in {item.skill_key for item in config.skills[:index]}:
                raise agent_revision_create_failed("skill_duplicate", path=f"skills.{index}")
        try:
            validate_agent_config(config, protocol_policy=self._protocol_policy)
        except AgentConfigValidationError as error:
            raise agent_revision_create_failed(error.reason, path=error.path) from error

    async def _resolve_skills(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        config: AgentConfig,
    ) -> tuple[ResolvedSkillBinding, ...]:
        if not config.skills:
            return ()
        await authorize_agent_skill_binding(
            session,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
        try:
            return await resolve_skill_bindings(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                selections=config.skills,
                lock=True,
            )
        except SkillSelectionInvalid as error:
            raise agent_revision_create_failed("skill_selection_invalid", path="skills") from error

    async def _resolve_subagents(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        config: AgentConfig,
    ) -> tuple[ResolvedSubagentEdge, ...]:
        result: list[ResolvedSubagentEdge] = []
        for name, selection in config.subagents.items():
            await authorize_template(
                session, actor=actor, workspace_id=workspace_id, revision_id=selection.environment.template_revision_id
            )
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=selection.agent_id,
                action=WorkspaceAction.agent_read,
            )
            child = await session.scalar(
                select(AgentRecord)
                .where(
                    AgentRecord.id == selection.agent_id,
                    AgentRecord.organization_id == organization_id,
                    AgentRecord.workspace_id == workspace_id,
                )
                .with_for_update(read=True)
            )
            if child is None or not child.enabled or child.archived_at is not None:
                raise agent_revision_create_failed("subagent_unavailable", path=f"subagents.{name}")
            revision_query = select(AgentRevisionRecord).where(
                AgentRevisionRecord.agent_id == child.id,
                AgentRevisionRecord.organization_id == organization_id,
                AgentRevisionRecord.workspace_id == workspace_id,
            )
            if selection.version is None:
                revision_query = revision_query.where(AgentRevisionRecord.id == child.default_revision_id)
            else:
                revision_query = revision_query.where(AgentRevisionRecord.version == selection.version)
            revision = await session.scalar(revision_query.with_for_update(read=True))
            if revision is None:
                raise agent_revision_create_failed("subagent_revision_not_found", path=f"subagents.{name}.version")
            await self._validate_subagent_graph(
                session,
                root_agent_id=agent_id,
                first_revision=revision,
                path=f"subagents.{name}",
            )
            result.append(
                ResolvedSubagentEdge(
                    name=name,
                    child_agent_id=selection.agent_id,
                    child_agent_revision_id=revision.id,
                    description=selection.description,
                    context=selection.context,
                    usage_limits=selection.usage_limits,
                    environment=selection.environment,
                )
            )
        return tuple(result)

    async def _validate_subagent_graph(
        self,
        session: AsyncSession,
        *,
        root_agent_id: str,
        first_revision: AgentRevisionRecord,
        path: str,
    ) -> None:
        pending: list[tuple[AgentRevisionRecord, int]] = [(first_revision, 1)]
        visited: set[str] = set()
        while pending:
            revision, depth = pending.pop()
            if depth > MAX_SUBAGENT_DEPTH:
                raise agent_revision_create_failed("subagent_graph_too_deep", path=path)
            if revision.agent_id == root_agent_id:
                raise agent_revision_create_failed("subagent_cycle", path=path)
            if revision.id in visited:
                continue
            visited.add(revision.id)
            if len(visited) > MAX_SUBAGENT_NODES:
                raise agent_revision_create_failed("subagent_graph_too_large", path=path)
            child_ids = tuple(str(item["child_agent_revision_id"]) for item in revision.resolved_subagents)
            if not child_ids:
                continue
            children = tuple(
                (await session.scalars(select(AgentRevisionRecord).where(AgentRevisionRecord.id.in_(child_ids)))).all()
            )
            if len(children) != len(set(child_ids)):
                raise agent_revision_create_failed("subagent_revision_not_found", path=path)
            pending.extend((child, depth + 1) for child in children)


async def _authorize_revision(
    session: AsyncSession, *, actor: AuthenticatedActor, workspace_id: str, agent_id: str, creation: bool
) -> None:
    if creation:
        await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.agent_create)
    else:
        await authorize_agent(
            session,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=agent_id,
            action=WorkspaceAction.agent_revision_create,
        )


def resolution_error(error: Exception) -> AgentError:
    """Map an owning-domain resolution failure to one bounded Revision-creation error."""

    if isinstance(error, AgentError):
        return error
    if isinstance(error, WebProviderError | MemoryProviderError):
        return AgentError(error.code, error.message, category=error.category)
    return agent_revision_create_failed("managed_resource_unavailable")
