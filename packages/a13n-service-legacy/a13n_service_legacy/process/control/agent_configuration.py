"""Compose configuration authoring over the shared Agent and Run authorities."""

from a13n_harness.providers.endpoint_policy import EndpointPolicy

from a13n_service_legacy.agent_configuration.application import ConfigurationApplication
from a13n_service_legacy.agent_configuration.conversations import ConfigurationConversations
from a13n_service_legacy.agent_configuration.definition import load_definition
from a13n_service_legacy.agent_configuration.drafts import ConfigurationDrafts
from a13n_service_legacy.agent_configuration.inputs import ConfigurationInputs
from a13n_service_legacy.agent_configuration.knowledge import KnowledgeFiles
from a13n_service_legacy.agent_configuration.readiness import ConfigurationReadiness
from a13n_service_legacy.agent_configuration.review import ConfigurationReviews
from a13n_service_legacy.agent_configuration.service import ConfigurationService
from a13n_service_legacy.agent_configuration.system_agent import SystemConfigurationAgent
from a13n_service_legacy.agents.resolution import AgentResolver
from a13n_service_legacy.assets.catalog import AssetCatalog
from a13n_service_legacy.environments.websocket.coordination import ConnectionCoordination
from a13n_service_legacy.hooks import InlineHookValidator
from a13n_service_legacy.interactions.acceptance import RunAcceptanceService
from a13n_service_legacy.interactions.command_preparation import CommandInput
from a13n_service_legacy.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service_legacy.process.agents import AgentResources
from a13n_service_legacy.process.resources import ExecutionResources
from a13n_service_legacy.process.runtime import SharedRuntime
from a13n_service_legacy.settings import Settings


def build_configuration_service(
    settings: Settings,
    shared: SharedRuntime,
    resources: AgentResources,
    execution: ExecutionResources,
    resolver: AgentResolver,
    assets: AssetCatalog,
    hooks: InlineHookValidator,
) -> ConfigurationService:
    if shared.memory_behaviors is None:
        raise RuntimeError("Execution memory behavior composition is required")
    sessions = shared.storage.sessions
    definition = load_definition(total_tokens_limit=settings.configuration_assistant.total_tokens_limit)
    readiness = ConfigurationReadiness(sessions, execution.model_provider_catalog, definition)
    states = RunStateStore(shared.storage.objects)
    return ConfigurationService(
        conversations=ConfigurationConversations(sessions),
        drafts=ConfigurationDrafts(sessions, resolver),
        application=ConfigurationApplication(sessions, resolver),
        readiness=readiness,
        reviews=ConfigurationReviews(sessions),
        inputs=ConfigurationInputs(
            sessions,
            resources.invocations,
            RunAcceptanceService(
                sessions,
                states,
                RunPayloadStore(shared.storage.objects),
                hooks,
                lifecycle=shared.lifecycle,
                bindings=shared.memory_behaviors,
                coordination=ConnectionCoordination(shared.storage.redis),
            ),
            states,
            CommandInput(sessions, assets, EndpointPolicy()),
            readiness,
            SystemConfigurationAgent(sessions, definition),
            definition,
            KnowledgeFiles(),
            execution_max_attempts=settings.gateway.run_execution_max_attempts,
            max_handoffs=settings.gateway.run_max_handoffs,
            queue_name=settings.gateway.run_queue_name,
            priority=settings.gateway.run_priority,
        ),
    )
