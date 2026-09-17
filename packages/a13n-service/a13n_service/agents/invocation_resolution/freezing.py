"""Short-transaction freezing for prepared Agent invocations."""

from __future__ import annotations

from a13n_harness.memory_plugins import MemoryBackendCatalog
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.selection_resolution import (
    ConnectivitySelectionResolver,
)
from a13n_service.iam import (
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.iam.authorization import ActorPermissions, read_actor_permissions
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service import ModelError
from a13n_service.web.registry import WebProviderRegistry

from ..connectivity_resolution import freeze_invocation_connectivity
from ..domain import (
    AgentConfig,
    ChildAgentExecution,
)
from ..errors import (
    agent_revision_not_executable,
    current_revision_conflict,
    map_authorization_error,
    map_model_error,
)
from .composition import compose_config
from .contracts import (
    AgentSelectorKind,
    FrozenAgentInvocation,
    PreparedAgentInvocation,
)
from .providers import validate_providers
from .queries import load_agent_record, load_revision_record, require_invocable_agent
from .skills import freeze_skills


class AgentInvocationFreezer:
    """Reauthorize and freeze prepared invocation evidence in one short transaction."""

    def __init__(
        self,
        model_selector: AcceptedModelSelector,
        *,
        connectivity_resolver: ConnectivitySelectionResolver,
        web_provider_registry: WebProviderRegistry,
        memory_backend_catalog: MemoryBackendCatalog,
    ) -> None:
        self._model_selector = model_selector
        self._connectivity_resolver = connectivity_resolver
        self._web_provider_registry = web_provider_registry
        self._memory_backend_catalog = memory_backend_catalog

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentInvocation,
        authority: ActorPermissions | None = None,
    ) -> FrozenAgentInvocation:
        if authority is None:
            try:
                authority = await read_actor_permissions(
                    session,
                    actor=prepared.actor,
                    workspace_id=prepared.workspace_id,
                    agent_ids=_selected_agent_ids(prepared),
                )
            except AuthorizationError as error:
                raise map_authorization_error(error) from error
        _, _, skills, _ = await self._check_dependencies(session, prepared=prepared, authority=authority)

        child_configs = {}
        for item in prepared.subagents:
            child = item.invocation
            frozen_child = await self.freeze_in_transaction(session, prepared=child, authority=authority)
            assert child.agent_revision_id is not None and child.revision_content_digest is not None
            child_configs[child.agent_revision_id] = ChildAgentExecution(
                agent_id=child.agent_id,
                revision_content_digest=child.revision_content_digest,
                effective_config=frozen_child.effective_config,
                connection_selections=frozen_child.connection_selections,
            )
        return compose_config(prepared, skills=skills, child_configs=child_configs)

    async def validate_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentInvocation,
        frozen: FrozenAgentInvocation,
        authority: ActorPermissions | None = None,
    ) -> tuple[bool, bool]:
        """Recheck mutable evidence without rebuilding effective configuration."""
        if authority is None:
            try:
                authority = await read_actor_permissions(
                    session,
                    actor=prepared.actor,
                    workspace_id=prepared.workspace_id,
                    agent_ids=_selected_agent_ids(prepared),
                )
            except AuthorizationError as error:
                raise map_authorization_error(error) from error
        execution, reviewer_execution, skills, connectivity = await self._check_dependencies(
            session, prepared=prepared, authority=authority
        )
        non_skill_matches = (
            frozen.agent_id == prepared.agent_id
            and frozen.agent_revision_id == prepared.agent_revision_id
            and frozen.selector_kind == prepared.selector_kind
            and execution == frozen.effective_config.resolved_model.execution
            and reviewer_execution
            == (
                frozen.effective_config.resolved_reviewer_model.execution
                if frozen.effective_config.resolved_reviewer_model is not None
                else None
            )
            and connectivity.connection_selections == frozen.connection_selections
        )
        skills_match = skills == frozen.effective_config.skills
        expected_children = {item.invocation.agent_revision_id for item in prepared.subagents}
        if set(frozen.effective_config.child_configs) != expected_children:
            return False, skills_match
        for item in prepared.subagents:
            child = item.invocation
            assert child.agent_revision_id is not None
            expected = frozen.effective_config.child_configs[child.agent_revision_id]
            child_non_skill, child_skills = await self.validate_in_transaction(
                session,
                prepared=child,
                authority=authority,
                frozen=FrozenAgentInvocation(
                    agent_id=expected.agent_id,
                    agent_revision_id=child.agent_revision_id,
                    selector_kind=child.selector_kind,
                    effective_config=expected.effective_config,
                    connection_selections=expected.connection_selections,
                ),
            )
            non_skill_matches = non_skill_matches and child_non_skill
            skills_match = skills_match and child_skills
        return non_skill_matches, skills_match

    async def _check_dependencies(
        self, session: AsyncSession, *, prepared: PreparedAgentInvocation, authority: ActorPermissions
    ):
        try:
            if prepared.configuration_context is not None:
                from a13n_service.agent_configuration.authorization import authorize_invocation

                await authorize_invocation(
                    session, actor=prepared.actor, agent_id=prepared.agent_id, context=prepared.configuration_context
                )
            else:
                await authorize_agent(
                    session,
                    actor=prepared.actor,
                    workspace_id=prepared.workspace_id,
                    agent_id=prepared.agent_id,
                    action=WorkspaceAction.agent_invoke,
                    authority=authority,
                )
            agent = await load_agent_record(
                session,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                agent_id=prepared.agent_id,
                for_update=True,
            )
            require_invocable_agent(
                agent,
                policy=prepared.root_state_policy,
            )
            if (
                prepared.expected_current_revision_id is not None
                and agent.current_revision_id != prepared.expected_current_revision_id
            ):
                raise current_revision_conflict(agent.current_revision_id)
            if (
                prepared.selector_kind is AgentSelectorKind.current
                and agent.current_revision_id != prepared.agent_revision_id
            ):
                raise current_revision_conflict(agent.current_revision_id)
            if prepared.agent_revision_id is None:
                # This source is reachable only through protected configuration admission.
                authored = prepared.merged
            else:
                revision_record = await load_revision_record(
                    session,
                    organization_id=prepared.organization_id,
                    workspace_id=prepared.workspace_id,
                    agent_id=prepared.agent_id,
                    revision_id=prepared.agent_revision_id,
                    for_update=True,
                )
                if revision_record.content_digest != prepared.revision_content_digest:
                    raise agent_revision_not_executable("revision_changed")
                authored = AgentConfig.model_validate(revision_record.config)
            await authorize_workspace(
                session,
                actor=prepared.actor,
                workspace_id=prepared.workspace_id,
                action=WorkspaceAction.models_read,
                authority=authority,
            )
            try:
                execution = await self._model_selector.freeze_in_transaction(session, prepared=prepared.model)
                reviewer_execution = (
                    await self._model_selector.freeze_in_transaction(session, prepared=prepared.reviewer_model)
                    if prepared.reviewer_model is not None
                    else None
                )
            except ModelError as error:
                raise map_model_error(error) from error
            await validate_providers(
                session,
                actor=prepared.actor,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                authored=authored,
                merged=prepared.merged,
                authority=authority,
                memory_backend_catalog=self._memory_backend_catalog,
                web_provider_registry=self._web_provider_registry,
            )
            skills = await freeze_skills(session, prepared, authority=authority)
            connectivity = await freeze_invocation_connectivity(
                self._connectivity_resolver,
                session,
                prepared.connectivity,
                authority=authority,
            )
        except AuthorizationError as error:
            raise map_authorization_error(error) from error

        return execution, reviewer_execution, skills, connectivity


def _selected_agent_ids(prepared: PreparedAgentInvocation) -> tuple[str, ...]:
    return (
        prepared.agent_id,
        *(agent_id for child in prepared.subagents for agent_id in _selected_agent_ids(child.invocation)),
    )
