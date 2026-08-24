"""Per-run trusted bindings and Pydantic AI dependency context."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import JsonValue
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage

from converge_agent_harness.identity import AgentIdentityRef, AgentInstanceContext
from converge_agent_harness.state import AgentContextState, HarnessState

if TYPE_CHECKING:
    from converge_agent_harness.environment.providers import BoundEnvironment, EnvironmentRunBinding
    from converge_agent_harness.events import HarnessEventEmitter
    from converge_agent_harness.execution import AgentDefinition, ExecutableAgent, SubagentDefinition
    from converge_agent_harness.models import ModelRunBinding
    from converge_agent_harness.plugins import BoundPluginContext
    from converge_agent_harness.tools.deferred import DeferredToolResume
    from converge_agent_harness.usage import ProviderUsage, ProviderUsageRecord, UsageRecord, _RunUsageLedger


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
        from converge_agent_harness.environment.coordinator import NoopEnvironmentRunBinding

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
    _usage_attribution: _RunUsageLedger = field(repr=False)
    deferred_resume: DeferredToolResume | None
    metadata: Mapping[str, JsonValue]
    _capability_provenance: _CapabilityProvenance = field(default_factory=_CapabilityProvenance, repr=False)
    _managed_tool_ids: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    _run_capability_instances: dict[str, AbstractCapability[AgentContext]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _run_cleanup_callbacks: dict[str, Callable[[], Awaitable[None]]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _tool_result_spill_store: _ToolResultSpillStore | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def _record_managed_tool_surface(self, tool_ids: Mapping[str, str]) -> None:
        object.__setattr__(self, "_managed_tool_ids", MappingProxyType(dict(tool_ids)))

    def _run_capability(self, capability_id: str) -> AbstractCapability[AgentContext] | None:
        """Return a logical-run Capability replacement cached across inner Agent attempts."""
        return self._run_capability_instances.get(capability_id)

    def _record_run_capability(
        self,
        capability_id: str,
        capability: AbstractCapability[AgentContext],
    ) -> None:
        """Retain one fresh Capability replacement for this logical Harness run."""
        existing = self._run_capability_instances.setdefault(capability_id, capability)
        if existing is not capability:
            raise RuntimeError(f"Run Capability {capability_id!r} is already bound")

    def _register_run_cleanup(
        self,
        owner_id: str,
        cleanup: Callable[[], Awaitable[None]],
    ) -> None:
        """Register one owner-bound live collaborator for logical-run teardown."""
        if owner_id in self._run_cleanup_callbacks:
            raise RuntimeError(f"Run cleanup owner {owner_id!r} is already registered")
        self._run_cleanup_callbacks[owner_id] = cleanup

    async def _close_run_cleanups(self) -> None:
        """Close owner-bound collaborators in reverse registration order."""
        callbacks = tuple(reversed(tuple(self._run_cleanup_callbacks.values())))
        self._run_cleanup_callbacks.clear()
        first_error: BaseException | None = None
        for cleanup in callbacks:
            try:
                await cleanup()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    async def _spill_tool_result(self, data: bytes, *, suffix: str) -> str | None:
        """Write one bounded managed result through the current Environment when possible."""
        store = self._tool_result_spill_store
        if store is None:
            store = _ToolResultSpillStore(self)
            object.__setattr__(self, "_tool_result_spill_store", store)
            self._register_run_cleanup("converge.tool-result-spills", store.close)
        return await store.write(data, suffix=suffix)

    @property
    def identity(self) -> AgentIdentityRef:
        """Return the identity carried by the trusted instance binding."""
        return self.instance.identity

    @property
    def usage_records(self) -> tuple[UsageRecord, ...]:
        """Return a detached snapshot of mixed run-local usage attribution."""
        return self._usage_attribution._snapshot()

    async def record_provider_usage(
        self,
        usage: ProviderUsage,
        *,
        source: str,
        tool_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> ProviderUsageRecord:
        """Record one stable non-model usage receipt for the next reporting boundary."""
        return await self._usage_attribution._record_provider(
            usage,
            source=source,
            tool_id=tool_id,
            tool_call_id=tool_call_id,
        )

    async def export_state(self, message_history: Sequence[ModelMessage]) -> HarnessState:
        """Export a detached continuation envelope without persistence side effects."""
        return HarnessState(
            message_history=tuple(message_history),
            agent_context_state=await self.state.snapshot(),
            environment_state=await self.environment.export_state(),
        )


class _ToolResultSpillStore:
    """One best-effort run-private spill directory over the logical file facade."""

    def __init__(self, context: AgentContext) -> None:
        run_digest = hashlib.sha256(context.run_id.encode("utf-8")).hexdigest()[:12]
        self._files = context.environment.files
        self._directory = f"/workspace/.converge/tmp/tool-results/run-{run_digest}"
        self._next_sequence = 1
        self._ready = False
        self._closed = False
        self._lock = asyncio.Lock()

    async def write(self, data: bytes, *, suffix: str) -> str | None:
        if suffix not in {".json", ".txt"}:
            raise ValueError("tool result spill suffix is invalid")
        async with self._lock:
            if self._closed:
                return None
            try:
                if not self._ready:
                    await self._files.mkdir(self._directory, parents=True, exist_ok=True)
                    self._ready = True
                path = f"{self._directory}/tool-result-{self._next_sequence}{suffix}"
                self._next_sequence += 1
                await self._files.write_bytes_stream(path, _byte_chunks(data), mode="create")
            except Exception:
                return None
            return path

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            if not self._ready:
                return
            try:
                await self._files.remove(self._directory, recursive=True)
            except Exception:
                # Spill cleanup is finite best effort. It must not turn a completed
                # tool side effect or run into a retry-shaped failure.
                return


async def _byte_chunks(data: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(data), 64 * 1024):
        yield data[offset : offset + 64 * 1024]
