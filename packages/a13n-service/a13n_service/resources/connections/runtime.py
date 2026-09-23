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
from a13n_harness.providers.connector import ConnectorProviderDefinition
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
    ConnectionProvider,
    ToolInfo,
    ToolSourceDefinition,
)
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.connections import cache, oauth_access
from a13n_service.resources.connections.schemas import ConnectionSelection, ConnectionTest, recovery_tools
from a13n_service.resources.connections.scope import check_collisions, connection_scope, validate_tools
from a13n_service.resources.connections.service import ResolvedConnection, authentication_headers, resolve
from a13n_service.runs.attempts import lock_authority
from a13n_service.runs.policy import CallCheck, authorize_execution
from a13n_service.runs.schemas import AttemptClaim, RunOptions
from a13n_service.settings import OAuth
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Scope


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
                        RECOVERY_RETRY_SAFE_METADATA_KEY: name in recovery_tools(self.connection.config),
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
            raise ServiceError("conflict", "Tool call has no original identity")
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
                "The tool call returned no usable result. Its effects may have occurred; check external state before another call."
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
    catalog: ProviderCatalog[ConnectionProvider],
    check: CallCheck,
    oauth_settings: OAuth,
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
                oauth_settings=oauth_settings,
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
    catalog: ProviderCatalog[ConnectionProvider],
    check: CallCheck,
    oauth_settings: OAuth,
) -> AbstractCapability[AgentContext]:
    async def current() -> tuple[ResolvedConnection, Principal, ExecutionAuthority]:
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
        return selected, principal, ExecutionAuthority.model_validate(run.authority)

    selected, principal, authority = await current()

    def wrapper(tools: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        bound = ConnectionTools(tools, selected, selection, check, redis)
        return bound.renamed({tool_alias(selected.id, name): name for name in selection.tools})

    if selected.auth == "managed":
        from a13n_service.resources.connections.managed_runtime import open_actions

        definition = catalog.require(selected.type)
        assert isinstance(definition, ConnectorProviderDefinition)
        return await open_actions(
            stack,
            storage,
            selected,
            principal,
            definition=definition,
            keys=keys,
            policy=policy,
            current=current,
            wrapper=wrapper,
            run_id=claim.run_id,
        )
    oauth_token = (
        await oauth_access.access(
            storage, principal, selected, keys=keys, policy=policy, settings=oauth_settings, authority=authority
        )
        if selected.auth == "oauth"
        else None
    )
    context_headers = dict(options.mcp_headers.get(selected.id, {}))
    check_collisions(selected, context_headers, keys)
    try:
        definition = catalog.require(selected.type)
    except ProviderNotSelected:
        raise ServiceError("unavailable", "Connection provider is unavailable") from None
    dispatched: set[str] = set()

    async def before_request(request: httpx2.Request) -> None:
        # Closing an already-owned remote session needs no new execution authority.
        active = selected if request.method == "DELETE" else (await current())[0]
        check_collisions(active, context_headers, keys)
        if active.version != selected.version:
            raise ServiceError("disabled", "Connection changed during execution; use a fresh run")
        if oauth_token is not None:
            if request.method != "DELETE":
                await oauth_access.check_session(
                    storage, oauth_token, principal_id=principal.id, connection_id=selected.id
                )
            request.headers["authorization"] = "Bearer " + oauth_token.token
        else:
            request.headers.update(authentication_headers(active, keys))
        if request.method == "POST":
            value = json.loads(await request.aread())
            if value.get("method") == "tools/call":
                correlation = value.get("params", {}).get("_meta", {}).get("a13n.service", {})
                operation = correlation.get("operation_id")
                if not isinstance(operation, str) or operation in dispatched or len(dispatched) >= 1000:
                    raise ServiceError("conflict", "MCP transport attempted an untracked or repeated dispatch")
                dispatched.add(operation)

    async def check_response(response: httpx2.Response) -> None:
        if oauth_token is not None and response.status_code in {401, 403}:
            await oauth_access.rejected(storage, oauth_token)
            raise oauth_access.required("access_token_rejected")

    client = await stack.enter_async_context(
        open_http(
            policy,
            timeout=CALL_SECONDS,
            max_bytes=RESPONSE_BYTES,
            before_request=before_request,
            after_response=check_response,
        )
    )
    assert isinstance(definition, ToolSourceDefinition)
    source = definition.bind(selected.config.model_dump(mode="json"), source_id=selected.id, client=client)

    return source.open(lambda context: context_headers, wrapper, run_id=claim.run_id)
