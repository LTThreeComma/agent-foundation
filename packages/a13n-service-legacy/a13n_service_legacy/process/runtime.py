"""Typed process composition."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a13n_service_legacy.agent_configuration.service import ConfigurationService
    from a13n_service_legacy.agents.application import AgentManagement
    from a13n_service_legacy.assets.catalog import AssetCatalog
    from a13n_service_legacy.assets.uploads import AssetUploadService
    from a13n_service_legacy.bots.connectivity.service import BotService
    from a13n_service_legacy.connectivity.execution import ExternalToolRuntime
    from a13n_service_legacy.connectivity.runtime import ConnectivityRuntime
    from a13n_service_legacy.environments.devices import DeviceDiscovery
    from a13n_service_legacy.environments.lifecycle import EnvironmentLifecycle
    from a13n_service_legacy.environments.maintenance import EnvironmentMaintenanceLoop
    from a13n_service_legacy.environments.mounts import RunEnvironmentMountService
    from a13n_service_legacy.environments.service import EnvironmentService
    from a13n_service_legacy.environments.websocket.relay_runtime import RelayResponseRuntime
    from a13n_service_legacy.environments.websocket.runtime import ClientConnectionRuntime
    from a13n_service_legacy.environments.websocket.worker_connections import WorkerClientConnections
    from a13n_service_legacy.gateway import GatewayRuntime
    from a13n_service_legacy.hooks.management import HookSubscriptionService
    from a13n_service_legacy.iam import RequestAuthenticator
    from a13n_service_legacy.iam.runtime import IdentityRuntime
    from a13n_service_legacy.interactions.lifecycle import LifecycleWriter
    from a13n_service_legacy.interactions.worker import WorkerExecutionLoop
    from a13n_service_legacy.lifecycle.service import LifecycleEventService
    from a13n_service_legacy.memory.behaviors import MemoryBehaviors
    from a13n_service_legacy.memory.providers import MemoryProviderService
    from a13n_service_legacy.memory.service import MemoryService
    from a13n_service_legacy.models.model_factory import NativeModelFactory
    from a13n_service_legacy.models.provider_service import ModelProviderService
    from a13n_service_legacy.models.service import ModelService
    from a13n_service_legacy.observability import ObservabilityRuntime
    from a13n_service_legacy.run_stream import RedisRunStream, RunDisplayStore
    from a13n_service_legacy.secrets import SecretProtector
    from a13n_service_legacy.settings import Settings
    from a13n_service_legacy.skills.catalog import SkillCatalogService
    from a13n_service_legacy.skills.publication import SkillPublicationService
    from a13n_service_legacy.skills.runtime import SkillRuntimePreparer
    from a13n_service_legacy.skills.uploads import SkillUploadService
    from a13n_service_legacy.storage import StorageResources
    from a13n_service_legacy.subagents.maintenance import SubagentMaintenance
    from a13n_service_legacy.trace_query.service import TraceQueryService
    from a13n_service_legacy.web.service import WebProviderService

logger = logging.getLogger("a13n_service_legacy.process.runtime")


@dataclass(frozen=True, slots=True)
class SharedRuntime:
    """Resources constructed once for every process role."""

    storage: StorageResources
    lifecycle: LifecycleWriter
    secret_protector: SecretProtector
    memories: MemoryService | None = None
    memory_behaviors: MemoryBehaviors | None = None
    relay_responses: RelayResponseRuntime | None = None
    devices: DeviceDiscovery | None = None


@dataclass(frozen=True, slots=True)
class ControlRuntime:
    """Control-plane services exposed to request handlers."""

    trace_queries: TraceQueryService
    environments: EnvironmentService
    environment_mounts: RunEnvironmentMountService
    skill_uploads: SkillUploadService
    skill_publication: SkillPublicationService
    skill_catalog: SkillCatalogService
    agents: AgentManagement
    models: ModelService
    model_providers: ModelProviderService
    assets: AssetCatalog
    asset_uploads: AssetUploadService
    hook_subscriptions: HookSubscriptionService
    lifecycle_events: LifecycleEventService
    gateway: GatewayRuntime
    subagent_maintenance: SubagentMaintenance
    web_providers: WebProviderService | None = None
    memory_providers: MemoryProviderService | None = None
    identity: IdentityRuntime | None = None
    configuration: ConfigurationService | None = None
    client_connections: ClientConnectionRuntime | None = None


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    """Worker-owned execution components."""

    external_tools: ExternalToolRuntime
    native_model_factory: NativeModelFactory
    skill_runtime: SkillRuntimePreparer
    environment_maintenance: EnvironmentMaintenanceLoop
    environments: EnvironmentLifecycle
    run_stream: RedisRunStream
    run_display: RunDisplayStore
    execution_loop: WorkerExecutionLoop | None = None
    client_connections: WorkerClientConnections | None = None


@dataclass(slots=True)
class ProcessStatus:
    """Mutable readiness and drain state shared with the HTTP boundary."""

    startup_complete: bool = False
    draining: bool = False


@dataclass(frozen=True, slots=True)
class ProcessRuntime:
    """One explicit runtime for the selected process-role composition."""

    settings: Settings
    status: ProcessStatus
    request_authenticator: RequestAuthenticator | None
    observability: ObservabilityRuntime
    shared: SharedRuntime
    control: ControlRuntime | None
    worker: WorkerRuntime | None
    connectivity: ConnectivityRuntime | None
    bots: BotService | None = None

    def begin_drain(self) -> None:
        """Reject new work before the HTTP server waits for connections to close."""
        first_request = not self.status.draining
        self.status.draining = True
        if self.connectivity is not None and self.connectivity.data is not None:
            if self.connectivity.data.event_connections is not None:
                self.connectivity.data.event_connections.drain()
        if self.worker is not None:
            if self.worker.execution_loop is not None:
                self.worker.execution_loop.begin_drain()
            self.worker.environment_maintenance.drain()
            if self.worker.client_connections is not None:
                self.worker.client_connections.stop_admission()
        if self.control is not None:
            self.control.subagent_maintenance.drain()
            if self.control.client_connections is not None:
                self.control.client_connections.begin_drain()
        if first_request:
            logger.info(
                "service_drain_started",
                extra={"event": "service_drain_started", "role": self.settings.service.role.value},
            )


__all__ = [
    "ControlRuntime",
    "ProcessRuntime",
    "ProcessStatus",
    "SharedRuntime",
    "WorkerRuntime",
]
