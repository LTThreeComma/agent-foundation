"""Managed provider eligibility checks shared by invocation selection and validation."""

from __future__ import annotations

from a13n_harness.memory_plugins import MemoryBackendCatalog
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_workspace
from a13n_service.iam.authorization import ActorPermissions
from a13n_service.memory.resources import require_provider as require_memory_provider
from a13n_service.web.domain import ScrapeSelection, provider_selections
from a13n_service.web.registry import WebProviderRegistry
from a13n_service.web.resources import require_operation
from a13n_service.web.resources import require_provider as require_web_provider

from ..domain import AgentConfig
from ..invocation import MergedAgentRunConfig
from ..toolsets import web_selection


async def validate_providers(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    authored: AgentConfig | MergedAgentRunConfig,
    merged: MergedAgentRunConfig,
    authority: ActorPermissions,
    memory_backend_catalog: MemoryBackendCatalog,
    web_provider_registry: WebProviderRegistry,
) -> None:
    memory = merged.memory
    if memory is not None:
        if authored.memory is None or authored.memory.provider_id != memory.provider_id:
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
            provider_id=memory.provider_id,
            eligible=True,
            catalog=memory_backend_catalog,
        )
    original_web = web_selection(authored.toolsets)
    original_by_operation = dict(provider_selections(original_web))
    for operation, selection in provider_selections(web_selection(merged.toolsets)):
        provider = await require_web_provider(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            provider_id=selection.provider_id,
            eligible=True,
            registry=web_provider_registry,
        )
        require_operation(
            provider,
            operation,
            web_provider_registry,
            selection=selection if isinstance(selection, ScrapeSelection) else None,
        )
        original_operation = original_by_operation.get(operation)
        original_provider = original_operation.provider_id if original_operation is not None else None
        if original_provider != selection.provider_id:
            await authorize_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.web_provider_read,
                authority=authority,
            )
