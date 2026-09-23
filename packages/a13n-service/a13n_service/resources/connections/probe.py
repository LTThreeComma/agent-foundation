"""Saved Connection discovery; no tool invocation or transaction across MCP I/O."""

import asyncio

import httpx2
from a13n_harness.providers.catalog import ProviderCatalog, ProviderNotSelected
from a13n_harness.providers.connector import ConnectorProviderDefinition
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from redis.asyncio import Redis

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.providers.tools import (
    INITIALIZATION_SECONDS,
    MAX_TOOLS,
    RESPONSE_BYTES,
    ConnectionProvider,
    ToolSourceDefinition,
)
from a13n_service.resources.connections import cache, managed_access, oauth_access
from a13n_service.resources.connections.schemas import ConnectionTest
from a13n_service.resources.connections.service import authentication_headers, get_row, resolve
from a13n_service.settings import OAuth
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
    catalog: ProviderCatalog[ConnectionProvider],
    oauth_settings: OAuth,
) -> ConnectionTest:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write" if refresh else "read")
        selected = await resolve(session, actor, scope, connection_id, verb="read")
    if selected.auth == "managed":
        definition = catalog.require(selected.type)
        assert isinstance(definition, ConnectorProviderDefinition)
        return await managed_access.discover(
            storage,
            actor,
            selected,
            keys=keys,
            policy=policy,
            definition=definition,
        )
    oauth_token = (
        await oauth_access.access(storage, actor, selected, keys=keys, policy=policy, settings=oauth_settings)
        if selected.auth == "oauth"
        else None
    )
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
        if oauth_token is not None:
            await oauth_access.check_session(storage, oauth_token, principal_id=actor.id, connection_id=selected.id)
            request.headers["authorization"] = "Bearer " + oauth_token.token
        else:
            request.headers.update(authentication_headers(selected, keys))

    async def check_response(response: httpx2.Response) -> None:
        if oauth_token is not None and response.status_code in {401, 403}:
            await oauth_access.rejected(storage, oauth_token)
            raise oauth_access.required("access_token_rejected")

    try:
        async with (
            asyncio.timeout(INITIALIZATION_SECONDS),
            open_http(
                policy,
                timeout=INITIALIZATION_SECONDS,
                max_bytes=RESPONSE_BYTES,
                before_request=authorize_request,
                after_response=check_response,
            ) as client,
        ):
            assert isinstance(definition, ToolSourceDefinition)
            source = definition.bind(selected.config.model_dump(mode="json"), source_id=selected.id, client=client)
            tools = await source.discover()
            if len(tools) > MAX_TOOLS:
                raise ServiceError("payload_too_large", "Connection advertises too many tools")
    except ServiceError:
        raise
    except Exception:
        if oauth_token is not None:
            await oauth_access.check_session(storage, oauth_token, principal_id=actor.id, connection_id=selected.id)
        raise ServiceError("unavailable", "MCP discovery failed; check endpoint and authentication") from None
    async with short_session(storage) as session:
        current = await get_row(session, selected.workspace_id, selected.id)
        if current.version != selected.version or not current.enabled:
            raise ServiceError("conflict", "Connection changed during its test")
    result = ConnectionTest(connection_id=selected.id, version=selected.version, tools=tools)
    await cache.write(redis, selected, result)
    return result
