"""Attempt-owned Connection transports and host-visible tool dispatch."""

import asyncio
import hashlib
import json
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from typing import Any

import httpx2
from a13n_harness import AgentContext
from a13n_harness.providers.catalog import ProviderCatalog, ProviderNotSelected
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.tools.metadata import RECOVERY_RETRY_SAFE_METADATA_KEY
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset
from redis.asyncio import Redis

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.providers.tools import (
    CALL_SECONDS,
    INITIALIZATION_SECONDS,
    MAX_TOOLS,
    RESPONSE_BYTES,
    ToolInfo,
    ToolSourceDefinition,
)
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.connections import cache
from a13n_service.resources.connections.schemas import ConnectionSelection, ConnectionTest
from a13n_service.resources.connections.scope import check_collisions, connection_scope, validate_tools
from a13n_service.resources.connections.service import ResolvedConnection, authentication_headers, resolve
from a13n_service.runs.attempts import lock_authority
from a13n_service.runs.policy import CallCheck, authorize_execution
from a13n_service.runs.schemas import AttemptClaim, RunOptions
from a13n_service.tenancy.authorize import ExecutionAuthority, Scope


@dataclass
class ConnectionTools(WrapperToolset[AgentContext]):
    connection: ResolvedConnection
    selection: ConnectionSelection
    check: CallCheck
    redis: Redis

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        async with asyncio.timeout(INITIALIZATION_SECONDS):
            tools = await self.wrapped.get_tools(ctx)
        if len(tools) > MAX_TOOLS:
            raise ServiceError("payload_too_large", "Connection advertises too many tools")
        if not set(self.selection.tools) <= tools.keys():
            raise ServiceError("disabled", "Connection no longer advertises an Agent tool")
        await cache.write(
            self.redis,
            self.connection,
            ConnectionTest(
                connection_id=self.connection.id,
                version=self.connection.version,
                tools=[
                    ToolInfo(
                        name=name,
                        description=tool.tool_def.description,
                        input_schema=tool.tool_def.parameters_json_schema,
                    )
                    for name, tool in tools.items()
                ],
            ),
        )
        return {
            name: replace(
                tool,
                tool_def=replace(
                    tool.tool_def,
                    metadata={
                        **(tool.tool_def.metadata or {}),
                        RECOVERY_RETRY_SAFE_METADATA_KEY: name in self.connection.config.recovery_retry_safe_tools,
                    },
                ),
            )
            for name, tool in tools.items()
            if name in self.selection.tools
        }

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        if ctx.tool_call_id is None:
            raise ServiceError("conflict", "MCP tool call has no original identity")
        await self.check.check_tool(
            self.connection, self.selection, name=name, call_id=ctx.tool_call_id, harness_run_id=ctx.deps.run_id
        )
        try:
            async with asyncio.timeout(CALL_SECONDS):
                return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        except ServiceError:
            raise
        except Exception:
            raise ToolFailed(
                "The MCP call returned no usable result. Its effects may have occurred; check external state before another call."
            ) from None


def tool_alias(connection_id: str, name: str) -> str:
    suffix = hashlib.sha256(f"{connection_id}:{name}".encode()).hexdigest()[:16]
    label = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:32]
    return f"{label}_{suffix}"


async def open_connections(
    stack: AsyncExitStack,
    storage: Storage,
    claim: AttemptClaim,
    config: AgentConfig,
    options: RunOptions,
    *,
    redis: Redis,
    keys: KeyRing,
    policy: EndpointPolicy,
    catalog: ProviderCatalog[ToolSourceDefinition],
    check: CallCheck,
) -> list[AbstractCapability[AgentContext]]:
    capabilities: list[AbstractCapability[AgentContext]] = []
    for selection in connection_scope(config).values():
        capabilities.append(
            await _open_connection(
                stack,
                storage,
                claim,
                selection,
                options,
                redis=redis,
                keys=keys,
                policy=policy,
                catalog=catalog,
                check=check,
            )
        )
    return capabilities


async def _open_connection(
    stack: AsyncExitStack,
    storage: Storage,
    claim: AttemptClaim,
    selection: ConnectionSelection,
    options: RunOptions,
    *,
    redis: Redis,
    keys: KeyRing,
    policy: EndpointPolicy,
    catalog: ProviderCatalog[ToolSourceDefinition],
    check: CallCheck,
) -> AbstractCapability[AgentContext]:
    async def current() -> ResolvedConnection:
        async with transaction(storage) as session:
            run, _, _ = await lock_authority(session, claim)
            principal = await authorize_execution(session, run)
            selected = await resolve(
                session,
                principal,
                Scope(run.organization_id, run.workspace_id),
                selection.connection_id,
                verb="run",
                authority=ExecutionAuthority.model_validate(run.authority),
            )
            validate_tools(selected, selection)
        return selected

    selected = await current()
    context_headers = dict(options.mcp_headers.get(selected.id, {}))
    check_collisions(selected, context_headers, keys)
    try:
        definition = catalog.require(selected.type)
    except ProviderNotSelected:
        raise ServiceError("unavailable", "Connection provider is unavailable") from None
    dispatched: set[str] = set()

    async def before_request(request: httpx2.Request) -> None:
        # Closing an already-owned remote session needs no new execution authority.
        active = selected if request.method == "DELETE" else await current()
        check_collisions(active, context_headers, keys)
        if active.version != selected.version:
            raise ServiceError("disabled", "Connection changed during execution; use a fresh run")
        request.headers.update(authentication_headers(active, keys))
        if request.method == "POST":
            value = json.loads(await request.aread())
            if value.get("method") == "tools/call":
                correlation = value.get("params", {}).get("_meta", {}).get("a13n.service", {})
                operation = correlation.get("operation_id")
                if not isinstance(operation, str) or operation in dispatched or len(dispatched) >= 1000:
                    raise ServiceError("conflict", "MCP transport attempted an untracked or repeated dispatch")
                dispatched.add(operation)

    client = await stack.enter_async_context(
        open_http(policy, timeout=CALL_SECONDS, max_bytes=RESPONSE_BYTES, before_request=before_request)
    )
    source = definition.bind(selected.config.model_dump(mode="json"), source_id=selected.id, client=client)

    def wrapper(tools: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        bound = ConnectionTools(tools, selected, selection, check, redis)
        return bound.renamed({tool_alias(selected.id, name): name for name in selection.tools})

    return source.open(lambda context: context_headers, wrapper, run_id=claim.run_id)
