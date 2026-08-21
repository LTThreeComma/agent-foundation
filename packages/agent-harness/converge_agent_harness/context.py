"""Per-run trusted bindings and Pydantic AI dependency context."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import JsonValue
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage

from converge_agent_harness.environment import BoundEnvironment, EnvironmentRunBinding, NoopEnvironmentRunBinding
from converge_agent_harness.identity import AgentIdentityRef, AgentInstanceContext
from converge_agent_harness.state import AgentContextState, HarnessState

if TYPE_CHECKING:
    from converge_agent_harness.execution import ExecutableAgent
    from converge_agent_harness.models import ModelRunBinding
    from converge_agent_harness.plugins import BoundPluginContext


@dataclass(frozen=True, slots=True)
class SubagentCollection:
    """Immutable immediate-child collection; empty is the Block 1 zero value."""

    _items: Mapping[str, ExecutableAgent[Any]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_items", MappingProxyType(dict(self._items)))

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def get(self, name: str) -> ExecutableAgent[Any] | None:
        return self._items.get(name)


EMPTY_SUBAGENTS = SubagentCollection()


@dataclass(frozen=True, slots=True)
class RunBindings:
    """Fresh trusted authority and run integrations supplied by the caller."""

    instance: AgentInstanceContext
    environment: EnvironmentRunBinding
    model_binding: ModelRunBinding | None = None
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @classmethod
    def local(
        cls,
        *,
        identity: AgentIdentityRef | None = None,
        environment: EnvironmentRunBinding | None = None,
        model_binding: ModelRunBinding | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> RunBindings:
        """Create fresh bindings for an embedded process-local run."""
        instance_id = str(uuid4())
        return cls(
            instance=AgentInstanceContext(
                identity=identity or AgentIdentityRef(issuer="local", subject="embedded"),
                agent_instance_id=instance_id,
            ),
            environment=environment or NoopEnvironmentRunBinding(),
            model_binding=model_binding,
            capabilities=tuple(capabilities),
            metadata=metadata or {},
        )


@dataclass(frozen=True, slots=True)
class AgentContext:
    """The one dependency object shared across a Pydantic AI run."""

    run_id: str
    instance: AgentInstanceContext
    state: AgentContextState
    environment: BoundEnvironment
    model_binding: ModelRunBinding | None
    plugins: BoundPluginContext
    subagents: SubagentCollection
    metadata: Mapping[str, JsonValue]

    @property
    def identity(self) -> AgentIdentityRef:
        """Return the identity carried by the trusted instance binding."""
        return self.instance.identity

    async def export_state(self, message_history: Sequence[ModelMessage]) -> HarnessState:
        """Export a detached continuation envelope without persistence side effects."""
        return HarnessState(
            message_history=tuple(message_history),
            agent_context_state=await self.state.snapshot(),
        )
