"""Process-local provider and aggregate Environment contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .models import (
    EnvironmentAvailability,
    EnvironmentBindingObservation,
    EnvironmentBindingState,
    EnvironmentOperationFamily,
    EnvironmentPath,
    EnvironmentReadinessRequirement,
    EnvironmentState,
    EnvironmentStateLimits,
    EnvironmentTopology,
    EnvironmentTopologyChange,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
)

if TYPE_CHECKING:
    from converge_agent_harness.identity import AgentInstanceContext


@dataclass(frozen=True, slots=True)
class EnvironmentProviderOperations:
    """Provider-local semantic operation facets captured at entry."""

    files: Any | None = None
    shell: Any | None = None
    processes: Any | None = None
    ports: Any | None = None
    outputs: Any | None = None


class BoundEnvironmentProvider(Protocol):
    """One entered provider resource with a stable observed generation."""

    @property
    def provider_type(self) -> str: ...

    @property
    def environment_id(self) -> str: ...

    @property
    def descriptor(self) -> Any: ...

    @property
    def availability(self) -> EnvironmentAvailability: ...

    @property
    def operations(self) -> EnvironmentProviderOperations: ...

    async def ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None: ...

    async def export_state(self, *, max_bytes: int) -> EnvironmentBindingState | None: ...

    async def restore_state(self, state: EnvironmentBindingState) -> None: ...


class EnvironmentProviderBinding(ABC):
    """Single-use provider candidate materialized by a trusted Host."""

    def _claim_transfer(self) -> bool:
        """Atomically mark this candidate as transferred without a central identity table."""
        marker_name = "_EnvironmentProviderBinding__transferred"
        try:
            object.__getattribute__(self, marker_name)
        except AttributeError:
            object.__setattr__(self, marker_name, True)
            return True
        return False

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Return the stable provider compatibility discriminator."""

    @property
    @abstractmethod
    def environment_id(self) -> str:
        """Return the provider's logical resource identity."""

    @abstractmethod
    def bind(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
        binding_id: str,
        binding_revision: int,
    ) -> AbstractAsyncContextManager[BoundEnvironmentProvider]:
        """Enter this candidate exactly once."""

    @abstractmethod
    async def discard(self) -> None:
        """Idempotently dispose a candidate that did not enter successfully."""


class EnvironmentTopologyObserver(Protocol):
    @property
    def initial_topology_version(self) -> int: ...

    async def read(
        self,
        *,
        after_version: int,
        wait: bool = False,
    ) -> tuple[EnvironmentTopologyChange, ...]: ...


class EnvironmentTopologyController(Protocol):
    async def wait_until_active(self) -> None: ...

    async def apply(self, request: EnvironmentTopologyRequest) -> EnvironmentTopologyChange: ...

    def begin_close(self) -> None:
        """Install the logical-run terminal fence without awaiting provider cleanup."""


class BoundEnvironment(ABC):
    """Stable provider-neutral facade available for one logical Harness run."""

    @property
    @abstractmethod
    def topology(self) -> EnvironmentTopology: ...

    @property
    @abstractmethod
    def restored_state_topology_version(self) -> int | None: ...

    @property
    @abstractmethod
    def topology_observer(self) -> EnvironmentTopologyObserver: ...

    @property
    @abstractmethod
    def files(self) -> Any: ...

    @property
    @abstractmethod
    def shell(self) -> Any: ...

    @property
    @abstractmethod
    def processes(self) -> Any: ...

    @property
    @abstractmethod
    def ports(self) -> Any: ...

    @property
    @abstractmethod
    def outputs(self) -> Any: ...

    @abstractmethod
    def resolve_path(self, path: str, *, alias: str | None = None) -> EnvironmentPath:
        """Resolve a model-facing selector into one captured internal binding path."""

    @abstractmethod
    async def activate(self) -> None:
        """Publish controller activation after initial state restore completes."""

    @abstractmethod
    async def describe(self, binding_id: str) -> EnvironmentBindingObservation: ...

    @abstractmethod
    async def ensure_ready(self, requirement: EnvironmentReadinessRequirement) -> None: ...

    @abstractmethod
    async def export_state(self) -> EnvironmentState: ...

    @abstractmethod
    async def restore_state(self, state: EnvironmentState) -> None: ...


class EnvironmentRunBinding(ABC):
    """Single-use aggregate paired with one Host-retained topology controller."""

    @property
    @abstractmethod
    def controller(self) -> EnvironmentTopologyController: ...

    @property
    @abstractmethod
    def topology_limits(self) -> EnvironmentTopologyLimits: ...

    @property
    @abstractmethod
    def state_limits(self) -> EnvironmentStateLimits: ...

    @abstractmethod
    def bind(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
    ) -> AbstractAsyncContextManager[BoundEnvironment]:
        """Bind and enter the aggregate for exactly one run."""


class RawEnvironmentReader(Protocol):
    def __aiter__(self) -> AsyncIterator[bytes]: ...
