from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import converge_agent_harness.execution as execution_module
import pytest
from converge_agent_harness import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalEnvironmentProviderBinding,
    DirectLocalRootConfiguration,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentError,
    EnvironmentPath,
    EnvironmentPermissionSet,
    EnvironmentStateLimits,
    EnvironmentToolsCapability,
    EnvironmentToolsConfiguration,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    ModelRecoveryPolicy,
    RunBindings,
    create_environment_run_binding,
    create_noop_environment_run_binding,
)
from converge_agent_harness.environment.files import FileMetadata, FileRevision
from converge_agent_harness.environment.retention import BoundOutputReference, OpaqueOutputReference
from converge_agent_harness.environment.tools import (
    _BoundFileReference,
    _CompactReferenceTable,
    _EnvironmentRetainedToolResult,
    _EnvironmentToolsRunCapability,
)
from converge_agent_harness.environment.virtual_files import (
    VirtualFileOperator,
    _FileResultProvenance,
    _PreparedFile,
)
from converge_agent_harness.plugins import (
    AbstractHarnessPlugin,
    PluginOrdering,
    PluginRunExchange,
    PluginRunNext,
    PluginRunResponse,
)
from converge_agent_harness.result import HarnessRunResult
from converge_agent_harness.tools import (
    HARNESS_TOOL_METADATA_KEY,
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from converge_agent_harness.tools.invocation import _apply_result_policy
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


def _configuration(**updates: Any) -> EnvironmentToolsConfiguration:
    return EnvironmentToolsConfiguration(
        max_topology_bindings=8,
        max_topology_bytes=4096,
        max_reference_entries=64,
        **updates,
    )


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


def _policy() -> InvocationPolicyCapability:
    return InvocationPolicyCapability(evaluator=_Allow(), max_dispatch_retries=0)


def _local_binding(root: Path):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="environment-tools-test",
            root=DirectLocalRootConfiguration(path=root, ownership="caller_owned"),
        )
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )
    return create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


async def test_dynamic_topology_emits_an_independent_harness_context_event(tmp_path: Path) -> None:
    started = asyncio.Event()
    finish = asyncio.Event()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        started.set()
        await finish.wait()
        yield "done"

    aggregate = create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(topology_version=0, bindings=(), default_binding_id=None),
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2),
        state_limits=EnvironmentStateLimits(),
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="environment-tools-test",
            root=DirectLocalRootConfiguration(path=tmp_path, ownership="caller_owned"),
        )
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )

    async with executable.stream("wait", bindings=RunBindings.local(environment=aggregate)) as run:
        pending = asyncio.create_task(run.__anext__())
        await started.wait()
        await aggregate.controller.apply(request)
        item = await asyncio.wait_for(pending, timeout=2)
        assert isinstance(item, HarnessEvent)
        assert isinstance(item.event, HarnessExtensionEvent)
        assert item.event.kind == "context"
        assert item.event.payload["type"] == "environment_topology_changed"
        assert item.event.payload["current_version"] == 1
        finish.set()
        terminal = [event async for event in run][-1]
        assert terminal.result.output_or_raise() == "done"


async def test_capability_projects_stable_tools_and_one_bounded_fresh_topology_snapshot() -> None:
    calls: list[tuple[list[ModelMessage], AgentInfo]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append((messages, info))
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(EnvironmentToolsCapability(_configuration()),),
    )
    result = await executable.run("inspect", bindings=RunBindings.local())

    assert result.output_or_raise() == "done"
    assert len(calls) == 1
    messages, info = calls[0]
    names = {tool.name for tool in info.function_tools}
    assert "environment_read_text" in names
    assert "environment_process_start" in names
    assert "environment_port_inspect" not in names
    metadata = {
        tool.name: tool.metadata[HARNESS_TOOL_METADATA_KEY]
        for tool in info.function_tools
        if tool.metadata is not None and HARNESS_TOOL_METADATA_KEY in tool.metadata
    }
    assert metadata["environment_copy"].effects == frozenset({"read", "write"})
    assert metadata["environment_move"].effects == frozenset({"write", "delete"})
    assert metadata["environment_shell_exec"].effects == frozenset(
        {"read", "write", "delete", "execute", "external_communication"}
    )
    assert metadata["environment_process_start"].effects == metadata["environment_shell_exec"].effects
    assert info.instructions is not None
    assert "Agent-wide tool timeout" in info.instructions
    topology_parts = [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str) and "topology_version" in part.content
    ]
    assert len(topology_parts) == 1
    assert '"bindings":[]' in topology_parts[0]
    assert len(topology_parts[0].encode()) < _configuration().max_topology_bytes
    assert executable.definition.agent.tool_timeout is None


async def test_file_tools_use_scoped_compact_revisions_and_native_managed_policy(tmp_path: Path) -> None:
    model_calls = 0
    tool_results: list[dict[str, Any]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal model_calls
        del info
        model_calls += 1
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        tool_results[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="environment_write_text",
                    json_args=json.dumps({"path": "note.txt", "text": "one", "mode": "create"}),
                    tool_call_id="write-1",
                )
            }
        elif len(returns) == 1:
            yield {
                0: DeltaToolCall(
                    name="environment_write_text",
                    json_args=json.dumps(
                        {
                            "path": "/workspace/note.txt",
                            "text": "two",
                            "mode": "replace",
                            "expected_revision": returns[0]["revision"],
                        }
                    ),
                    tool_call_id="write-2",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test", retries={"tools": 2}),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(EnvironmentToolsCapability(_configuration()),),
    )
    result = await executable.run(
        "write",
        bindings=RunBindings.local(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    assert model_calls == 3
    assert (tmp_path / "note.txt").read_text() == "two"
    assert tool_results[0]["ok"] is True
    assert tool_results[0]["revision"] == "revision-1"
    assert tool_results[1]["revision"] == "revision-2"


async def test_compact_references_survive_inner_model_recovery_attempts(tmp_path: Path) -> None:
    failed_once = False

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal failed_once
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="environment_write_text",
                    json_args=json.dumps({"path": "/workspace/recovered.txt", "text": "one", "mode": "create"}),
                    tool_call_id="recover-write-1",
                )
            }
        elif len(returns) == 1 and not failed_once:
            failed_once = True
            raise RuntimeError("model stream disconnected")
        elif len(returns) == 1:
            yield {
                0: DeltaToolCall(
                    name="environment_write_text",
                    json_args=json.dumps(
                        {
                            "path": "/workspace/recovered.txt",
                            "text": "two",
                            "mode": "replace",
                            "expected_revision": returns[0]["revision"],
                        }
                    ),
                    tool_call_id="recover-write-2",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(EnvironmentToolsCapability(_configuration()),),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )
    result = await executable.run(
        "write",
        bindings=RunBindings.local(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    assert (tmp_path / "recovered.txt").read_text() == "two"
    assert result.usage.requests == 4


def test_compact_reference_table_is_scoped_bounded_monotonic_and_tombstoned() -> None:
    table = _CompactReferenceTable(max_entries=2)
    revision = object()

    assert table.register("revision", revision, scope="/workspace/a") == "revision-1"
    assert table.register("revision", revision, scope="/workspace/a") == "revision-1"
    with pytest.raises(Exception) as wrong_scope:
        table.resolve("revision-1", "revision", scope="/workspace/b")
    assert getattr(wrong_scope.value, "code", None) == "environment_reference_invalid"

    table.tombstone("revision-1", "revision")
    with pytest.raises(Exception) as stale:
        table.resolve("revision-1", "revision", scope="/workspace/a")
    assert getattr(stale.value, "code", None) == "environment_reference_stale"

    assert table.register("cursor", object(), scope="query:one") == "cursor-1"
    with pytest.raises(Exception) as exhausted:
        table.register("output", object())
    assert getattr(exhausted.value, "code", None) == "environment_reference_exhausted"


async def test_managed_dispatch_fails_stale_when_policy_wait_refreshes_binding(tmp_path: Path) -> None:
    (tmp_path / "value.txt").write_text("value")
    aggregate = _local_binding(tmp_path)
    replacement = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="environment-tools-test",
            root=DirectLocalRootConfiguration(path=tmp_path, ownership="caller_owned"),
        )
    )
    refresh = EnvironmentTopologyRequest(
        topology_version=2,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=2,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=replacement,
            ),
        ),
        default_binding_id="binding-1",
    )

    class RefreshOnAuthorize:
        applied = False

        async def __call__(self, invocation, metadata, *, context):
            del invocation, metadata, context
            if not self.applied:
                self.applied = True
                await aggregate.controller.apply(refresh)
            return InvocationPolicyDecision.allow()

    observed: dict[str, Any] = {}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="environment_stat",
                    json_args=json.dumps({"path": "/workspace/value.txt"}),
                    tool_call_id="stat-refresh-1",
                )
            }
        else:
            assert isinstance(returns[-1], dict)
            observed.update(returns[-1])
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(EnvironmentToolsCapability(_configuration()),),
    )
    result = await executable.run(
        "inspect",
        bindings=RunBindings.local(
            environment=aggregate,
            capabilities=(InvocationPolicyCapability(evaluator=RefreshOnAuthorize()),),
        ),
    )

    assert result.output_or_raise() == "done"
    assert observed["ok"] is False
    assert observed["error"]["code"] == "environment_stale_binding"


async def test_file_references_and_managed_authorization_are_fenced_by_binding_revision(
    tmp_path: Path,
) -> None:
    aggregate = _local_binding(tmp_path)
    run_bindings = RunBindings.local(environment=aggregate)
    replacement = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="environment-tools-test",
            root=DirectLocalRootConfiguration(path=tmp_path, ownership="caller_owned"),
        )
    )
    refresh = EnvironmentTopologyRequest(
        topology_version=2,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=2,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=replacement,
            ),
        ),
        default_binding_id="binding-1",
    )

    async with aggregate.bind(run_id="run-1", instance=run_bindings.instance) as environment:
        await environment.activate()
        capability = _EnvironmentToolsRunCapability(
            _configuration(),
            run_id="run-1",
            environment=environment,
        )
        revision = FileRevision("provider-revision")
        binding, provider_path = capability._file_reference_context("relative.txt")
        provenance = _FileResultProvenance(
            binding_id=binding.binding_id,
            binding_revision=binding.binding_revision,
            observed_generation=binding.observed_generation,
            paths=(("relative.txt", provider_path),),
        )
        compact = capability._revision_reference("relative.txt", revision, provenance)
        assert compact == "revision-1"
        assert capability._revision(compact, "/workspace/relative.txt") == revision

        resolver = capability._resource_resolver("environment.stat")
        resources = await resolver(
            {"path": "/workspace/relative.txt"},
            context=cast(Any, SimpleNamespace(environment=environment)),
        )
        assert len(resources) == 1
        await aggregate.controller.apply(refresh)

        with pytest.raises(Exception) as stale_reference:
            capability._revision(compact, "/workspace/relative.txt")
        assert getattr(stale_reference.value, "code", None) == "environment_reference_stale"
        with pytest.raises(Exception) as stale_authorization:
            capability._assert_authorized_fence()
        assert getattr(stale_authorization.value, "code", None) == "environment_stale_binding"


def test_ineligible_retained_output_uses_bounded_no_reference_fallback() -> None:
    retained = _EnvironmentRetainedToolResult(
        value={"content": "x" * 1000},
        reference=BoundOutputReference(
            binding_id="binding-1",
            binding_revision=1,
            observed_generation="generation-1",
            reference=OpaqueOutputReference._from_payload("retained-1"),
        ),
    )
    metadata = HarnessToolMetadata(
        tool_id="test.retained-fallback",
        effects=frozenset({"read"}),
        credential_audiences=(),
        idempotency="read_only",
        output_policy=ToolOutputPolicy(
            max_inline_bytes=64,
            max_output_bytes=2048,
            overflow="environment_reference",
            redact=True,
        ),
    )

    class RejectingProjector:
        def project_retained_result(self, result: Any) -> Any:
            del result
            raise EnvironmentError("stale", code="environment_reference_stale")

    projected = _apply_result_policy(retained, metadata, environment_projector=RejectingProjector())
    assert isinstance(projected, dict)
    assert projected["complete"] is False
    assert projected["reference"] is None


async def test_model_error_projection_omits_internal_environment_details() -> None:
    capability = _EnvironmentToolsRunCapability(
        _configuration(),
        run_id="run-1",
        environment=SimpleNamespace(),
    )

    async def fail() -> None:
        raise EnvironmentError(
            "internal provider detail",
            code="environment_unavailable",
            details={
                "binding_id": "binding-secret",
                "generation": "generation-secret",
                "reason_code": "provider-secret",
                "timeout_seconds": 3,
                "missing": ["files"],
            },
        )

    result = await capability._execute(fail, lambda value: {})
    assert result["ok"] is False
    assert result["error"]["details"] == {"timeout_seconds": 3, "missing": ["files"]}


async def test_managed_environment_reference_uses_the_run_capability_table(tmp_path: Path) -> None:
    def produce(ctx: RunContext[Any]) -> Any:
        binding = ctx.deps.environment.topology.bindings[0]
        retained = BoundOutputReference(
            binding_id=binding.binding_id,
            binding_revision=binding.binding_revision,
            observed_generation=binding.descriptor.generation,
            reference=OpaqueOutputReference._from_payload("retained-1"),
        )
        return _EnvironmentRetainedToolResult(value={"content": "x" * 1000}, reference=retained)

    tool = HarnessTool(
        produce,
        harness_metadata=HarnessToolMetadata(
            tool_id="test.retained",
            effects=frozenset({"read"}),
            credential_audiences=(),
            idempotency="read_only",
            output_policy=ToolOutputPolicy(
                max_inline_bytes=64,
                max_output_bytes=2048,
                overflow="environment_reference",
                redact=True,
            ),
        ),
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {0: DeltaToolCall(name="produce", json_args="{}", tool_call_id="produce-1")}
        else:
            content = returns[-1].content
            assert isinstance(content, dict)
            assert content["reference"] == "output-1"
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Capability(tools=[tool], id="test-tools"),
            EnvironmentToolsCapability(_configuration()),
        ),
    )
    result = await executable.run(
        "produce",
        bindings=RunBindings.local(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"


async def test_empty_topology_tool_returns_typed_unavailable_result_after_policy_allow() -> None:
    observed: dict[str, Any] = {}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="environment_stat",
                    json_args=json.dumps({"path": "/workspace/missing"}),
                    tool_call_id="stat-1",
                )
            }
        else:
            assert isinstance(returns[-1], dict)
            observed.update(returns[-1])
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(EnvironmentToolsCapability(_configuration()),),
    )
    result = await executable.run("inspect", bindings=RunBindings.local(capabilities=(_policy(),)))

    assert result.output_or_raise() == "done"
    assert observed["ok"] is False
    assert observed["error"]["code"] == "environment_selection_invalid"


async def test_large_environment_result_is_bounded_without_retry_shaped_failure(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("\\" * (256 * 1024))
    observed: dict[str, Any] = {}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="environment_read_text",
                    json_args=json.dumps({"path": "/workspace/large.txt", "max_bytes": 256 * 1024}),
                    tool_call_id="large-read-1",
                )
            }
        else:
            assert isinstance(returns[-1], dict)
            observed.update(returns[-1])
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(EnvironmentToolsCapability(_configuration()),),
    )
    result = await executable.run(
        "read",
        bindings=RunBindings.local(environment=_local_binding(tmp_path), capabilities=(_policy(),)),
    )

    assert result.output_or_raise() == "done"
    assert observed["complete"] is False
    assert observed["truncated"] is True
    assert isinstance(observed["content"], str)


async def test_agent_spec_tool_retries_exhaust_once_without_environment_retry_loop() -> None:
    model_calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
        nonlocal model_calls
        del messages, info
        model_calls += 1
        yield {
            0: DeltaToolCall(
                name="environment_stat",
                json_args="{}",
                tool_call_id=f"invalid-{model_calls}",
            )
        }

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test", retries={"tools": 2}),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(EnvironmentToolsCapability(_configuration()),),
    )
    result = await executable.run("inspect", bindings=RunBindings.local(capabilities=(_policy(),)))

    assert result.status == "failed"
    assert model_calls == 3
    assert result.usage.requests == 3
    retry_parts = [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    ]
    assert len(retry_parts) == 2


async def test_file_projection_uses_operation_revision_after_concurrent_refresh() -> None:
    class Backend:
        async def stat(self, path: str) -> FileMetadata:
            return FileMetadata(
                path=path,
                kind="file",
                size=1,
                revision=FileRevision("old-token"),
                writable=True,
            )

    @asynccontextmanager
    async def prepare(path: str, action: EnvironmentAction):
        del action
        yield _PreparedFile(
            selected=EnvironmentPath(
                binding_id="binding-1",
                binding_revision=1,
                path=f"/{path}",
            ),
            observed_generation="generation-1",
            backend=Backend(),
            validate_result=lambda value: None,
        )

    files = VirtualFileOperator(prepare, lambda selected, path: path)

    class RefreshedEnvironment:
        def __init__(self) -> None:
            self.files = files

        def resolve_path(self, path: str, *, alias: str | None = None) -> EnvironmentPath:
            del path, alias
            raise AssertionError("projection must not resolve the live revision")

    environment = RefreshedEnvironment()
    capability = _EnvironmentToolsRunCapability(
        _configuration(),
        run_id="run-1",
        environment=cast(Any, environment),
    )
    context = cast(Any, SimpleNamespace(deps=SimpleNamespace(environment=environment)))

    result = await capability.environment_stat(context, "value.txt")

    assert result["ok"] is True
    reference = cast(str, result["revision"])
    retained = capability._references.resolve_value(reference, "revision")
    assert isinstance(retained, _BoundFileReference)
    assert retained.binding.binding_revision == 1
    assert retained.binding.observed_generation == "generation-1"
    assert retained.provider_path == "/value.txt"


class _ApplyTopologyAfterResultPlugin(AbstractHarnessPlugin):
    def __init__(
        self,
        controller: Any,
        request: EnvironmentTopologyRequest,
        *,
        applied: asyncio.Event | None = None,
    ) -> None:
        self._controller = controller
        self._request = request
        self._applied = applied

    @property
    def plugin_id(self) -> str:
        return "apply-topology-after-result"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessRunResult):
                    await self._controller.apply(self._request)
                    if self._applied is not None:
                        self._applied.set()
                yield item

        return PluginRunResponse(iterate())


def _dynamic_local_request(root: Path) -> EnvironmentTopologyRequest:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="environment-event-test",
            root=DirectLocalRootConfiguration(path=root, ownership="caller_owned"),
        )
    )
    return EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )


def _topology_context_events(items: list[Any]) -> list[HarnessEvent]:
    return [
        item
        for item in items
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.payload.get("type") == "environment_topology_changed"
    ]


async def test_topology_event_adapter_survives_model_recovery_boundary(tmp_path: Path) -> None:
    prompt_started = asyncio.Event()
    release_prompt = asyncio.Event()
    calls = 0

    async def prompt_factory(error: BaseException, attempt: int, messages: Any) -> str:
        del error, attempt, messages
        prompt_started.set()
        await release_prompt.wait()
        return "continue"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        del messages, info
        calls += 1
        if calls == 1:
            raise RuntimeError("recoverable failure")
        yield "done"

    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2)
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            prompt_factory=prompt_factory,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )
    async with executable.stream("start", bindings=RunBindings.local(environment=aggregate)) as run:
        pending = asyncio.create_task(run.__anext__())
        await prompt_started.wait()
        await aggregate.controller.apply(_dynamic_local_request(tmp_path))
        topology_event = await asyncio.wait_for(pending, timeout=2)
        assert _topology_context_events([topology_event]) == [topology_event]
        release_prompt.set()
        remaining = [item async for item in run]

    assert calls == 2
    assert remaining[-1].result.output_or_raise() == "done"


async def test_topology_event_from_result_middleware_precedes_terminal_result(tmp_path: Path) -> None:
    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2)
    )
    plugin = _ApplyTopologyAfterResultPlugin(
        aggregate.controller,
        _dynamic_local_request(tmp_path),
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(plugin,),
    )
    async with executable.stream("start", bindings=RunBindings.local(environment=aggregate)) as run:
        items = [item async for item in run]

    topology_events = _topology_context_events(items)
    assert len(topology_events) == 1
    assert items.index(topology_events[0]) < len(items) - 1
    assert items[-1].result.output_or_raise() == "done"


class _TransformTopologyEventsPlugin(AbstractHarnessPlugin):
    def __init__(self) -> None:
        self.seen = 0
        self.order: list[str] = []

    @property
    def plugin_id(self) -> str:
        return "transform-topology-events"

    def get_ordering(self) -> PluginOrdering:
        return PluginOrdering(wraps=("apply-topology-after-result",))

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if _topology_context_events([item]):
                    assert isinstance(item, HarnessEvent)
                    assert isinstance(item.event, HarnessExtensionEvent)
                    self.seen += 1
                    self.order.append("event")
                    item = HarnessEvent(
                        run_id=item.run_id,
                        sequence=item.sequence,
                        occurred_at=item.occurred_at,
                        event=HarnessExtensionEvent(
                            kind=item.event.kind,
                            payload={**item.event.payload, "observed_by_plugin": True},
                        ),
                    )
                elif isinstance(item, HarnessRunResult):
                    self.order.append("result")
                yield item

        return PluginRunResponse(iterate())


async def test_emitter_topology_events_pass_through_plugin_middleware(tmp_path: Path) -> None:
    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2)
    )
    apply_plugin = _ApplyTopologyAfterResultPlugin(
        aggregate.controller,
        _dynamic_local_request(tmp_path),
    )
    transform_plugin = _TransformTopologyEventsPlugin()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(apply_plugin, transform_plugin),
    )
    async with executable.stream("start", bindings=RunBindings.local(environment=aggregate)) as run:
        items = [item async for item in run]

    topology_events = _topology_context_events(items)
    assert transform_plugin.seen == 1
    assert transform_plugin.order == ["event", "result"]
    assert len(topology_events) == 1
    assert topology_events[0].event.payload["observed_by_plugin"] is True
    assert items[-1].result.output_or_raise() == "done"


class _ApplyTopologyBurstAfterResultPlugin(AbstractHarnessPlugin):
    def __init__(self, controller: Any, count: int) -> None:
        self._controller = controller
        self._count = count

    @property
    def plugin_id(self) -> str:
        return "apply-topology-burst-after-result"

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext,
    ) -> PluginRunResponse:
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessRunResult):
                    for version in range(1, self._count + 1):
                        await self._controller.apply(
                            EnvironmentTopologyRequest(
                                topology_version=version,
                                bindings=(),
                                default_binding_id=None,
                            )
                        )
                yield item

        return PluginRunResponse(iterate())


async def test_terminal_drains_topology_burst_larger_than_emitter_capacity() -> None:
    change_count = 65
    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=1, max_committed_changes=change_count)
    )
    plugin = _ApplyTopologyBurstAfterResultPlugin(aggregate.controller, change_count)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(plugin,),
    )
    async with executable.stream("start", bindings=RunBindings.local(environment=aggregate)) as run:
        items = [item async for item in run]

    assert len(_topology_context_events(items)) == change_count
    assert items[-1].result.output_or_raise() == "done"


async def test_terminal_waits_for_delayed_topology_adapter_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_started = asyncio.Event()
    release_adapter = asyncio.Event()
    topology_applied = asyncio.Event()
    original_adapter = execution_module._emit_environment_topology_events

    async def delayed_adapter(context: Any, drain: Any) -> None:
        adapter_started.set()
        await release_adapter.wait()
        await original_adapter(context, drain)

    monkeypatch.setattr(execution_module, "_emit_environment_topology_events", delayed_adapter)
    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2)
    )
    plugin = _ApplyTopologyAfterResultPlugin(
        aggregate.controller,
        _dynamic_local_request(tmp_path),
        applied=topology_applied,
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(plugin,),
    )

    async def collect() -> list[Any]:
        async with executable.stream("start", bindings=RunBindings.local(environment=aggregate)) as run:
            return [item async for item in run]

    collect_task = asyncio.create_task(collect())
    await adapter_started.wait()
    await topology_applied.wait()
    await asyncio.sleep(0)
    assert not collect_task.done()
    release_adapter.set()
    items = await collect_task

    topology_events = _topology_context_events(items)
    assert len(topology_events) == 1
    assert items.index(topology_events[0]) < len(items) - 1
    assert items[-1].result.output_or_raise() == "done"
