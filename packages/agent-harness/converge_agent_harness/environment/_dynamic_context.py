"""Dynamic Environment context projection and managed-resource fencing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, replace
from typing import Any, cast

from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.messages import BaseToolReturnPart, ModelRequest, RetryPromptPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext

from converge_agent_harness._json import dump_json_bytes
from converge_agent_harness.context import AgentContext
from converge_agent_harness.environment.commands import BoundProcessHandle
from converge_agent_harness.environment.models import (
    ENVIRONMENT_ACTION_DISPATCH,
    EnvironmentError,
)
from converge_agent_harness.errors import DefinitionError
from converge_agent_harness.tools.metadata import CanonicalResource, ToolResourceResolver

from .configuration import DynamicEnvironmentConfiguration
from .providers import BoundEnvironment

_DYNAMIC_ENVIRONMENT_INSTRUCTIONS = """Environment tools operate on the live run Environment.
Use relative paths or /workspace for the current default binding. Use /environment/{alias} for another binding.
Aliases are ordinary strings because topology can change without changing tool schemas.
Values named process-N are opaque references valid only in this logical run.
Never invent, alter, or persist a reference.
Environment tool results are bounded semantic JSON. A result with ok=false is a terminal operation result; adapt the
request instead of repeating it blindly. Prefer view before edit, exact replacements for partial changes, multi_edit
for multiple changes to one file, glob for path discovery, and grep for content search. Shell and process wall-time
limits are owned by the Environment provider. The Harness does not impose an additional Agent-wide tool timeout."""


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


class _DynamicEnvironmentContext:
    """Run-local topology projection and authorization fence shared by Toolsets."""

    def __init__(
        self,
        configuration: DynamicEnvironmentConfiguration,
        *,
        run_id: str,
        environment: BoundEnvironment,
        resolve_process: Callable[[str], BoundProcessHandle],
    ) -> None:
        self.configuration = configuration.model_copy(deep=True)
        self._run_id = run_id
        self._environment = environment
        self._resolve_process = resolve_process
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
                if exc.code not in {
                    "environment_reference_invalid",
                    "environment_reference_stale",
                    "environment_selection_invalid",
                    "environment_unavailable",
                }:
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
            "filesystem.view",
            "filesystem.write",
            "filesystem.edit",
            "filesystem.multi_edit",
        }:
            return (self._path_resource(context, _string_argument(arguments, "file_path")),)
        if tool_id == "filesystem.ls":
            return (self._path_resource(context, _string_argument(arguments, "path")),)
        if tool_id in {"filesystem.glob", "filesystem.grep"}:
            return (self._path_resource(context, _optional_string_argument(arguments, "root") or "."),)
        if tool_id in {"environment.shell_exec", "environment.process_start"}:
            alias = _optional_string_argument(arguments, "alias")
            cwd = _optional_string_argument(arguments, "cwd")
            if cwd is not None:
                return (self._path_resource(context, cwd, alias=alias),)
            return (self._binding_resource(context, alias),)
        if tool_id == "environment.process_status":
            return ()
        if tool_id.startswith("environment.process_"):
            handle = self._resolve_process(_string_argument(arguments, "process"))
            self._record_fence(handle.binding_id, handle.binding_revision, handle.observed_generation)
            return (
                CanonicalResource(
                    namespace="environment",
                    kind="process",
                    identifier=f"{handle.binding_id}:{handle.binding_revision}:{handle.observed_generation}",
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
                code="dynamic_environment_limit_invalid",
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


__all__ = ["_DYNAMIC_ENVIRONMENT_INSTRUCTIONS", "_DynamicEnvironmentContext"]
