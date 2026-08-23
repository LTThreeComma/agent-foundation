"""Outer managed-invocation boundary over Pydantic AI's assembled Toolset."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
from collections.abc import AsyncIterable, Iterator, Mapping
from contextlib import AsyncExitStack
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.exceptions import ApprovalRequired, ToolFailed
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset

from converge_agent_harness._json import (
    dump_json_bytes,
    is_sensitive_key,
    redact_bearer,
    redact_json,
    require_finite_json,
)
from converge_agent_harness.context import AgentContext
from converge_agent_harness.errors import DefinitionError
from converge_agent_harness.events import HarnessExtensionEvent
from converge_agent_harness.tools.deferred import managed_approval_tool_id
from converge_agent_harness.tools.metadata import (
    HARNESS_TOOL_METADATA_KEY,
    CanonicalResource,
    HarnessToolMetadata,
    normalize_harness_tool_metadata,
)
from converge_agent_harness.tools.policy import (
    INVOCATION_POLICY_CAPABILITY_ID,
    CredentialLease,
    InvocationGrantRef,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    InvocationScope,
    ToolInvocationContext,
)

INVOCATION_AUTHORIZATION_CAPABILITY_ID = "converge.invocation-authorization"
MAX_ARGUMENT_BYTES = 64 * 1024

_ANY_ADAPTER = TypeAdapter(Any)
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_INVOCATION_SCOPE: contextvars.ContextVar[InvocationScope | None] = contextvars.ContextVar(
    "converge_harness_invocation_scope",
    default=None,
)


@dataclass(frozen=True, slots=True)
class _PreparedInvocation:
    context: ToolInvocationContext
    typed_arguments: dict[str, Any]


class ManagedToolProviderError(Exception):
    """Typed provider failure carrying only retry and outcome evidence."""

    def __init__(
        self,
        message: str = "Managed tool provider failed.",
        *,
        retryable: bool = False,
        outcome_known: bool = True,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.outcome_known = outcome_known


def current_invocation_scope() -> InvocationScope:
    """Return task-local managed authority for a first-party dispatch adapter."""
    scope = _INVOCATION_SCOPE.get()
    if scope is None:
        raise RuntimeError("No managed tool invocation is active in this task")
    return scope


@dataclass
class InvocationAuthorizationCapability(AbstractCapability[AgentContext]):
    """Mandatory code-owned outer wrapper for every non-output Toolset."""

    id: str | None = INVOCATION_AUTHORIZATION_CAPABILITY_ID

    def __post_init__(self) -> None:
        if self.id != INVOCATION_AUTHORIZATION_CAPABILITY_ID:
            raise ValueError(f"InvocationAuthorizationCapability.id must be {INVOCATION_AUTHORIZATION_CAPABILITY_ID!r}")

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(AbstractCapability,))

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return InvocationAuthorizationToolset(toolset)

    async def wrap_run_event_stream(
        self,
        ctx: RunContext[AgentContext],
        *,
        stream: AsyncIterable[Any],
    ) -> AsyncIterable[Any]:
        _validate_finalized_capability_provenance(ctx)
        async for event in stream:
            yield event


@dataclass
class InvocationAuthorizationToolset(WrapperToolset[AgentContext]):
    """Normalize final definitions and dispatch only opted-in function tools."""

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        _validate_client_run_attachment(ctx)
        _resolve_environment_result_projector(ctx)
        tools = await self.wrapped.get_tools(ctx)
        policy = _resolve_policy(ctx)
        managed_ids: dict[str, str] = {}
        normalized: dict[str, ToolsetTool[AgentContext]] = {}

        for name, tool in tools.items():
            tool_def = tool.tool_def
            metadata_values = tool_def.metadata or {}
            reserved_present = HARNESS_TOOL_METADATA_KEY in metadata_values

            if tool_def.kind == "external":
                if reserved_present:
                    raise DefinitionError(
                        "External tools cannot carry managed function-tool metadata.",
                        code="tool_metadata_kind_invalid",
                    )
                _validate_client_marker(tool_def.name, metadata_values)
                normalized[name] = tool
                continue

            if tool_def.kind not in {"function", "unapproved"}:
                normalized[name] = tool
                continue

            if not reserved_present:
                if policy is not None and policy.strict_managed_tools:
                    raise DefinitionError(
                        "The current invocation policy requires every function tool to be managed.",
                        code="unmanaged_tool_rejected",
                        details={"tool_name": tool_def.name},
                    )
                normalized[name] = tool
                continue

            managed = normalize_harness_tool_metadata(metadata_values[HARNESS_TOOL_METADATA_KEY])
            if previous := managed_ids.get(managed.tool_id):
                raise DefinitionError(
                    "Managed tool_id values must be unique in the assembled run surface.",
                    code="managed_tool_id_duplicate",
                    details={"tool_id": managed.tool_id, "tool_name": tool_def.name, "other_tool_name": previous},
                )
            managed_ids[managed.tool_id] = tool_def.name
            copied_metadata = dict(metadata_values)
            copied_metadata[HARNESS_TOOL_METADATA_KEY] = managed
            normalized[name] = replace(tool, tool_def=replace(tool_def, metadata=copied_metadata))

        ctx.deps._record_managed_tool_surface({tool_name: tool_id for tool_id, tool_name in managed_ids.items()})
        _validate_resume_surface(ctx, normalized)
        return normalized

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentContext],
        tool: ToolsetTool[AgentContext],
    ) -> Any:
        tool_def = tool.tool_def
        if tool_def.kind == "external":
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        metadata_values = tool_def.metadata or {}
        raw_metadata = metadata_values.get(HARNESS_TOOL_METADATA_KEY)
        if raw_metadata is None:
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        managed = normalize_harness_tool_metadata(raw_metadata)
        policy = _resolve_policy(ctx)
        if policy is None:
            await _emit(ctx, managed, "denied", reason="policy_unavailable")
            raise ToolFailed("Managed tool authorization is unavailable.")

        try:
            prepared = await _prepare_invocation(ctx, tool_def.name, tool_def.toolset_id, tool_args, managed)
        except ToolFailed:
            await _emit(ctx, managed, "preparation_failed")
            raise
        invocation = prepared.context
        await _emit(ctx, managed, "prepared", invocation_id=invocation.invocation_id)
        if ctx.tool_call_approved and policy.approval_verifier is not None:
            approval_metadata = ctx.tool_call_metadata
            if approval_metadata is None:
                approval_metadata = {}
            if not isinstance(approval_metadata, Mapping) or not all(isinstance(key, str) for key in approval_metadata):
                raise ToolFailed("Managed tool approval metadata is invalid.")
            try:
                verified = await policy.approval_verifier.verify(
                    invocation,
                    cast(Mapping[str, JsonValue], approval_metadata),
                    context=ctx.deps,
                )
            except Exception as exc:
                raise ToolFailed("Managed tool approval could not be verified.") from exc
            if verified is not True:
                await _emit(ctx, managed, "denied", invocation_id=invocation.invocation_id)
                raise ToolFailed("Managed tool approval is no longer valid.")
        await _require_policy_allow(
            ctx,
            policy,
            invocation,
            managed,
            approval_satisfied=ctx.tool_call_approved,
        )
        await _emit(ctx, managed, "authorized", invocation_id=invocation.invocation_id)
        leases: list[CredentialLease] = []
        lease_stack = AsyncExitStack()
        grant = None
        try:
            if policy.credential_broker is not None:
                for audience in managed.credential_audiences:
                    try:
                        lease = await policy.credential_broker.acquire(audience, invocation, context=ctx.deps)
                    except (DefinitionError, ToolFailed):
                        raise
                    except Exception as exc:
                        raise ToolFailed("A required managed tool credential is unavailable.") from exc
                    if not isinstance(lease, CredentialLease):
                        raise DefinitionError(
                            "Credential broker returned an invalid lease.",
                            code="credential_lease_invalid",
                        )
                    lease_stack.push_async_callback(lease.close)
                    if lease.audience != audience:
                        raise DefinitionError(
                            "Credential broker returned a lease for the wrong audience.",
                            code="credential_lease_invalid",
                        )
                    leases.append(lease)
            elif managed.credential_audiences:
                raise ToolFailed("A required managed tool credential is unavailable.")

            if policy.grant_broker is not None:
                try:
                    grant = await policy.grant_broker.issue(invocation, managed, context=ctx.deps)
                except (DefinitionError, ToolFailed):
                    raise
                except Exception as exc:
                    raise ToolFailed("A managed tool invocation grant is unavailable.") from exc
                if grant is not None and not isinstance(grant, InvocationGrantRef):
                    raise DefinitionError(
                        "Invocation grant broker returned an invalid value.",
                        code="invocation_grant_invalid",
                    )
                if grant is not None and grant.expires_at <= datetime.now(UTC):
                    raise ToolFailed("A managed tool invocation grant is unavailable.")

            scope = InvocationScope(
                invocation=invocation,
                credentials={lease.audience: lease.value for lease in leases},
                grant=grant,
            )
            token = _INVOCATION_SCOPE.set(scope)
            try:
                result = await self._dispatch(
                    name,
                    prepared.typed_arguments,
                    ctx,
                    tool,
                    managed,
                    invocation,
                    policy,
                )
            finally:
                _INVOCATION_SCOPE.reset(token)
            try:
                safe_result = _apply_result_policy(
                    result,
                    managed,
                    environment_projector=_resolve_environment_result_projector(ctx),
                )
            except ToolFailed:
                await _emit(ctx, managed, "result_rejected", invocation_id=invocation.invocation_id)
                raise
            await _emit(ctx, managed, "completed", invocation_id=invocation.invocation_id)
            return safe_result
        finally:
            await lease_stack.aclose()

    async def _dispatch(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentContext],
        tool: ToolsetTool[AgentContext],
        metadata: HarnessToolMetadata,
        invocation: ToolInvocationContext,
        policy: InvocationPolicyCapability,
    ) -> Any:
        replay_safe = metadata.idempotency in {"read_only", "provider_key"}
        attempts = 1 + (policy.max_dispatch_retries if replay_safe else 0)
        for attempt in range(attempts):
            try:
                await _emit(
                    ctx,
                    metadata,
                    "dispatching",
                    invocation_id=invocation.invocation_id,
                    attempt=attempt,
                )
                return await self.wrapped.call_tool(name, deepcopy(tool_args), ctx, tool)
            except asyncio.CancelledError:
                await _emit_best_effort(
                    ctx,
                    metadata,
                    "unknown_outcome",
                    invocation_id=invocation.invocation_id,
                )
                raise
            except TimeoutError as exc:
                await _emit(ctx, metadata, "unknown_outcome", invocation_id=invocation.invocation_id)
                raise ToolFailed("Managed tool outcome is unknown and requires reconciliation.") from exc
            except ManagedToolProviderError as exc:
                if not exc.outcome_known:
                    await _emit(ctx, metadata, "unknown_outcome", invocation_id=invocation.invocation_id)
                    raise ToolFailed("Managed tool outcome is unknown and requires reconciliation.") from exc
                if exc.retryable and replay_safe and attempt + 1 < attempts:
                    await _emit(
                        ctx,
                        metadata,
                        "retrying",
                        invocation_id=invocation.invocation_id,
                        attempt=attempt + 1,
                    )
                    continue
                await _emit(ctx, metadata, "provider_failed", invocation_id=invocation.invocation_id)
                raise ToolFailed("Managed tool provider failed.") from exc
        raise AssertionError("unreachable")


async def _require_policy_allow(
    ctx: RunContext[AgentContext],
    policy: InvocationPolicyCapability,
    invocation: ToolInvocationContext,
    metadata: HarnessToolMetadata,
    *,
    approval_satisfied: bool,
) -> None:
    decision = await policy.evaluator(invocation, metadata, context=ctx.deps)
    if not isinstance(decision, InvocationPolicyDecision):
        raise DefinitionError("Invocation policy returned an invalid decision.", code="invocation_policy_invalid")
    if decision.decision == "deny":
        await _emit(ctx, metadata, "denied", invocation_id=invocation.invocation_id)
        raise ToolFailed("Managed tool invocation was denied.")
    if decision.decision == "approval_required" and not approval_satisfied:
        await _emit(ctx, metadata, "approval_required", invocation_id=invocation.invocation_id)
        raise ApprovalRequired(metadata=dict(decision.approval_metadata))


def _validate_finalized_capability_provenance(ctx: RunContext[AgentContext]) -> None:
    """Reject protected Capability replacement after native for_run finalization."""
    from converge_agent_harness.environment.tools import (
        ENVIRONMENT_TOOLS_CAPABILITY_ID,
        EnvironmentToolsCapability,
        _EnvironmentToolsRunCapability,
    )
    from converge_agent_harness.tools.client import (
        CLIENT_TOOLS_CAPABILITY_ID,
        CLIENT_TOOLS_RUN_CAPABILITY_ID,
        ClientToolsCapability,
        ClientToolsRunCapability,
    )

    provenance = ctx.deps._capability_provenance
    expected = {
        INVOCATION_AUTHORIZATION_CAPABILITY_ID: (
            (InvocationAuthorizationCapability,),
            None,
        ),
        INVOCATION_POLICY_CAPABILITY_ID: (
            (InvocationPolicyCapability,),
            provenance.run_ids,
        ),
        CLIENT_TOOLS_CAPABILITY_ID: (
            (ClientToolsCapability,),
            provenance.definition_ids,
        ),
        CLIENT_TOOLS_RUN_CAPABILITY_ID: (
            (ClientToolsRunCapability,),
            provenance.run_ids,
        ),
        ENVIRONMENT_TOOLS_CAPABILITY_ID: (
            (EnvironmentToolsCapability, _EnvironmentToolsRunCapability),
            provenance.definition_ids,
        ),
    }
    reserved_types = tuple(capability_type for item in expected.values() for capability_type in item[0])
    for capability_id, capability in ctx.capabilities.items():
        if isinstance(capability, reserved_types) and capability_id not in expected:
            raise DefinitionError(
                "A protected Harness Capability changed its reserved ID during run binding.",
                code="capability_scope_invalid",
                details={
                    "capability_id": capability_id,
                    "capability_type": type(capability).__name__,
                    "source": "run_finalized",
                },
            )

    for capability_id, (expected_types, allowed_ids) in expected.items():
        capability = ctx.capabilities.get(capability_id)
        if capability_id == INVOCATION_AUTHORIZATION_CAPABILITY_ID:
            if type(capability) is not InvocationAuthorizationCapability:
                raise DefinitionError(
                    "The finalized run is missing its exact mandatory invocation Capability.",
                    code="capability_scope_invalid",
                    details={"capability_id": capability_id, "source": "run_finalized"},
                )
            continue
        if capability is None:
            if allowed_ids is not None and capability_id in allowed_ids:
                raise DefinitionError(
                    "A protected Harness Capability disappeared during run binding.",
                    code="capability_scope_invalid",
                    details={"capability_id": capability_id, "source": "run_finalized"},
                )
            continue
        if type(capability) not in expected_types or allowed_ids is None or capability_id not in allowed_ids:
            raise DefinitionError(
                "A protected Harness Capability changed type or source during run binding.",
                code="capability_scope_invalid",
                details={
                    "capability_id": capability_id,
                    "capability_type": type(capability).__name__,
                    "source": "run_finalized",
                },
            )


def _resolve_policy(ctx: RunContext[AgentContext]) -> InvocationPolicyCapability | None:
    value = ctx.capabilities.get(INVOCATION_POLICY_CAPABILITY_ID)
    if value is None:
        return None
    if type(value) is not InvocationPolicyCapability:
        raise DefinitionError(
            "The invocation policy Capability changed protected type during run binding.",
            code="capability_scope_invalid",
        )
    if INVOCATION_POLICY_CAPABILITY_ID not in ctx.deps._capability_provenance.run_ids:
        raise DefinitionError(
            "InvocationPolicyCapability must originate from RunBindings.",
            code="capability_scope_invalid",
        )
    return value


async def _prepare_invocation(
    ctx: RunContext[AgentContext],
    tool_name: str,
    toolset_id: str | None,
    tool_args: dict[str, Any],
    metadata: HarnessToolMetadata,
) -> _PreparedInvocation:
    try:
        typed_arguments = deepcopy(tool_args)
        require_finite_json(typed_arguments)
        projected = _ANY_ADAPTER.dump_python(typed_arguments, mode="json", warnings="error")
        normalized = _JSON_OBJECT_ADAPTER.validate_python(projected, strict=True)
        encoded = dump_json_bytes(normalized, sort_keys=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ToolFailed("Managed tool arguments cannot be represented safely.") from exc
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise ToolFailed("Managed tool arguments exceed the supported size.")
    digest = hashlib.sha256(encoded).hexdigest()

    resources: tuple[CanonicalResource, ...] = ()
    if metadata.resource_resolver is not None:
        try:
            resolved = await metadata.resource_resolver(deepcopy(typed_arguments), context=ctx.deps)
        except Exception as exc:
            raise ToolFailed("Managed tool resources could not be resolved.") from exc
        if not isinstance(resolved, tuple) or not all(isinstance(item, CanonicalResource) for item in resolved):
            raise DefinitionError(
                "Managed resource resolver returned an invalid value.",
                code="resource_resolution_invalid",
            )
        resources = tuple(item.model_copy(deep=True) for item in resolved)

    tool_call_id = ctx.tool_call_id or f"programmatic:{uuid4()}"
    idempotency_key = None
    if metadata.idempotency in {"read_only", "provider_key"}:
        instance = ctx.deps.instance
        key_source = (
            f"{instance.identity.issuer}:{instance.identity.subject}:"
            f"{instance.agent_instance_id}:{tool_call_id}:{metadata.tool_id}:{digest}"
        ).encode()
        idempotency_key = hashlib.sha256(key_source).hexdigest()
    prepared_tool = ctx.tools.get(tool_name)
    timeout = prepared_tool.timeout if prepared_tool is not None else None
    deadline = datetime.now(UTC) + timedelta(seconds=timeout) if timeout is not None else None
    return _PreparedInvocation(
        context=ToolInvocationContext(
            invocation_id=str(uuid4()),
            tool_call_id=tool_call_id,
            run_id=ctx.deps.run_id,
            instance=ctx.deps.instance,
            tool_id=metadata.tool_id,
            toolset_id=toolset_id,
            tool_name=tool_name,
            normalized_arguments=normalized,
            arguments_digest=digest,
            resources=resources,
            idempotency_key=idempotency_key,
            deadline=deadline,
        ),
        typed_arguments=typed_arguments,
    )


def _apply_result_policy(
    result: Any,
    metadata: HarnessToolMetadata,
    *,
    environment_projector: Any | None = None,
) -> JsonValue:
    from converge_agent_harness.environment.models import EnvironmentError
    from converge_agent_harness.environment.tools import _EnvironmentRetainedToolResult

    policy = metadata.output_policy
    retained = result if isinstance(result, _EnvironmentRetainedToolResult) else None
    candidate = retained.value if retained is not None else result
    try:
        value = _project_json_result(candidate)
        limit = policy.max_inline_bytes if policy.overflow == "fail" else policy.max_output_bytes
        captured, complete = _capture_json(value, limit=limit, redact=policy.redact)
    except (RecursionError, TypeError, ValueError, ValidationError) as exc:
        raise ToolFailed("Managed tool returned an invalid result.") from exc

    if complete and len(captured) <= policy.max_inline_bytes:
        return redact_json(value) if policy.redact else deepcopy(value)
    if policy.overflow == "fail":
        raise ToolFailed("Managed tool result exceeded its output limit.")

    compact_reference: str | None = None
    if policy.overflow == "environment_reference" and retained is not None and environment_projector is not None:
        try:
            _, compact_reference = environment_projector.project_retained_result(retained)
        except EnvironmentError:
            # A live Environment may legitimately have no compatible retained-output
            # sink after a topology change or reference-table exhaustion. Preserve the
            # bounded no-reference fallback rather than turning completed work into a
            # retry-shaped tool failure.
            compact_reference = None
        except DefinitionError:
            raise
        except Exception as exc:
            raise ToolFailed("Managed tool retained output could not be projected.") from exc

    fallback: dict[str, JsonValue] = {
        "content": captured[: policy.max_inline_bytes].decode("utf-8", errors="ignore"),
        "complete": False,
        "truncated": True,
        "captured_bytes": len(captured),
        "reference": compact_reference,
    }
    if complete:
        fallback["dropped_bytes"] = 0
    return fallback


def _resolve_environment_result_projector(ctx: RunContext[AgentContext]) -> Any | None:
    from converge_agent_harness.environment.tools import _resolve_environment_result_projector as resolve

    return resolve(ctx)


def _project_json_result(result: Any) -> JsonValue:
    if not _is_native_json(result):
        raise TypeError("Managed tool results must be native JSON values")
    return cast(JsonValue, result)


def _is_native_json(value: Any, active: set[int] | None = None) -> bool:
    if value is None or isinstance(value, str | bool | int):
        return True
    if isinstance(value, float):
        require_finite_json(value)
        return True
    if not isinstance(value, list | dict):
        return False

    active = active if active is not None else set()
    value_id = id(value)
    if value_id in active:
        raise ValueError("JSON values cannot contain cycles")
    active.add(value_id)
    try:
        if isinstance(value, list):
            return all(_is_native_json(item, active) for item in value)
        return all(isinstance(key, str) and _is_native_json(item, active) for key, item in value.items())
    finally:
        active.remove(value_id)


def _capture_json(value: JsonValue, *, limit: int, redact: bool) -> tuple[bytes, bool]:
    captured = bytearray()
    for chunk in _iter_json(value, redact=redact):
        remaining = limit - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > remaining:
            return bytes(captured), False
    return bytes(captured), True


def _iter_json(value: JsonValue, *, redact: bool) -> Iterator[bytes]:
    if value is None:
        yield b"null"
    elif value is True:
        yield b"true"
    elif value is False:
        yield b"false"
    elif isinstance(value, int | float):
        yield json.dumps(value, allow_nan=False).encode("ascii")
    elif isinstance(value, str):
        yield from _iter_json_string(redact_bearer(value) if redact else value)
    elif isinstance(value, list):
        yield b"["
        for index, item in enumerate(value):
            if index:
                yield b","
            yield from _iter_json(item, redact=redact)
        yield b"]"
    else:
        yield b"{"
        for index, (key, item) in enumerate(value.items()):
            if index:
                yield b","
            yield from _iter_json_string(key)
            yield b":"
            if redact and is_sensitive_key(key):
                yield b'"[REDACTED]"'
            else:
                yield from _iter_json(item, redact=redact)
        yield b"}"


def _iter_json_string(value: str, *, chunk_size: int = 4096) -> Iterator[bytes]:
    yield b'"'
    for offset in range(0, len(value), chunk_size):
        encoded = json.dumps(value[offset : offset + chunk_size], ensure_ascii=False)[1:-1]
        yield encoded.encode("utf-8")
    yield b'"'


async def _emit(
    ctx: RunContext[AgentContext],
    metadata: HarnessToolMetadata,
    phase: str,
    **fields: JsonValue,
) -> None:
    payload: dict[str, JsonValue] = {"phase": phase, "tool_id": metadata.tool_id, "tool_name": ctx.tool_name}
    payload.update(fields)
    await ctx.deps.events.emit(HarnessExtensionEvent(kind="invocation", payload=payload))


async def _emit_best_effort(
    ctx: RunContext[AgentContext],
    metadata: HarnessToolMetadata,
    phase: str,
    **fields: JsonValue,
) -> None:
    try:
        await asyncio.shield(_emit(ctx, metadata, phase, **fields))
    except BaseException:
        pass


def _validate_client_run_attachment(ctx: RunContext[AgentContext]) -> None:
    from converge_agent_harness.tools.client import (
        CLIENT_TOOLS_CAPABILITY_ID,
        CLIENT_TOOLS_RUN_CAPABILITY_ID,
        ClientToolsCapability,
        ClientToolsRunCapability,
    )

    provenance = ctx.deps._capability_provenance
    owner = ctx.capabilities.get(CLIENT_TOOLS_CAPABILITY_ID)
    if owner is not None and type(owner) is not ClientToolsCapability:
        raise DefinitionError(
            "The client-tools Capability has an incompatible type.",
            code="client_tools_type_mismatch",
        )
    if owner is not None and CLIENT_TOOLS_CAPABILITY_ID not in provenance.definition_ids:
        raise DefinitionError(
            "ClientToolsCapability must originate from the Agent definition.",
            code="capability_scope_invalid",
        )

    attachment = ctx.capabilities.get(CLIENT_TOOLS_RUN_CAPABILITY_ID)
    if attachment is not None and type(attachment) is not ClientToolsRunCapability:
        raise DefinitionError(
            "The client-tools run Capability has an incompatible type.",
            code="client_tools_run_type_mismatch",
        )
    if attachment is not None and CLIENT_TOOLS_RUN_CAPABILITY_ID not in provenance.run_ids:
        raise DefinitionError(
            "ClientToolsRunCapability must originate from RunBindings.",
            code="capability_scope_invalid",
        )
    if attachment is not None and owner is None:
        raise DefinitionError(
            "A client-tools run attachment requires ClientToolsCapability in the Agent definition.",
            code="client_tools_owner_missing",
        )


def _validate_client_marker(tool_name: str, metadata: Mapping[str, Any]) -> None:
    from converge_agent_harness.tools.client import CLIENT_TOOL_MARKER_KEY

    marker = metadata.get(CLIENT_TOOL_MARKER_KEY)
    if marker is None:
        return
    if not isinstance(marker, Mapping) or marker.get("declared_name") != tool_name:
        raise DefinitionError(
            "A client tool was renamed after its declaration was materialized.",
            code="client_tool_name_changed",
            details={"tool_name": tool_name},
        )


def _validate_resume_surface(
    ctx: RunContext[AgentContext],
    tools: Mapping[str, ToolsetTool[AgentContext]],
) -> None:
    resume = ctx.deps.deferred_resume
    if resume is None:
        return
    required_names = {call.tool_name for call in resume.requests.calls}
    for name in required_names:
        tool = tools.get(name)
        if tool is None or tool.tool_def.kind != "external":
            raise DefinitionError(
                "The current tool surface does not match the pending external call.",
                code="deferred_surface_mismatch",
                details={"tool_name": name},
            )

    for request in resume.requests.approvals:
        expected_tool_id = managed_approval_tool_id(resume.requests, request.tool_call_id)
        if expected_tool_id is None:
            continue
        tool = tools.get(request.tool_name)
        raw_metadata = (
            (tool.tool_def.metadata or {}).get(HARNESS_TOOL_METADATA_KEY)
            if tool is not None and tool.tool_def.kind in {"function", "unapproved"}
            else None
        )
        if raw_metadata is None:
            raise DefinitionError(
                "The current tool surface does not match the pending managed approval.",
                code="deferred_surface_mismatch",
                details={"tool_name": request.tool_name},
            )
        current = normalize_harness_tool_metadata(raw_metadata)
        if current.tool_id != expected_tool_id:
            raise DefinitionError(
                "The current managed tool identity does not match the pending approval.",
                code="deferred_surface_mismatch",
                details={"tool_name": request.tool_name},
            )
