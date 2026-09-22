"""Saved Connection discovery; no tool invocation or transaction across MCP I/O."""

import asyncio

import httpx2
from a13n_harness.providers.catalog import ProviderCatalog, ProviderNotSelected
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from redis.asyncio import Redis

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.providers.tools import INITIALIZATION_SECONDS, MAX_TOOLS, RESPONSE_BYTES, ToolSourceDefinition
from a13n_service.resources.connections import cache
from a13n_service.resources.connections.schemas import ConnectionTest
from a13n_service.resources.connections.service import authentication_headers, get_row, resolve
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.grants import workspace_scope


async def test_connection(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    connection_id: str,
    *,
    redis: Redis,
    refresh: bool,
    keys: KeyRing,
    policy: EndpointPolicy,
    catalog: ProviderCatalog[ToolSourceDefinition],
) -> ConnectionTest:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write" if refresh else "read")
        selected = await resolve(session, actor, scope, connection_id, verb="read")
    if not refresh:
        cached = await cache.read(redis, selected)
        if cached is not None:
            return cached
    try:
        definition = catalog.require(selected.type)
    except ProviderNotSelected:
        raise ServiceError("unavailable", "Connection provider is unavailable") from None

    async def authorize_request(request: httpx2.Request) -> None:
        async with short_session(storage) as session:
            current = await get_row(session, selected.workspace_id, selected.id)
            if current.version != selected.version or not current.enabled:
                raise ServiceError("conflict", "Connection changed during its test")
        request.headers.update(authentication_headers(selected, keys))

    try:
        async with (
            asyncio.timeout(INITIALIZATION_SECONDS),
            open_http(
                policy, timeout=INITIALIZATION_SECONDS, max_bytes=RESPONSE_BYTES, before_request=authorize_request
            ) as client,
        ):
            source = definition.bind(selected.config.model_dump(mode="json"), source_id=selected.id, client=client)
            tools = await source.discover()
            if len(tools) > MAX_TOOLS:
                raise ServiceError("payload_too_large", "Connection advertises too many tools")
    except ServiceError:
        raise
    except Exception:
        raise ServiceError("unavailable", "MCP discovery failed; check endpoint and authentication") from None
    async with short_session(storage) as session:
        current = await get_row(session, selected.workspace_id, selected.id)
        if current.version != selected.version or not current.enabled:
            raise ServiceError("conflict", "Connection changed during its test")
    result = ConnectionTest(connection_id=selected.id, version=selected.version, tools=tools)
    await cache.write(redis, selected, result)
    return result
