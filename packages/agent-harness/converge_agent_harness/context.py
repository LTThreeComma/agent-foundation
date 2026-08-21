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
    from converge_agent_harness.events import HarnessEventEmitter
    from converge_agent_harness.execution import AgentDefinition, ExecutableAgent, SubagentDefinition
    from converge_agent_harness.models import ModelRunBinding
    from converge_agent_harness.plugins import BoundPluginContext
    from converge_agent_harness.tools.deferred import DeferredToolResume


@dataclass(frozen=True, slots=True, init=False)
class BuiltSubagent:
    """One authored child edge and its recursively built executable."""

    _declaration: SubagentDefinition = field(repr=False)
    definition: AgentDefinition[Any]
    executable: ExecutableAgent[Any]

    def __init__(
        self,
        *,
        declaration: SubagentDefinition,
        definition: AgentDefinition[Any],
        executable: ExecutableAgent[Any],
    ) -> None:
        object.__setattr__(self, "_declaration", _copy_subagent_declaration(declaration))
        object.__setattr__(self, "definition", definition)
        object.__setattr__(self, "executable", executable)

    @property
    def declaration(self) -> SubagentDefinition:
        """Return a detached edge value so mutable native limits cannot widen the build."""
        return _copy_subagent_declaration(self._declaration)


def _copy_subagent_declaration(declaration: SubagentDefinition) -> SubagentDefinition:
    from converge_agent_harness.execution import SubagentDefinition

    return SubagentDefinition(
        name=declaration.name,
        description=declaration.description,
        agent=declaration.agent,
        context=declaration.context,
        usage_limits=declaration.usage_limits,
    )


@dataclass(frozen=True, slots=True)
class SubagentCollection(Mapping[str, BuiltSubagent]):
    """Immutable immediate-child collection in authored order."""

    _items: Mapping[str, BuiltSubagent] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_items", MappingProxyType(dict(self._items)))

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __getitem__(self, name: str) -> BuiltSubagent:
        return self._items[name]

    def require(self, name: str) -> BuiltSubagent:
        """Return a named immediate child or raise a clear lookup error."""
        try:
            return self._items[name]
        except KeyError:
            raise KeyError(f"Unknown subagent: {name!r}") from None


EMPTY_SUBAGENTS = SubagentCollection()


@dataclass(frozen=True, slots=True)
class _CapabilityProvenance:
    """Reserved Capability IDs accepted from each trusted composition source."""

    definition_ids: frozenset[str] = frozenset()
    run_ids: frozenset[str] = frozenset()


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
    events: HarnessEventEmitter
    deferred_resume: DeferredToolResume | None
    metadata: Mapping[str, JsonValue]
    _capability_provenance: _CapabilityProvenance = field(default_factory=_CapabilityProvenance, repr=False)
    _managed_tool_ids: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )

    def _record_managed_tool_surface(self, tool_ids: Mapping[str, str]) -> None:
        object.__setattr__(self, "_managed_tool_ids", MappingProxyType(dict(tool_ids)))

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
