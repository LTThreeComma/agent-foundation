"""Native managed actions with the same live run and private-account fences as discovery."""

import hashlib
from contextlib import AsyncExitStack
from typing import Any

from a13n_harness import AgentContext
from a13n_harness.providers.connector import ConnectorProviderDefinition
from a13n_harness.providers.connector.contracts import ConnectionBinding, ConnectorTool
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Toolset
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import FunctionToolset

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.composio import ComposioConfig
from a13n_service.providers.tools import ToolsetWrapper
from a13n_service.resources.connections import managed_access
from a13n_service.resources.connections.managed import check_inspection
from a13n_service.resources.connections.managed_transport import actions, open_provider
from a13n_service.resources.connections.service import ResolvedConnection
from a13n_service.tenancy.authorize import Principal


async def open_actions(
    stack: AsyncExitStack,
    storage: Storage,
    selected: ResolvedConnection,
    principal: Principal,
    *,
    definition: ConnectorProviderDefinition,
    keys: KeyRing,
    policy: EndpointPolicy,
    current,
    wrapper: ToolsetWrapper,
    run_id: str,
) -> Toolset[AgentContext]:
    config = selected.config
    assert isinstance(config, ComposioConfig)
    owned = await managed_access.access(storage, principal, selected, keys=keys)
    allowed: set[str] = set()
    dispatched: set[str] = set()

    async def before(request):
        active, _, _ = await current()
        if active.version != selected.version:
            raise ServiceError("disabled", "Connection changed during execution; use a fresh run")
        await managed_access.check(storage, owned, selected)
        if request.method == "POST":
            operation = request.headers.get("idempotency-key")
            if operation not in allowed or operation in dispatched or len(dispatched) >= 1000:
                raise ServiceError("conflict", "Managed transport attempted an untracked or repeated action")
            dispatched.add(operation)

    provider = await stack.enter_async_context(
        open_provider(
            selected,
            definition,
            keys=keys,
            policy=policy,
            before_request=before,
        )
    )
    account = provider.connect(
        ConnectionBinding(
            connector_key=config.app,
            external_ref=owned.bundle["account_id"],
            external_user_correlation=owned.bundle["user_id"],
        )
    )
    check_inspection(config, owned.bundle, await account.inspect(), active=True)
    definitions = await actions(provider, config)

    def tool(definition: ConnectorTool) -> Tool[AgentContext]:
        async def call(ctx: RunContext[AgentContext], **arguments: Any):
            if ctx.tool_call_id is None:
                raise ServiceError("conflict", "Managed action has no original call identity")
            check_inspection(config, owned.bundle, await account.inspect(), active=True)
            operation = hashlib.sha256(f"{run_id}:{selected.id}:{ctx.tool_call_id}".encode()).hexdigest()
            allowed.add(operation)
            result = await account.execute_tool(
                tool_key=definition.key,
                provider_version=config.toolkit_version,
                arguments=arguments,
                request_id=operation,
            )
            if result.kind != "succeeded":
                raise ToolFailed("The action returned no usable result. Check external state before another call.")
            return result.result

        return Tool.from_schema(
            call,
            name=definition.key,
            description=definition.description,
            json_schema=definition.input_schema,
            takes_ctx=True,
        )

    tools = FunctionToolset[AgentContext](tools=[tool(item) for item in definitions], max_retries=0, id=selected.id)
    return Toolset(wrapper(tools))
