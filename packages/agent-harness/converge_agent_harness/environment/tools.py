"""Optional model-facing Environment Capability and compact run-local references."""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import BaseToolReturnPart, ModelRequest, RetryPromptPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

from converge_agent_harness._json import dump_json_bytes
from converge_agent_harness.context import AgentContext
from converge_agent_harness.errors import DefinitionError
from converge_agent_harness.tools.metadata import (
    CanonicalResource,
    HarnessTool,
    HarnessToolMetadata,
    ToolEffect,
    ToolOutputPolicy,
    ToolResourceResolver,
)

from .commands import (
    ArgvCommand,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    PortObservation,
    PortTarget,
    ProcessInfo,
    ProcessStatus,
    ShellCommand,
)
from .files import (
    FileMetadata,
    FileQueryCursor,
    FileQueryRequest,
    FileRevision,
    FileTextCursor,
    FileTextSearchRequest,
    FileWriteMode,
)
from .models import ENVIRONMENT_ACTION_DISPATCH, EnvironmentAction, EnvironmentError
from .retention import BoundOutputReference, EnvironmentOutputCapture, EnvironmentOutputPolicy
from .virtual_files import VirtualFileOperator, _FileResultProvenance

ENVIRONMENT_TOOLS_CAPABILITY_ID = "converge.environment-tools"
_ENVIRONMENT_TOOLSET_ID = "converge-environment-tools"
_MAX_MODEL_TEXT_BYTES = 256 * 1024
_MAX_MODEL_RESULTS = 1_000
_MAX_MODEL_OUTPUT_BYTES = 1024 * 1024
_REFERENCE_PATTERN = re.compile(r"^(revision|cursor|process|output)-([1-9][0-9]*)$")

_PositiveTextBytes = Annotated[int, Field(gt=0, le=_MAX_MODEL_TEXT_BYTES)]
_PositiveOutputBytes = Annotated[int, Field(gt=0, le=_MAX_MODEL_OUTPUT_BYTES)]
_PositiveResults = Annotated[int, Field(gt=0, le=_MAX_MODEL_RESULTS)]
_NonNegativeOffset = Annotated[int, Field(ge=0)]
_PositiveTimeout = Annotated[float, Field(gt=0, allow_inf_nan=False)]
_NonNegativeTimeout = Annotated[float, Field(ge=0, allow_inf_nan=False)]
_CursorT = TypeVar("_CursorT", FileTextCursor, FileQueryCursor)

_STABLE_INSTRUCTIONS = """Environment tools operate on the live run Environment.
Use relative paths or /workspace for the current default binding. Use /environment/{alias} for another binding.
Aliases are ordinary strings because topology can change without changing tool schemas.
Values named revision-N, cursor-N, process-N, and output-N are opaque references valid only in this logical run.
Reuse them only with the same path or request shape that produced them. Never invent, alter, or persist a reference.
Environment tool results are bounded semantic JSON. A result with ok=false is a terminal operation result; adapt the
request instead of repeating it blindly. Shell and process wall-time limits are owned by the Environment provider.
The Harness does not impose an additional Agent-wide tool timeout."""


class EnvironmentToolsConfiguration(BaseModel):
    """Frozen definition-selected model projection and finite run-local limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    files: bool = True
    shell: bool = True
    processes: bool = True
    ports: bool = False
    max_topology_bindings: int = Field(gt=0, le=1024)
    max_topology_bytes: int = Field(ge=256, le=1024 * 1024)
    max_reference_entries: int = Field(gt=0, le=100_000)


@dataclass(frozen=True, slots=True)
class _BindingFence:
    binding_id: str
    binding_revision: int
    observed_generation: str


@dataclass(frozen=True, slots=True)
class _AuthorizationFence:
    topology_version: int
    bindings: tuple[_BindingFence, ...]
    unresolved: bool = False


@dataclass(frozen=True, slots=True)
class _BoundFileReference:
    binding: _BindingFence
    provider_path: str
    request_scope: str | None
    value: FileRevision | FileTextCursor | FileQueryCursor


@dataclass(slots=True)
class _ReferenceEntry:
    kind: Literal["revision", "cursor", "process", "output"]
    reference: str
    value: object
    scope: str | None
    expires_at: datetime | None
    active: bool = True


class _CompactReferenceTable:
    """One bounded monotonic table shared by every tool in a logical run."""

    def __init__(self, max_entries: int) -> None:
        self._max_entries = max_entries
        self._next = {"revision": 1, "cursor": 1, "process": 1, "output": 1}
        self._entries: dict[str, _ReferenceEntry] = {}
        self._keys: dict[tuple[str, str | None, object], str] = {}
        self._lock = threading.Lock()

    def register(
        self,
        kind: Literal["revision", "cursor", "process", "output"],
        value: object,
        *,
        scope: str | None = None,
        expires_at: datetime | None = None,
    ) -> str:
        key = (kind, scope, value)
        with self._lock:
            existing_ref = self._keys.get(key)
            if existing_ref is not None:
                existing = self._entries[existing_ref]
                self._expire(existing)
                if not existing.active:
                    raise EnvironmentError(
                        "Environment compact reference is no longer available.",
                        code="environment_reference_stale",
                    )
                if expires_at is not None and (existing.expires_at is None or expires_at < existing.expires_at):
                    existing.expires_at = expires_at
                return existing.reference
            if len(self._entries) >= self._max_entries:
                raise EnvironmentError(
                    "Environment compact reference capacity is exhausted.",
                    code="environment_reference_exhausted",
                )
            sequence = self._next[kind]
            self._next[kind] = sequence + 1
            reference = f"{kind}-{sequence}"
            entry = _ReferenceEntry(
                kind=kind,
                reference=reference,
                value=value,
                scope=scope,
                expires_at=expires_at,
            )
            self._entries[reference] = entry
            self._keys[key] = reference
            return reference

    def resolve(
        self,
        reference: str,
        kind: Literal["revision", "cursor", "process", "output"],
        *,
        scope: str | None = None,
    ) -> object:
        match = _REFERENCE_PATTERN.fullmatch(reference)
        if match is None or match.group(1) != kind:
            raise EnvironmentError(
                f"Expected a {kind} compact reference.",
                code="environment_reference_invalid",
            )
        with self._lock:
            entry = self._entries.get(reference)
            if entry is None or entry.kind != kind or entry.scope != scope:
                raise EnvironmentError(
                    "Environment compact reference is unknown or has the wrong scope.",
                    code="environment_reference_invalid",
                )
            self._expire(entry)
            if not entry.active:
                raise EnvironmentError(
                    "Environment compact reference is no longer available.",
                    code="environment_reference_stale",
                )
            return entry.value

    def resolve_value(
        self,
        reference: str,
        kind: Literal["revision", "cursor", "process", "output"],
    ) -> object:
        """Resolve an entry whose package-private value carries its own exact scope."""
        match = _REFERENCE_PATTERN.fullmatch(reference)
        if match is None or match.group(1) != kind:
            raise EnvironmentError(
                f"Expected a {kind} compact reference.",
                code="environment_reference_invalid",
            )
        with self._lock:
            entry = self._entries.get(reference)
            if entry is None or entry.kind != kind:
                raise EnvironmentError(
                    "Environment compact reference is unknown.",
                    code="environment_reference_invalid",
                )
            self._expire(entry)
            if not entry.active:
                raise EnvironmentError(
                    "Environment compact reference is no longer available.",
                    code="environment_reference_stale",
                )
            return entry.value

    def tombstone(self, reference: str, kind: Literal["revision", "cursor", "process", "output"]) -> None:
        with self._lock:
            entry = self._entries.get(reference)
            if entry is None or entry.kind != kind:
                raise EnvironmentError(
                    "Environment compact reference is unknown.",
                    code="environment_reference_invalid",
                )
            entry.active = False

    def tombstone_value(self, kind: Literal["revision", "cursor", "process", "output"], value: object) -> None:
        with self._lock:
            for entry in self._entries.values():
                if entry.kind == kind and entry.value == value:
                    entry.active = False

    @staticmethod
    def _expire(entry: _ReferenceEntry) -> None:
        if entry.expires_at is not None and datetime.now(UTC) >= entry.expires_at:
            entry.active = False


@dataclass(frozen=True, slots=True)
class _EnvironmentRetainedToolResult:
    """Package-private evidence that output retention preceded managed-result overflow."""

    value: JsonValue
    reference: BoundOutputReference
    expires_at: datetime | None = None


class _EnvironmentResultProjector:
    """Internal cross-Capability seam consumed by managed invocation."""

    def project_retained_result(self, result: _EnvironmentRetainedToolResult) -> tuple[JsonValue, str]:
        raise NotImplementedError


@dataclass(init=False)
class EnvironmentToolsCapability(AbstractCapability[AgentContext]):
    """Definition-selected Environment model projection; providers never inject it."""

    id = ENVIRONMENT_TOOLS_CAPABILITY_ID

    def __init__(self, configuration: EnvironmentToolsConfiguration) -> None:
        if not isinstance(configuration, EnvironmentToolsConfiguration):
            configuration = EnvironmentToolsConfiguration.model_validate(configuration, strict=True)
        self.configuration = configuration.model_copy(deep=True)

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(ENVIRONMENT_TOOLS_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _EnvironmentToolsRunCapability):
                raise DefinitionError(
                    "Environment tools Capability has an incompatible logical-run replacement.",
                    code="capability_type_mismatch",
                )
            return existing
        replacement = _EnvironmentToolsRunCapability(
            self.configuration,
            run_id=ctx.deps.run_id,
            environment=ctx.deps.environment,
        )
        ctx.deps._record_run_capability(ENVIRONMENT_TOOLS_CAPABILITY_ID, replacement)
        return replacement

    def get_instructions(self) -> str:
        surfaces = [
            name
            for name, enabled in (
                ("files", self.configuration.files),
                ("shell", self.configuration.shell),
                ("processes", self.configuration.processes),
                ("ports", self.configuration.ports),
            )
            if enabled
        ]
        return f"{_STABLE_INSTRUCTIONS}\nEnabled Environment tool surfaces: {', '.join(surfaces)}."


@dataclass(init=False)
class _EnvironmentToolsRunCapability(EnvironmentToolsCapability, _EnvironmentResultProjector):
    def __init__(
        self,
        configuration: EnvironmentToolsConfiguration,
        *,
        run_id: str,
        environment: Any,
    ) -> None:
        super().__init__(configuration)
        self._run_id = run_id
        self._environment = environment
        self._references = _CompactReferenceTable(configuration.max_reference_entries)
        self._last_projected_version: int | None = None
        self._pending_version: int | None = None
        self._notice_pending = False
        self._active_context: RunContext[AgentContext] | None = None
        self._observer_task: asyncio.Task[None] | None = None
        self._authorization_fence: ContextVar[_AuthorizationFence | None] = ContextVar(
            f"environment_authorization_fence_{run_id}",
            default=None,
        )
        self._fence_builder: ContextVar[list[_BindingFence] | None] = ContextVar(
            f"environment_fence_builder_{run_id}",
            default=None,
        )

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps.run_id != self._run_id:
            raise DefinitionError(
                "Environment tools run replacement cannot cross logical runs.",
                code="capability_scope_invalid",
            )
        return self

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        tools: list[HarnessTool] = []
        if self.configuration.files:
            tools.extend(
                (
                    self._tool(self.environment_read_text, "environment.read_text", {"read"}, "read_only"),
                    self._tool(self.environment_write_text, "environment.write_text", {"write"}, "none"),
                    self._tool(self.environment_patch_text, "environment.patch_text", {"write"}, "none"),
                    self._tool(self.environment_stat, "environment.stat", {"read"}, "read_only"),
                    self._tool(self.environment_list, "environment.list", {"read"}, "read_only"),
                    self._tool(self.environment_query, "environment.query", {"read"}, "read_only"),
                    self._tool(self.environment_search_text, "environment.search_text", {"read"}, "read_only"),
                    self._tool(self.environment_mkdir, "environment.mkdir", {"write"}, "none"),
                    self._tool(self.environment_move, "environment.move", {"write", "delete"}, "none"),
                    self._tool(self.environment_remove, "environment.remove", {"delete"}, "none"),
                    self._tool(self.environment_copy, "environment.copy", {"read", "write"}, "none"),
                )
            )
        arbitrary_command_effects: set[ToolEffect] = {
            "read",
            "write",
            "delete",
            "execute",
            "external_communication",
        }
        if self.configuration.shell:
            tools.append(
                self._tool(
                    self.environment_shell_exec,
                    "environment.shell_exec",
                    arbitrary_command_effects,
                    "none",
                )
            )
        if self.configuration.processes:
            tools.extend(
                (
                    self._tool(
                        self.environment_process_start,
                        "environment.process_start",
                        arbitrary_command_effects,
                        "none",
                    ),
                    self._tool(
                        self.environment_process_inspect,
                        "environment.process_inspect",
                        {"read"},
                        "read_only",
                    ),
                    self._tool(
                        self.environment_process_read_output,
                        "environment.process_read_output",
                        {"read"},
                        "read_only",
                    ),
                    self._tool(
                        self.environment_process_write_stdin,
                        "environment.process_write_stdin",
                        {"write"},
                        "none",
                    ),
                    self._tool(
                        self.environment_process_close_stdin,
                        "environment.process_close_stdin",
                        {"write"},
                        "none",
                    ),
                    self._tool(self.environment_process_signal, "environment.process_signal", {"execute"}, "none"),
                    self._tool(self.environment_process_wait, "environment.process_wait", {"read"}, "read_only"),
                    self._tool(self.environment_process_kill, "environment.process_kill", {"execute"}, "none"),
                    self._tool(self.environment_process_release, "environment.process_release", {"delete"}, "none"),
                )
            )
        if self.configuration.shell or self.configuration.processes:
            tools.extend(
                (
                    self._tool(self.environment_output_read, "environment.output_read", {"read"}, "read_only"),
                    self._tool(self.environment_output_release, "environment.output_release", {"delete"}, "none"),
                )
            )
        if self.configuration.ports:
            tools.extend(
                (
                    self._tool(self.environment_port_inspect, "environment.port_inspect", {"read"}, "read_only"),
                    self._tool(self.environment_port_wait, "environment.port_wait", {"read"}, "read_only"),
                )
            )
        return FunctionToolset(tools=tools, id=_ENVIRONMENT_TOOLSET_ID)

    def _tool(
        self,
        function: Callable[..., Any],
        tool_id: str,
        effects: set[ToolEffect],
        idempotency: Literal["none", "read_only"],
    ) -> HarnessTool:
        return HarnessTool(
            function,
            harness_metadata=HarnessToolMetadata(
                tool_id=tool_id,
                effects=frozenset(effects),
                credential_audiences=(),
                idempotency=idempotency,
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=256 * 1024,
                    max_output_bytes=4 * 1024 * 1024,
                    overflow="truncate",
                    redact=True,
                ),
                resource_resolver=self._resource_resolver(tool_id),
            ),
        )

    def _resource_resolver(self, tool_id: str) -> ToolResourceResolver:
        async def resolve(
            arguments: Mapping[str, object],
            *,
            context: AgentContext,
        ) -> tuple[CanonicalResource, ...]:
            topology_version = context.environment.topology.topology_version
            fences: list[_BindingFence] = []
            token = self._fence_builder.set(fences)
            try:
                resources = self._resolve_resources(tool_id, arguments, context)
            except EnvironmentError as exc:
                if exc.code not in {"environment_selection_invalid", "environment_unavailable"}:
                    raise
                self._authorization_fence.set(
                    _AuthorizationFence(topology_version=topology_version, bindings=(), unresolved=True)
                )
                return ()
            finally:
                self._fence_builder.reset(token)
            self._authorization_fence.set(
                _AuthorizationFence(
                    topology_version=topology_version,
                    bindings=tuple(dict.fromkeys(fences)),
                )
            )
            return resources

        return resolve

    def _resolve_resources(
        self,
        tool_id: str,
        arguments: Mapping[str, object],
        context: AgentContext,
    ) -> tuple[CanonicalResource, ...]:
        if tool_id in {
            "environment.read_text",
            "environment.write_text",
            "environment.patch_text",
            "environment.stat",
            "environment.list",
            "environment.mkdir",
            "environment.remove",
        }:
            return (self._path_resource(context, _string_argument(arguments, "path")),)
        if tool_id in {"environment.query", "environment.search_text"}:
            return (self._path_resource(context, _string_argument(arguments, "root")),)
        if tool_id in {"environment.move", "environment.copy"}:
            return (
                self._path_resource(context, _string_argument(arguments, "source")),
                self._path_resource(context, _string_argument(arguments, "destination")),
            )
        if tool_id in {"environment.shell_exec", "environment.process_start"}:
            alias = _optional_string_argument(arguments, "alias")
            cwd = _optional_string_argument(arguments, "cwd")
            if cwd is not None:
                return (self._path_resource(context, cwd, alias=alias),)
            return (self._binding_resource(context, alias),)
        if tool_id.startswith("environment.process_"):
            handle = self._process(_string_argument(arguments, "process"))
            self._record_fence(handle.binding_id, handle.binding_revision, handle.observed_generation)
            return (
                CanonicalResource(
                    namespace="environment",
                    kind="process",
                    identifier=f"{handle.binding_id}:{handle.binding_revision}:{handle.observed_generation}",
                ),
            )
        if tool_id.startswith("environment.output_"):
            output = self._output(_string_argument(arguments, "output"))
            self._record_fence(output.binding_id, output.binding_revision, output.observed_generation)
            return (
                CanonicalResource(
                    namespace="environment",
                    kind="output",
                    identifier=f"{output.binding_id}:{output.binding_revision}:{output.observed_generation}",
                ),
            )
        if tool_id.startswith("environment.port_"):
            return (self._binding_resource(context, _optional_string_argument(arguments, "alias")),)
        return ()

    def _path_resource(
        self,
        context: AgentContext,
        path: str,
        *,
        alias: str | None = None,
    ) -> CanonicalResource:
        selected = context.environment.resolve_path(path, alias=alias)
        binding = next(item for item in context.environment.topology.bindings if item.binding_id == selected.binding_id)
        self._record_fence(binding.binding_id, binding.binding_revision, binding.descriptor.generation)
        return CanonicalResource(
            namespace="environment",
            kind="file",
            identifier=(
                f"{selected.binding_id}:{selected.binding_revision}:{binding.descriptor.generation}:{selected.path}"
            ),
        )

    def _binding_resource(self, context: AgentContext, alias: str | None) -> CanonicalResource:
        topology = context.environment.topology
        if alias is None:
            binding = next(
                (item for item in topology.bindings if item.binding_id == topology.default_binding_id),
                None,
            )
        else:
            binding = next((item for item in topology.bindings if item.alias == alias), None)
        if binding is None:
            raise EnvironmentError(
                "The selected Environment binding is unavailable.",
                code="environment_selection_invalid",
            )
        self._record_fence(binding.binding_id, binding.binding_revision, binding.descriptor.generation)
        return CanonicalResource(
            namespace="environment",
            kind="binding",
            identifier=f"{binding.binding_id}:{binding.binding_revision}:{binding.descriptor.generation}",
        )

    def _record_fence(self, binding_id: str, binding_revision: int, generation: str) -> None:
        builder = self._fence_builder.get()
        if builder is not None:
            builder.append(_BindingFence(binding_id, binding_revision, generation))

    def _assert_authorized_fence(self) -> None:
        fence = self._authorization_fence.get()
        if fence is None:
            return
        topology = self._environment.topology
        if fence.unresolved:
            if topology.topology_version != fence.topology_version:
                raise EnvironmentError(
                    "Environment topology changed after managed resource authorization.",
                    code="environment_stale_binding",
                )
            return
        current = {item.binding_id: item for item in topology.bindings}
        for expected in fence.bindings:
            binding = current.get(expected.binding_id)
            if (
                binding is None
                or binding.binding_revision != expected.binding_revision
                or binding.descriptor.generation != expected.observed_generation
            ):
                raise EnvironmentError(
                    "Environment binding changed after managed resource authorization.",
                    code="environment_stale_binding",
                )

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Any) -> Any:
        self._active_context = ctx
        self._ensure_observer(ctx)
        try:
            return await handler()
        finally:
            if self._active_context is ctx:
                self._active_context = None

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if not _has_ordinary_user_boundary(ctx, request_context.messages):
            return request_context
        topology = ctx.deps.environment.topology
        if self._last_projected_version == topology.topology_version and self._pending_version is None:
            return request_context
        rendered = await self._render_topology(ctx.deps)
        messages = list(request_context.messages)
        final = cast(ModelRequest, messages[-1])
        messages[-1] = replace(final, parts=(*final.parts, UserPromptPart(rendered)))
        self._last_projected_version = topology.topology_version
        if self._pending_version is not None and self._pending_version <= topology.topology_version:
            self._pending_version = None
        self._notice_pending = False
        updated = copy(request_context)
        updated.messages = messages
        return updated

    def _ensure_observer(self, ctx: RunContext[AgentContext]) -> None:
        if self._observer_task is not None:
            return
        self._observer_task = asyncio.create_task(self._observe_topology(ctx.deps))
        self._observer_task.add_done_callback(_consume_task_result)

    async def _observe_topology(self, context: AgentContext) -> None:
        observer = context.environment.topology_observer
        cursor = observer.initial_topology_version
        while True:
            try:
                changes = await observer.read(after_version=cursor, wait=True)
            except EnvironmentError as exc:
                if exc.code == "environment_closed":
                    return
                raise
            if not changes:
                return
            cursor = changes[-1].current_version
            self._pending_version = cursor
            active = self._active_context
            if active is not None and not self._notice_pending:
                self._notice_pending = True
                active.enqueue(
                    "The Environment topology changed. A fresh bounded topology snapshot is attached to this request.",
                    priority="asap",
                )

    async def _render_topology(self, context: AgentContext) -> str:
        topology = context.environment.topology
        selected = topology.bindings[: self.configuration.max_topology_bindings]
        default = next((item for item in topology.bindings if item.binding_id == topology.default_binding_id), None)
        bindings: list[dict[str, JsonValue]] = []
        for binding in selected:
            availability = "unavailable"
            ready: list[str] = []
            reason: str | None = None
            try:
                observation = await context.environment.describe(binding.binding_id)
                availability = observation.availability.status
                ready = sorted(observation.availability.ready_families)
                reason = observation.availability.reason_code
            except EnvironmentError:
                pass
            effective_families = sorted(
                {
                    ENVIRONMENT_ACTION_DISPATCH[action].family
                    for action in binding.permission_ceiling.operations
                    if ENVIRONMENT_ACTION_DISPATCH[action].family != "state"
                }
            )
            root = (
                "/workspace" if binding.binding_id == topology.default_binding_id else f"/environment/{binding.alias}"
            )
            projected: dict[str, JsonValue] = {
                "alias": binding.alias,
                "root": root,
                "operations": cast(JsonValue, effective_families),
                "availability": availability,
                "ready": cast(JsonValue, ready),
                "read_only": not any(
                    action.value.startswith(("environment.file.write", "environment.file.patch"))
                    or action.value
                    in {
                        "environment.file.mkdir",
                        "environment.file.move",
                        "environment.file.remove",
                        "environment.file.copy_destination",
                    }
                    for action in binding.permission_ceiling.operations
                ),
            }
            if reason is not None:
                projected["reason"] = reason
            bindings.append(projected)

        payload: dict[str, JsonValue] = {
            "topology_version": topology.topology_version,
            "restored_state_topology_version": context.environment.restored_state_topology_version,
            "default_alias": default.alias if default is not None else None,
            "bindings": cast(JsonValue, bindings),
            "truncated": len(selected) < len(topology.bindings),
        }
        prefix = "Current Environment topology (trusted dynamic context):\n"
        rendered = _bounded_topology_json(
            payload,
            self.configuration.max_topology_bytes - len(prefix.encode("utf-8")),
        )
        return f"{prefix}{rendered}"

    def project_retained_result(self, result: _EnvironmentRetainedToolResult) -> tuple[JsonValue, str]:
        binding = next(
            (
                candidate
                for candidate in self._environment.topology.bindings
                if candidate.binding_id == result.reference.binding_id
            ),
            None,
        )
        if (
            binding is None
            or binding.binding_revision != result.reference.binding_revision
            or binding.descriptor.generation != result.reference.observed_generation
            or EnvironmentAction.OUTPUT_READ not in binding.permission_ceiling.operations
        ):
            raise EnvironmentError(
                "Managed retained output does not select one compatible live Environment binding.",
                code="environment_reference_invalid",
            )
        reference = self._references.register(
            "output",
            result.reference,
            expires_at=result.expires_at,
        )
        return result.value, reference

    async def environment_read_text(
        self,
        ctx: RunContext[AgentContext],
        path: str,
        *,
        cursor: str | None = None,
        start_line: Annotated[int | None, Field(ge=1)] = None,
        max_lines: _PositiveResults | None = 200,
        max_bytes: _PositiveTextBytes = 64 * 1024,
        expected_revision: str | None = None,
    ) -> dict[str, JsonValue]:
        shape = _request_scope(
            "read_text",
            {
                "path": self._file_reference_scope(path),
                "start_line": start_line,
                "max_lines": max_lines,
                "max_bytes": max_bytes,
            },
        )
        return await self._execute_file(
            lambda: ctx.deps.environment.files.read_text(
                path,
                cursor=self._cursor(cursor, FileTextCursor, shape, path),
                start_line=start_line,
                max_lines=max_lines,
                max_bytes=max_bytes,
                expected_revision=self._revision(expected_revision, path),
            ),
            lambda page, provenance: {
                "path": page.path,
                "revision": self._revision_reference(page.path, page.revision, provenance),
                "text": page.text,
                "start": {"line": page.start.line, "byte_column": page.start.byte_column},
                "end": {"line": page.end.line, "byte_column": page.end.byte_column},
                "next_cursor": self._cursor_reference(page.next_cursor, shape, page.path, provenance),
                "content_complete": page.content_complete,
                "truncated": page.truncated,
            },
        )

    async def environment_write_text(
        self,
        ctx: RunContext[AgentContext],
        path: str,
        text: str,
        *,
        mode: FileWriteMode = "upsert",
        expected_revision: str | None = None,
    ) -> dict[str, JsonValue]:
        return await self._execute_file(
            lambda: ctx.deps.environment.files.write_text(
                path,
                text,
                mode=mode,
                expected_revision=self._revision(expected_revision, path),
            ),
            lambda result, provenance: {
                "path": result.path,
                "revision": self._revision_reference(result.path, result.revision, provenance),
                "bytes_written": result.bytes_written,
            },
        )

    async def environment_patch_text(
        self,
        ctx: RunContext[AgentContext],
        path: str,
        patch: str,
        *,
        expected_revision: str,
    ) -> dict[str, JsonValue]:
        return await self._execute_file(
            lambda: ctx.deps.environment.files.patch_text(
                path,
                patch,
                expected_revision=cast(FileRevision, self._revision(expected_revision, path)),
            ),
            lambda result, provenance: {
                "path": result.path,
                "revision": self._revision_reference(result.path, result.revision, provenance),
                "hunks_applied": result.hunks_applied,
            },
        )

    async def environment_stat(
        self,
        ctx: RunContext[AgentContext],
        path: str,
    ) -> dict[str, JsonValue]:
        return await self._execute_file(
            lambda: ctx.deps.environment.files.stat(path),
            lambda metadata, provenance: self._project_metadata(metadata, provenance),
        )

    async def environment_list(
        self,
        ctx: RunContext[AgentContext],
        path: str,
        *,
        cursor: str | None = None,
        max_results: _PositiveResults = 100,
        include_hidden: bool = False,
    ) -> dict[str, JsonValue]:
        shape = _request_scope(
            "list",
            {
                "path": self._file_reference_scope(path),
                "max_results": max_results,
                "include_hidden": include_hidden,
            },
        )
        return await self._execute_file(
            lambda: ctx.deps.environment.files.list(
                path,
                cursor=self._cursor(cursor, FileQueryCursor, shape, path),
                max_results=max_results,
                include_hidden=include_hidden,
            ),
            lambda page, provenance: {
                "path": page.path,
                "entries": [self._project_metadata(entry.metadata, provenance) for entry in page.entries],
                "next_cursor": self._cursor_reference(page.next_cursor, shape, page.path, provenance),
                "content_complete": page.content_complete,
            },
        )

    async def environment_query(
        self,
        ctx: RunContext[AgentContext],
        root: str,
        pattern: str,
        *,
        recursive: bool = True,
        include_hidden: bool = False,
        kinds: Sequence[Literal["file", "directory", "symlink", "other"]] | None = None,
        max_results: _PositiveResults = 100,
        cursor: str | None = None,
    ) -> dict[str, JsonValue]:
        fields: dict[str, JsonValue] = {
            "root": self._file_reference_scope(root),
            "pattern": pattern,
            "recursive": recursive,
            "include_hidden": include_hidden,
            "kinds": cast(JsonValue, sorted(kinds)) if kinds is not None else None,
            "max_results": max_results,
        }
        shape = _request_scope("query", fields)
        request = FileQueryRequest(
            root=root,
            pattern=pattern,
            recursive=recursive,
            include_hidden=include_hidden,
            kinds=frozenset(kinds) if kinds is not None else None,
            max_results=max_results,
            cursor=self._cursor(cursor, FileQueryCursor, shape, root),
        )
        return await self._execute_file(
            lambda: ctx.deps.environment.files.query(request),
            lambda page, provenance: {
                "entries": [self._project_metadata(entry.metadata, provenance) for entry in page.entries],
                "next_cursor": self._cursor_reference(page.next_cursor, shape, root, provenance),
                "content_complete": page.content_complete,
            },
        )

    async def environment_search_text(
        self,
        ctx: RunContext[AgentContext],
        root: str,
        pattern: str,
        *,
        regex: bool = False,
        case_sensitive: bool = True,
        include_hidden: bool = False,
        max_matches: _PositiveResults = 100,
        max_bytes: _PositiveTextBytes = 64 * 1024,
        cursor: str | None = None,
    ) -> dict[str, JsonValue]:
        fields: dict[str, JsonValue] = {
            "root": self._file_reference_scope(root),
            "pattern": pattern,
            "regex": regex,
            "case_sensitive": case_sensitive,
            "include_hidden": include_hidden,
            "max_matches": max_matches,
            "max_bytes": max_bytes,
        }
        shape = _request_scope("search_text", fields)
        request = FileTextSearchRequest(
            root=root,
            pattern=pattern,
            regex=regex,
            case_sensitive=case_sensitive,
            include_hidden=include_hidden,
            max_matches=max_matches,
            max_bytes=max_bytes,
            cursor=self._cursor(cursor, FileQueryCursor, shape, root),
        )
        return await self._execute_file(
            lambda: ctx.deps.environment.files.search_text(request),
            lambda page, provenance: {
                "matches": [
                    {
                        "path": match.path,
                        "revision": self._revision_reference(match.path, match.revision, provenance),
                        "line": match.line,
                        "byte_column": match.byte_column,
                        "text": match.text,
                    }
                    for match in page.matches
                ],
                "next_cursor": self._cursor_reference(page.next_cursor, shape, root, provenance),
                "content_complete": page.content_complete,
            },
        )

    async def environment_mkdir(
        self,
        ctx: RunContext[AgentContext],
        path: str,
        *,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> dict[str, JsonValue]:
        return await self._execute_file(
            lambda: ctx.deps.environment.files.mkdir(path, parents=parents, exist_ok=exist_ok),
            lambda result, provenance: {
                "path": result.path,
                "revision": self._revision_reference(result.path, result.revision, provenance),
            },
        )

    async def environment_move(
        self,
        ctx: RunContext[AgentContext],
        source: str,
        destination: str,
        *,
        expected_source_revision: str | None = None,
        replace_existing: bool = False,
    ) -> dict[str, JsonValue]:
        return await self._execute_file(
            lambda: ctx.deps.environment.files.move(
                source,
                destination,
                expected_source_revision=self._revision(expected_source_revision, source),
                replace=replace_existing,
            ),
            lambda result, provenance: {
                "path": result.path,
                "revision": self._revision_reference(result.path, result.revision, provenance),
            },
        )

    async def environment_remove(
        self,
        ctx: RunContext[AgentContext],
        path: str,
        *,
        recursive: bool = False,
        expected_revision: str | None = None,
    ) -> dict[str, JsonValue]:
        return await self._execute_file(
            lambda: ctx.deps.environment.files.remove(
                path,
                recursive=recursive,
                expected_revision=self._revision(expected_revision, path),
            ),
            lambda result, provenance: {"path": result.path, "removed": True},
        )

    async def environment_copy(
        self,
        ctx: RunContext[AgentContext],
        source: str,
        destination: str,
        *,
        expected_source_revision: str | None = None,
        expected_destination_revision: str | None = None,
        replace_existing: bool = False,
        require_atomic_destination: bool = True,
        require_stable_source: bool = False,
    ) -> dict[str, JsonValue]:
        return await self._execute_file(
            lambda: ctx.deps.environment.files.copy(
                source,
                destination,
                expected_source_revision=self._revision(expected_source_revision, source),
                expected_destination_revision=self._revision(expected_destination_revision, destination),
                replace=replace_existing,
                require_atomic_destination=require_atomic_destination,
                require_stable_source=require_stable_source,
            ),
            lambda result, provenance: {
                "path": result.path,
                "revision": self._revision_reference(result.path, result.revision, provenance),
                "bytes_copied": result.bytes_copied,
                "atomic_destination": result.atomic_destination,
                "source_stability": result.source_stability,
            },
        )

    async def environment_shell_exec(
        self,
        ctx: RunContext[AgentContext],
        command: ArgvCommand | ShellCommand,
        *,
        alias: str | None = None,
        cwd: str | None = None,
        environment: Mapping[str, str] | None = None,
        unset_environment: Sequence[str] = (),
        network: Literal["configured", "deny"] = "configured",
        wall_time_seconds: _PositiveTimeout | None = None,
        initial_stdin: str | None = None,
        max_inline_bytes: _PositiveTextBytes = 64 * 1024,
        max_output_bytes: _PositiveOutputBytes = 1024 * 1024,
    ) -> dict[str, JsonValue]:
        async def execute() -> Any:
            request = self._command_request(
                command,
                cwd=cwd,
                environment=environment,
                unset_environment=unset_environment,
                network=network,
                wall_time_seconds=wall_time_seconds,
                initial_stdin=initial_stdin,
                keep_stdin_open=False,
                max_inline_bytes=max_inline_bytes,
                max_output_bytes=max_output_bytes,
            )
            return await ctx.deps.environment.shell.exec(request, alias=alias)

        return await self._execute(
            execute,
            lambda result: {
                "status": self._project_status(result.status),
                "stdout": self._project_capture(result.output.stdout),
                "stderr": self._project_capture(result.output.stderr),
            },
        )

    async def environment_process_start(
        self,
        ctx: RunContext[AgentContext],
        command: ArgvCommand | ShellCommand,
        *,
        alias: str | None = None,
        cwd: str | None = None,
        environment: Mapping[str, str] | None = None,
        unset_environment: Sequence[str] = (),
        network: Literal["configured", "deny"] = "configured",
        wall_time_seconds: _PositiveTimeout | None = None,
        initial_stdin: str | None = None,
        keep_stdin_open: bool = False,
        max_inline_bytes: _PositiveTextBytes = 64 * 1024,
        max_output_bytes: _PositiveOutputBytes = 1024 * 1024,
    ) -> dict[str, JsonValue]:
        async def execute() -> Any:
            request = self._command_request(
                command,
                cwd=cwd,
                environment=environment,
                unset_environment=unset_environment,
                network=network,
                wall_time_seconds=wall_time_seconds,
                initial_stdin=initial_stdin,
                keep_stdin_open=keep_stdin_open,
                max_inline_bytes=max_inline_bytes,
                max_output_bytes=max_output_bytes,
            )
            return await ctx.deps.environment.processes.start(request, alias=alias)

        return await self._execute(
            execute,
            lambda result: self._project_process(result.process),
        )

    async def environment_process_inspect(
        self,
        ctx: RunContext[AgentContext],
        process: str,
    ) -> dict[str, JsonValue]:
        return await self._execute(
            lambda: ctx.deps.environment.processes.inspect(self._process(process)),
            self._project_process,
        )

    async def environment_process_read_output(
        self,
        ctx: RunContext[AgentContext],
        process: str,
        *,
        wait_seconds: _NonNegativeTimeout = 0,
        max_inline_bytes: _PositiveTextBytes = 64 * 1024,
        max_output_bytes: _PositiveOutputBytes = 1024 * 1024,
    ) -> dict[str, JsonValue]:
        async def execute() -> Any:
            policy = self._output_policy(max_inline_bytes, max_output_bytes)
            return await ctx.deps.environment.processes.read_output(
                self._process(process),
                wait_seconds=wait_seconds,
                policy=policy,
            )

        return await self._execute(
            execute,
            lambda result: {
                "process": self._project_process(result.process),
                "stdout": self._project_capture(result.stdout.capture),
                "stderr": self._project_capture(result.stderr.capture),
            },
        )

    async def environment_process_write_stdin(
        self,
        ctx: RunContext[AgentContext],
        process: str,
        data: str,
        *,
        close_after_write: bool = False,
    ) -> dict[str, JsonValue]:
        return await self._execute(
            lambda: ctx.deps.environment.processes.write_stdin(
                self._process(process),
                data.encode("utf-8"),
                close_after_write=close_after_write,
            ),
            lambda result: {"accepted_bytes": result.accepted_bytes, "stdin_open": result.stdin_open},
        )

    async def environment_process_close_stdin(
        self,
        ctx: RunContext[AgentContext],
        process: str,
    ) -> dict[str, JsonValue]:
        return await self._execute(
            lambda: ctx.deps.environment.processes.close_stdin(self._process(process)),
            lambda result: {"closed": True},
        )

    async def environment_process_signal(
        self,
        ctx: RunContext[AgentContext],
        process: str,
        signal: Literal["interrupt", "terminate"],
    ) -> dict[str, JsonValue]:
        return await self._execute(
            lambda: ctx.deps.environment.processes.signal(self._process(process), signal),
            lambda result: {
                "accepted": result.accepted,
                "process": self._project_process(result.process),
            },
        )

    async def environment_process_wait(
        self,
        ctx: RunContext[AgentContext],
        process: str,
        *,
        condition: Literal["initial_terminal", "tree_cleaned"] = "tree_cleaned",
        timeout_seconds: _PositiveTimeout,
    ) -> dict[str, JsonValue]:
        return await self._execute(
            lambda: ctx.deps.environment.processes.wait(
                self._process(process),
                condition=condition,
                timeout_seconds=timeout_seconds,
            ),
            self._project_process,
        )

    async def environment_process_kill(
        self,
        ctx: RunContext[AgentContext],
        process: str,
    ) -> dict[str, JsonValue]:
        return await self._execute(
            lambda: ctx.deps.environment.processes.kill(self._process(process)),
            lambda result: self._project_process(result.process),
        )

    async def environment_process_release(
        self,
        ctx: RunContext[AgentContext],
        process: str,
    ) -> dict[str, JsonValue]:
        async def release() -> None:
            handle = self._process(process)
            info = await ctx.deps.environment.processes.inspect(handle)
            await ctx.deps.environment.processes.release(handle)
            self._references.tombstone(process, "process")
            for capture in (info.output.stdout, info.output.stderr):
                if capture.reference is not None:
                    self._references.tombstone_value("output", capture.reference)

        return await self._execute(release, lambda result: {"released": True})

    async def environment_output_read(
        self,
        ctx: RunContext[AgentContext],
        output: str,
        *,
        start_offset: _NonNegativeOffset = 0,
        max_inline_bytes: _PositiveTextBytes = 64 * 1024,
    ) -> dict[str, JsonValue]:
        policy = EnvironmentOutputPolicy(
            max_inline_bytes=max_inline_bytes,
            max_output_bytes=max_inline_bytes,
            overflow="truncate",
        )
        return await self._execute(
            lambda: ctx.deps.environment.outputs.read(
                self._output(output),
                start_offset=start_offset,
                policy=policy,
            ),
            lambda result: {
                "output": output,
                "chunks": [
                    {
                        "start_offset": chunk.start_offset,
                        "text": chunk.data.decode("utf-8", errors="replace"),
                    }
                    for chunk in result.chunks
                ],
                "content_complete": result.capture.content_complete,
                "producer_complete": result.capture.producer_complete,
                "available_start": result.capture.available_start,
                "available_end": result.capture.available_end,
                "next_offset": (
                    max((chunk.start_offset + len(chunk.data) for chunk in result.chunks), default=start_offset)
                    if not result.capture.content_complete
                    else None
                ),
            },
        )

    async def environment_output_release(
        self,
        ctx: RunContext[AgentContext],
        output: str,
    ) -> dict[str, JsonValue]:
        async def release() -> None:
            await ctx.deps.environment.outputs.release(reference=self._output(output))
            self._references.tombstone(output, "output")

        return await self._execute(release, lambda result: {"released": True})

    async def environment_port_inspect(
        self,
        ctx: RunContext[AgentContext],
        port: Annotated[int, Field(ge=1, le=65535)],
        *,
        alias: str | None = None,
        address: Literal["loopback", "any"] = "loopback",
    ) -> dict[str, JsonValue]:
        return await self._execute(
            lambda: ctx.deps.environment.ports.inspect(PortTarget(alias=alias, address=address, port=port)),
            self._project_port,
        )

    async def environment_port_wait(
        self,
        ctx: RunContext[AgentContext],
        port: Annotated[int, Field(ge=1, le=65535)],
        *,
        alias: str | None = None,
        address: Literal["loopback", "any"] = "loopback",
        desired: Literal["listening", "not_listening"] = "listening",
        timeout_seconds: _PositiveTimeout,
    ) -> dict[str, JsonValue]:
        return await self._execute(
            lambda: ctx.deps.environment.ports.wait(
                PortTarget(alias=alias, address=address, port=port),
                desired=desired,
                timeout_seconds=timeout_seconds,
            ),
            self._project_port,
        )

    async def _execute(
        self,
        operation: Callable[[], Awaitable[Any]],
        project: Callable[[Any], Mapping[str, JsonValue]],
    ) -> dict[str, JsonValue]:
        try:
            self._assert_authorized_fence()
            result = await operation()
            return {"ok": True, **dict(project(result))}
        except EnvironmentError as exc:
            safe_details: dict[str, JsonValue] = {}
            timeout = exc.details.get("timeout_seconds")
            if isinstance(timeout, int | float) and not isinstance(timeout, bool):
                safe_details["timeout_seconds"] = timeout
            missing = exc.details.get("missing")
            if isinstance(missing, list) and all(isinstance(item, str) for item in missing):
                safe_details["missing"] = cast(JsonValue, list(missing))
            return {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "retry_hint": exc.retry_hint,
                    "details": safe_details,
                },
            }

    async def _execute_file(
        self,
        operation: Callable[[], Awaitable[Any]],
        project: Callable[[Any, _FileResultProvenance], Mapping[str, JsonValue]],
    ) -> dict[str, JsonValue]:
        files = self._environment.files
        if not isinstance(files, VirtualFileOperator):
            raise DefinitionError(
                "EnvironmentToolsCapability requires the Harness virtual file facade.",
                code="environment_tools_invalid",
            )
        token = files.begin_result_capture()
        try:
            return await self._execute(
                operation,
                lambda result: project(result, files.result_provenance()),
            )
        finally:
            files.reset_result_capture(token)

    def _file_reference_context(self, path: str) -> tuple[_BindingFence, str]:
        selected = self._environment.resolve_path(path)
        binding = next(
            (item for item in self._environment.topology.bindings if item.binding_id == selected.binding_id),
            None,
        )
        if binding is None or binding.binding_revision != selected.binding_revision:
            raise EnvironmentError("File binding is stale.", code="environment_stale_binding")
        return (
            _BindingFence(
                binding_id=binding.binding_id,
                binding_revision=binding.binding_revision,
                observed_generation=binding.descriptor.generation,
            ),
            selected.path,
        )

    @staticmethod
    def _captured_file_reference_context(
        path: str,
        provenance: _FileResultProvenance,
    ) -> tuple[_BindingFence, str]:
        return (
            _BindingFence(
                binding_id=provenance.binding_id,
                binding_revision=provenance.binding_revision,
                observed_generation=provenance.observed_generation,
            ),
            provenance.provider_path(path),
        )

    @staticmethod
    def _file_scope(
        binding: _BindingFence,
        provider_path: str,
        request_scope: str | None = None,
    ) -> str:
        return _request_scope(
            "file_reference",
            {
                "binding_id": binding.binding_id,
                "binding_revision": binding.binding_revision,
                "generation": binding.observed_generation,
                "path": provider_path,
                "request_scope": request_scope,
            },
        )

    def _file_reference_scope(self, path: str, request_scope: str | None = None) -> str:
        binding, provider_path = self._file_reference_context(path)
        return self._file_scope(binding, provider_path, request_scope)

    def _validate_file_reference(
        self,
        reference: _BoundFileReference,
        *,
        path: str,
        request_scope: str | None,
    ) -> None:
        binding, provider_path = self._file_reference_context(path)
        if (
            reference.binding != binding
            or reference.provider_path != provider_path
            or reference.request_scope != request_scope
        ):
            raise EnvironmentError(
                "File compact reference does not match the live binding and request.",
                code="environment_reference_stale",
            )

    def _revision(self, reference: str | None, path: str) -> FileRevision | None:
        if reference is None:
            return None
        value = self._references.resolve_value(reference, "revision")
        if not isinstance(value, _BoundFileReference) or not isinstance(value.value, FileRevision):
            raise EnvironmentError(
                "File revision reference has an incompatible value.",
                code="environment_reference_invalid",
            )
        self._validate_file_reference(value, path=path, request_scope=None)
        return value.value

    def _revision_reference(
        self,
        path: str,
        revision: FileRevision | None,
        provenance: _FileResultProvenance,
    ) -> str | None:
        if revision is None:
            return None
        binding, provider_path = self._captured_file_reference_context(path, provenance)
        value = _BoundFileReference(
            binding=binding,
            provider_path=provider_path,
            request_scope=None,
            value=revision,
        )
        return self._references.register(
            "revision",
            value,
            scope=self._file_scope(binding, provider_path),
        )

    def _cursor(
        self,
        reference: str | None,
        expected_type: type[_CursorT],
        scope: str,
        path: str,
    ) -> _CursorT | None:
        if reference is None:
            return None
        value = self._references.resolve_value(reference, "cursor")
        if not isinstance(value, _BoundFileReference) or not isinstance(value.value, expected_type):
            raise EnvironmentError(
                "File cursor reference has an incompatible value.",
                code="environment_reference_invalid",
            )
        self._validate_file_reference(value, path=path, request_scope=scope)
        return value.value

    def _cursor_reference(
        self,
        cursor: FileTextCursor | FileQueryCursor | None,
        scope: str,
        path: str,
        provenance: _FileResultProvenance,
    ) -> str | None:
        if cursor is None:
            return None
        binding, provider_path = self._captured_file_reference_context(path, provenance)
        value = _BoundFileReference(
            binding=binding,
            provider_path=provider_path,
            request_scope=scope,
            value=cursor,
        )
        return self._references.register(
            "cursor",
            value,
            scope=self._file_scope(binding, provider_path, scope),
        )

    def _process(self, reference: str) -> Any:
        from .commands import BoundProcessHandle

        value = self._references.resolve(reference, "process")
        if not isinstance(value, BoundProcessHandle):
            raise EnvironmentError("Process reference has an incompatible value.", code="environment_reference_invalid")
        return value

    def _output(self, reference: str) -> BoundOutputReference:
        value = self._references.resolve(reference, "output")
        if not isinstance(value, BoundOutputReference):
            raise EnvironmentError("Output reference has an incompatible value.", code="environment_reference_invalid")
        return value

    def _project_metadata(
        self,
        metadata: FileMetadata,
        provenance: _FileResultProvenance,
    ) -> dict[str, JsonValue]:
        return {
            "path": metadata.path,
            "kind": metadata.kind,
            "size": metadata.size,
            "revision": self._revision_reference(metadata.path, metadata.revision, provenance),
            "writable": metadata.writable,
        }

    def _project_process(self, process: ProcessInfo) -> dict[str, JsonValue]:
        reference = self._references.register("process", process.handle)
        return {
            "process": reference,
            "status": self._project_status(process.status),
            "stdin_open": process.stdin_open,
            "stdout": self._project_capture(process.output.stdout),
            "stderr": self._project_capture(process.output.stderr),
        }

    @staticmethod
    def _project_status(status: ProcessStatus) -> dict[str, JsonValue]:
        return {
            "phase": status.phase,
            "termination_reason": status.termination_reason,
            "exit_code": status.exit_code,
            "signal": status.signal,
            "cleanup": status.cleanup,
        }

    def _project_capture(self, capture: EnvironmentOutputCapture) -> dict[str, JsonValue]:
        data = capture.inline
        if data is None and capture.preview:
            data = b"".join(segment.data for segment in capture.preview)
        output_reference = None
        if capture.reference is not None:
            output_reference = self._references.register(
                "output",
                capture.reference,
                expires_at=capture.expires_at,
            )
        return {
            "kind": capture.kind,
            "producer_complete": capture.producer_complete,
            "content_complete": capture.content_complete,
            "produced_bytes": capture.produced_bytes,
            "captured_bytes": capture.captured_bytes,
            "dropped_bytes": capture.dropped_bytes,
            "text": (data or b"").decode("utf-8", errors="replace"),
            "output": output_reference,
            "available_start": capture.available_start,
            "available_end": capture.available_end,
            "expires_at": capture.expires_at.isoformat() if capture.expires_at is not None else None,
        }

    @staticmethod
    def _project_port(observation: PortObservation) -> dict[str, JsonValue]:
        return {
            "port": observation.target.port,
            "address": observation.target.address,
            "status": observation.status,
            "observed_at": observation.observed_at.isoformat(),
        }

    @staticmethod
    def _output_policy(max_inline_bytes: int, max_output_bytes: int) -> EnvironmentOutputPolicy:
        if max_inline_bytes > max_output_bytes:
            raise EnvironmentError(
                "max_inline_bytes cannot exceed max_output_bytes.",
                code="environment_request_invalid",
            )
        return EnvironmentOutputPolicy(
            max_inline_bytes=max_inline_bytes,
            max_output_bytes=max_output_bytes,
            overflow="retain",
        )

    def _command_request(
        self,
        command: ArgvCommand | ShellCommand,
        *,
        cwd: str | None,
        environment: Mapping[str, str] | None,
        unset_environment: Sequence[str],
        network: Literal["configured", "deny"],
        wall_time_seconds: float | None,
        initial_stdin: str | None,
        keep_stdin_open: bool,
        max_inline_bytes: int,
        max_output_bytes: int,
    ) -> CommandRequest:
        try:
            return CommandRequest(
                command=command,
                cwd=cwd,
                environment=CommandEnvironment(set=dict(environment or {}), unset=tuple(unset_environment)),
                network=network,
                limits=CommandLimits(wall_time_seconds=wall_time_seconds),
                initial_stdin=initial_stdin.encode("utf-8") if initial_stdin is not None else None,
                keep_stdin_open=keep_stdin_open,
                output_policy=self._output_policy(max_inline_bytes, max_output_bytes),
            )
        except EnvironmentError:
            raise
        except (TypeError, ValueError) as exc:
            raise EnvironmentError(
                "Environment command request is invalid.",
                code="environment_request_invalid",
            ) from exc


def _resolve_environment_result_projector(ctx: RunContext[AgentContext]) -> _EnvironmentResultProjector | None:
    value = ctx.capabilities.get(ENVIRONMENT_TOOLS_CAPABILITY_ID)
    if value is None:
        return None
    if not isinstance(value, _EnvironmentToolsRunCapability):
        raise DefinitionError(
            "Environment tools Capability has an incompatible finalized run value.",
            code="capability_type_mismatch",
        )
    return value


def _has_ordinary_user_boundary(ctx: RunContext[AgentContext], messages: Sequence[Any]) -> bool:
    if not messages or not isinstance(messages[-1], ModelRequest):
        return False
    final = messages[-1]
    if final.run_id != ctx.run_id:
        return False
    if not any(isinstance(part, UserPromptPart) for part in final.parts):
        return False
    return not any(isinstance(part, BaseToolReturnPart | RetryPromptPart) for part in final.parts)


def _string_argument(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    return value


def _optional_string_argument(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    return value


def _request_scope(operation: str, values: Mapping[str, JsonValue]) -> str:
    digest = hashlib.sha256(dump_json_bytes(dict(values), sort_keys=True)).hexdigest()
    return f"{operation}:{digest}"


def _bounded_topology_json(payload: dict[str, JsonValue], max_bytes: int) -> str:
    bindings = cast(list[JsonValue], payload["bindings"])
    while True:
        encoded = dump_json_bytes(payload, sort_keys=True)
        if len(encoded) <= max_bytes:
            return encoded.decode("utf-8")
        if bindings:
            bindings.pop()
            payload["truncated"] = True
            continue
        minimal: dict[str, JsonValue] = {
            "topology_version": payload["topology_version"],
            "bindings": [],
            "truncated": True,
        }
        encoded = dump_json_bytes(minimal, sort_keys=True)
        if len(encoded) > max_bytes:
            raise DefinitionError(
                "Environment topology byte limit cannot encode a minimal snapshot.",
                code="environment_tools_limit_invalid",
            )
        return encoded.decode("utf-8")


def _consume_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        # The Environment lifecycle remains authoritative; notices are best effort.
        return


__all__ = ["EnvironmentToolsCapability", "EnvironmentToolsConfiguration"]
