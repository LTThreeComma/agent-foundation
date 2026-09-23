"""Native managed Connector I/O under Service endpoint and response limits."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx2
from a13n_harness.providers.connector import ConnectorProviderDefinition
from a13n_harness.providers.connector.bounds import DISCOVERY_MAX_PAGES
from a13n_harness.providers.connector.contracts import ConnectorProviderRuntime, ConnectorTool
from a13n_harness.providers.connector.directory import DirectoryBudget
from a13n_harness.providers.connector.http import ConnectorHttpClient
from a13n_harness.providers.endpoint_policy import EndpointPolicy

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.providers.composio import ComposioConfig
from a13n_service.providers.tools import INITIALIZATION_SECONDS, MAX_TOOLS, RESPONSE_BYTES
from a13n_service.resources.connections.service import ResolvedConnection


@asynccontextmanager
async def open_provider(
    selected: ResolvedConnection,
    definition: ConnectorProviderDefinition,
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    before_request: Callable[[httpx2.Request], Awaitable[None]],
) -> AsyncIterator[ConnectorProviderRuntime]:
    credential = project_credential(selected, keys)
    async with open_project(credential, definition, policy=policy, before_request=before_request) as provider:
        yield provider


def project_credential(selected: ResolvedConnection, keys: KeyRing) -> dict:
    if selected.auth != "managed" or selected.credential is None:
        raise ServiceError("disabled", "Managed project credential is not configured")
    return json.loads(
        keys.reveal(
            Envelope.model_validate(selected.credential),
            SecretLocation(selected.organization_id, "connections", "credential", selected.id),
        )
    )


@asynccontextmanager
async def open_project(
    credential: dict,
    definition: ConnectorProviderDefinition,
    *,
    policy: EndpointPolicy,
    before_request: Callable[[httpx2.Request], Awaitable[None]],
) -> AsyncIterator[ConnectorProviderRuntime]:
    async with open_http(
        policy, timeout=INITIALIZATION_SECONDS, max_bytes=RESPONSE_BYTES, before_request=before_request
    ) as client:
        http = ConnectorHttpClient(
            client, policy, response_max_bytes=RESPONSE_BYTES, timeout_seconds=INITIALIZATION_SECONDS
        )
        async with definition.open({}, credential, http=http) as provider:
            yield provider


async def tool_definitions(provider: ConnectorProviderRuntime, app: str, version: str) -> tuple[ConnectorTool, ...]:
    catalog = provider.tool_catalog(app, provider_version=version)
    found: dict[str, ConnectorTool] = {}
    cursor = None
    seen: set[str] = set()
    budget = DirectoryBudget()
    for _ in range(DISCOVERY_MAX_PAGES):
        async with asyncio.timeout_at(budget.deadline):
            page = await catalog.discover_tools(cursor=cursor)
        budget.record(page.model_dump(mode="json"), len(page.items))
        if page.provider_version != version:
            raise ServiceError("conflict", "Managed action version changed")
        for tool in page.items:
            if tool.key in found:
                raise ServiceError("unavailable", "Managed catalogue contains duplicate actions")
            found[tool.key] = tool
        cursor = page.next_cursor
        if cursor is None:
            break
        if cursor in seen:
            raise ServiceError("unavailable", "Managed catalogue cursor repeated")
        seen.add(cursor)
    else:
        raise ServiceError("payload_too_large", "Managed catalogue has too many pages")
    return tuple(found.values())


async def actions(provider: ConnectorProviderRuntime, config: ComposioConfig) -> tuple[ConnectorTool, ...]:
    found = {tool.key: tool for tool in await tool_definitions(provider, config.app, config.toolkit_version)}
    if len(config.actions) > MAX_TOOLS or not set(config.actions) <= found.keys():
        raise ServiceError("disabled", "A selected managed action is unavailable")
    return tuple(found[name] for name in config.actions)
