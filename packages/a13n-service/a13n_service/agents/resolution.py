"""Agent Revision dependency selection and reviewed-preview validation."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_harness.memory_plugins import MemoryBackendCatalog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.selection_resolution import (
    ConnectivitySelectionError,
    ConnectivitySelectionResolver,
    PreparedConnectivity,
)
from a13n_service.environments.authoring import authorize_template
from a13n_service.iam import AuthenticatedActor, authorize_agent, authorize_agent_skill_binding, authorize_workspace
from a13n_service.iam.authorization import ActorPermissions, WorkspaceAction
from a13n_service.memory.resources import MemoryProviderError
from a13n_service.memory.resources import require_provider as require_memory_provider
from a13n_service.models.runtime import AcceptedModelSelector, PreparedModelExecution
from a13n_service.storage import short_session
from a13n_service.web.domain import ScrapeSelection, provider_selections
from a13n_service.web.registry import WebProviderRegistry, built_in_web_provider_registry
from a13n_service.web.resources import WebProviderError, require_operation
from a13n_service.web.resources import require_provider as require_web_provider

from .connectivity_resolution import freeze_revision_connectivity
from .domain import (
    AgentConfig,
    ResolvedAgentModel,
    ResolvedRevisionContent,
    ResolvedSkillBinding,
    ResolvedSubagentEdge,
    SubagentSelection,
)
from .errors import AgentError, agent_revision_create_failed
from .models import AgentRecord, AgentRevisionRecord
from .skill_resolution import (
    PreparedSkillBinding,
    SkillSelectionInvalid,
    freeze_skill_bindings,
    prepare_skill_bindings,
)
from .toolsets import web_selection
from .validation import AgentConfigValidationError, AgentProtocolPolicy, validate_agent_config

MAX_SUBAGENT_DEPTH = 16
MAX_SUBAGENT_NODES = 256


@dataclass(frozen=True, slots=True)
class PreparedSubagent:
    name: str
    selection: SubagentSelection
    child_revision_id: str
    child_revision_digest: str


@dataclass(frozen=True, slots=True)
class PreparedRevisionResolution:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    agent_id: str
    config: AgentConfig
    model: PreparedModelExecution
    skills: tuple[PreparedSkillBinding, ...]
    subagents: tuple[PreparedSubagent, ...]
    connectivity: PreparedConnectivity
    reviewer_model: PreparedModelExecution | None = None
    creation: bool = False
    authorization_agent_id: str | None = None


class AgentResolver:
    """Select dependencies at publication; recheck only previews crossing a review boundary."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model_selector: AcceptedModelSelector,
        *,
        connectivity_resolver: ConnectivitySelectionResolver | None = None,
        protocol_policy: AgentProtocolPolicy | None = None,
        web_provider_registry: WebProviderRegistry | None = None,
        memory_backend_catalog: MemoryBackendCatalog | None = None,
    ) -> None:
        self._sessions = sessions
        self._model_selector = model_selector
        self._connectivity_resolver = connectivity_resolver or ConnectivitySelectionResolver(sessions)
        self._protocol_policy = protocol_policy or AgentProtocolPolicy()
        self._web_provider_registry = web_provider_registry or built_in_web_provider_registry()
        self._memory_backend_catalog = (
            memory_backend_catalog if memory_backend_catalog is not None else MemoryBackendCatalog()
        )

    async def prepare(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        config: AgentConfig,
        creation: bool = False,
        authorization_agent_id: str | None = None,
    ) -> PreparedRevisionResolution:
        self._validate_local_config(config)
        async with short_session(self._sessions) as session:
            return await self._select_in_session(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                config=config,
                creation=creation,
                authorization_agent_id=authorization_agent_id,
            )

    async def resolve_in_transaction(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        config: AgentConfig,
        authority: ActorPermissions,
        creation: bool = True,
    ) -> ResolvedRevisionContent:
        """Select and lock eligible dependencies at the publication decision point."""
        self._validate_local_config(config)
        prepared = await self._select_in_session(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            config=config,
            creation=creation,
            authorization_agent_id=None,
            authority=authority,
            lock=True,
        )
        return _resolved_content(prepared)

    async def _select_in_session(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        config: AgentConfig,
        creation: bool,
        authorization_agent_id: str | None,
        authority: ActorPermissions | None = None,
        lock: bool = False,
    ) -> PreparedRevisionResolution:
        await _authorize_revision(
            session,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=authorization_agent_id or agent_id,
            creation=creation,
            authority=authority,
        )
        await self._validate_managed_resources(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            config=config,
            authority=authority,
        )
        model = await self._model_selector.prepare_in_session(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            model_key=config.model.model_key,
            settings=config.model.settings,
            lock=lock,
        )
        reviewer_model = (
            await self._model_selector.prepare_in_session(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                model_id=config.reviewer.model,
                settings=config.reviewer.model_settings or {},
                lock=lock,
            )
            if config.reviewer is not None
            else None
        )
        skills = await self._prepare_skills(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            config=config,
            lock=lock,
            authority=authority,
        )
        subagents = await self._prepare_subagents(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            config=config,
            lock=lock,
            authority=authority,
        )
        try:
            selections = await self._connectivity_resolver.resolve_in_session(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connection_tools=config.connection_tools,
                lock=lock,
                authority=authority,
            )
        except ConnectivitySelectionError as error:
            raise agent_revision_create_failed(error.code, path=error.path) from error
        return PreparedRevisionResolution(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            config=config,
            model=model,
            skills=skills,
            subagents=subagents,
            connectivity=PreparedConnectivity(actor, organization_id, workspace_id, selections),
            reviewer_model=reviewer_model,
            creation=creation,
            authorization_agent_id=authorization_agent_id,
        )

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedRevisionResolution,
    ) -> ResolvedRevisionContent:
        await _authorize_revision(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_id=prepared.authorization_agent_id or prepared.agent_id,
            creation=prepared.creation,
        )
        await self._validate_managed_resources(
            session,
            actor=prepared.actor,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            config=prepared.config,
        )
        await self._model_selector.freeze_in_transaction(session, prepared=prepared.model)
        if prepared.reviewer_model is not None:
            await self._model_selector.freeze_in_transaction(session, prepared=prepared.reviewer_model)
        await self._freeze_skills(session, prepared)
        await self._freeze_subagents(session, prepared)
        await freeze_revision_connectivity(self._connectivity_resolver, session, prepared.connectivity)
        return _resolved_content(prepared)

    async def _validate_managed_resources(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        config: AgentConfig,
        authority: ActorPermissions | None = None,
    ) -> None:
        if config.memory is not None:
            await authorize_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.memory_provider_read,
                authority=authority,
            )
            await require_memory_provider(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                provider_id=config.memory.provider_id,
                eligible=True,
                catalog=self._memory_backend_catalog,
            )
        for operation, selection in provider_selections(web_selection(config.toolsets)):
            provider = await require_web_provider(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                provider_id=selection.provider_id,
                eligible=True,
                registry=self._web_provider_registry,
            )
            require_operation(
                provider,
                operation,
                self._web_provider_registry,
                selection=selection if isinstance(selection, ScrapeSelection) else None,
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

    async def _prepare_skills(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        config: AgentConfig,
        lock: bool = False,
        authority: ActorPermissions | None = None,
    ) -> tuple[PreparedSkillBinding, ...]:
        if not config.skills:
            return ()
        await authorize_agent_skill_binding(
            session,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=agent_id,
            authority=authority,
        )
        try:
            return await prepare_skill_bindings(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                selections=config.skills,
                lock=lock,
            )
        except SkillSelectionInvalid as error:
            raise agent_revision_create_failed("skill_selection_invalid", path="skills") from error

    async def _prepare_subagents(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        config: AgentConfig,
        lock: bool = False,
        authority: ActorPermissions | None = None,
    ) -> tuple[PreparedSubagent, ...]:
        result: dict[str, PreparedSubagent] = {}
        for name, selection in sorted(config.subagents.items(), key=lambda item: item[1].agent_id):
            await authorize_template(
                session,
                actor=actor,
                workspace_id=workspace_id,
                revision_id=selection.environment.template_revision_id,
                authority=authority,
            )
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=selection.agent_id,
                action=WorkspaceAction.agent_read,
                authority=authority,
            )
            child_query = select(AgentRecord).where(
                AgentRecord.id == selection.agent_id,
                AgentRecord.organization_id == organization_id,
                AgentRecord.workspace_id == workspace_id,
            )
            child = await session.scalar(child_query.with_for_update(read=True) if lock else child_query)
            if child is None or not child.enabled or child.archived_at is not None:
                raise agent_revision_create_failed("subagent_unavailable", path=f"subagents.{name}")
            revision_query = select(AgentRevisionRecord).where(
                AgentRevisionRecord.agent_id == child.id,
                AgentRevisionRecord.organization_id == organization_id,
                AgentRevisionRecord.workspace_id == workspace_id,
            )
            if selection.version is None:
                revision_query = revision_query.where(AgentRevisionRecord.id == child.current_revision_id)
            else:
                revision_query = revision_query.where(AgentRevisionRecord.version == selection.version)
            revision = await session.scalar(revision_query.with_for_update(read=True) if lock else revision_query)
            if revision is None:
                raise agent_revision_create_failed("subagent_revision_not_found", path=f"subagents.{name}.version")
            await self._validate_subagent_graph(
                session,
                root_agent_id=agent_id,
                first_revision=revision,
                path=f"subagents.{name}",
            )
            result[name] = PreparedSubagent(
                name=name,
                selection=selection,
                child_revision_id=revision.id,
                child_revision_digest=revision.content_digest,
            )
        return tuple(result[name] for name in config.subagents)

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

    async def _freeze_skills(
        self,
        session: AsyncSession,
        prepared: PreparedRevisionResolution,
    ) -> tuple[ResolvedSkillBinding, ...]:
        if not prepared.skills:
            return ()
        await authorize_agent_skill_binding(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_id=prepared.agent_id,
        )
        try:
            return await freeze_skill_bindings(
                session,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                prepared=prepared.skills,
            )
        except SkillSelectionInvalid as error:
            raise agent_revision_create_failed("skill_selection_invalid", path="skills") from error

    async def _freeze_subagents(
        self,
        session: AsyncSession,
        prepared: PreparedRevisionResolution,
    ) -> tuple[ResolvedSubagentEdge, ...]:
        result: list[ResolvedSubagentEdge] = []
        for expected in prepared.subagents:
            await authorize_agent(
                session,
                actor=prepared.actor,
                workspace_id=prepared.workspace_id,
                agent_id=expected.selection.agent_id,
                action=WorkspaceAction.agent_read,
            )
            revision = await session.scalar(
                select(AgentRevisionRecord)
                .where(
                    AgentRevisionRecord.id == expected.child_revision_id,
                    AgentRevisionRecord.agent_id == expected.selection.agent_id,
                    AgentRevisionRecord.organization_id == prepared.organization_id,
                    AgentRevisionRecord.workspace_id == prepared.workspace_id,
                )
                .with_for_update()
            )
            if revision is None or revision.content_digest != expected.child_revision_digest:
                raise agent_revision_create_failed("subagent_revision_changed", path=f"subagents.{expected.name}")
            result.append(
                ResolvedSubagentEdge(
                    name=expected.name,
                    child_agent_id=expected.selection.agent_id,
                    child_agent_revision_id=expected.child_revision_id,
                    description=expected.selection.description,
                    context=expected.selection.context,
                    usage_limits=expected.selection.usage_limits,
                    environment=expected.selection.environment,
                )
            )
        return tuple(result)


def _resolved_content(prepared: PreparedRevisionResolution) -> ResolvedRevisionContent:
    config = prepared.config
    return ResolvedRevisionContent(
        resolved_model=ResolvedAgentModel(
            model_id=prepared.model.resource.id,
            model_key=prepared.model.resource.key,
            settings=config.model.settings,
            characteristics=config.model.characteristics,
        ),
        resolved_skills=tuple(item.binding for item in prepared.skills),
        connection_tools=config.connection_tools,
        resolved_subagents=tuple(
            ResolvedSubagentEdge(
                name=item.name,
                child_agent_id=item.selection.agent_id,
                child_agent_revision_id=item.child_revision_id,
                description=item.selection.description,
                context=item.selection.context,
                usage_limits=item.selection.usage_limits,
                environment=item.selection.environment,
            )
            for item in prepared.subagents
        ),
    )


async def _authorize_revision(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    agent_id: str,
    creation: bool,
    authority: ActorPermissions | None = None,
) -> None:
    if creation:
        await authorize_workspace(
            session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.agent_create, authority=authority
        )
    else:
        await authorize_agent(
            session,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=agent_id,
            action=WorkspaceAction.agent_revision_create,
            authority=authority,
        )


def resolution_error(error: Exception) -> AgentError:
    """Map an owning-domain resolution failure to one bounded Revision-creation error."""

    if isinstance(error, AgentError):
        return error
    if isinstance(error, WebProviderError | MemoryProviderError):
        return AgentError(error.code, error.message, category=error.category)
    return agent_revision_create_failed("managed_resource_unavailable")
