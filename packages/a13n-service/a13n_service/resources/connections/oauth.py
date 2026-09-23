"""Principal-owned authorization initiation, callback and revocation."""

import asyncio
import base64
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from datetime import timedelta
from urllib.parse import urlencode

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbound import open_http
from a13n_service.providers.mcp import MCPConfig
from a13n_service.providers.oauth import discover
from a13n_service.resources.connections import oauth_tokens
from a13n_service.resources.connections.oauth_state import identity, invalidate, reveal, seal
from a13n_service.resources.connections.oauth_values import AuthorizationStart, AuthorizationView, BrowserFlow
from a13n_service.resources.connections.service import get_row, resolve
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow
from a13n_service.settings import OAuth
from a13n_service.tenancy.authenticate import secret_hash
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Scope, authorize, execution_authority
from a13n_service.tenancy.grants import principal_for, workspace_scope


def cookie_name(authorization_id: str, state: str) -> str:
    return "__Host-a13n_oauth_" + authorization_id + "_" + secret_hash(state)[:16]


async def status(storage: Storage, actor: Principal, workspace_id: str, connection_id: str) -> AuthorizationView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        await get_row(session, scope.workspace_id, connection_id)
        row = await session.scalar(
            select(ConnectionAuthorizationRow).where(
                ConnectionAuthorizationRow.connection_id == connection_id,
                ConnectionAuthorizationRow.principal_id == actor.id,
            )
        )
        if row is None:
            return AuthorizationView(
                id=None,
                connection_id=connection_id,
                status="not_authorized",
                expires_at=None,
                generation=0,
                operation_kind=None,
                failure=None,
            )
        return oauth_tokens.view(row)


async def start(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    connection_id: str,
    return_url: str,
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: OAuth,
) -> BrowserFlow:
    if settings.callback_url is None or return_url not in settings.return_urls:
        raise ServiceError("invalid_argument", "OAuth callback or return target is not configured")
    try:
        await policy.validate(settings.callback_url, resolve_dns=False)
        await policy.validate(return_url, resolve_dns=False)
    except ValueError:
        raise ServiceError("invalid_argument", "OAuth callback or return target is not permitted") from None
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        selected = await resolve(session, actor, scope, connection_id, verb="run")
        if selected.auth != "oauth" or not isinstance(selected.config, MCPConfig) or selected.config.oauth is None:
            raise ServiceError("invalid_argument", "Connection does not use OAuth")
        ceiling = execution_authority(actor, scope)
    try:
        async with asyncio.timeout(8), open_http(policy, timeout=5, max_bytes=65536) as client:
            endpoints = await discover(client, selected.config.url, selected.config.oauth)
        await policy.validate(endpoints.authorization_endpoint, resolve_dns=True)
        await policy.validate(endpoints.token_endpoint, resolve_dns=True)
    except ServiceError:
        raise
    except Exception:
        raise ServiceError("unavailable", "OAuth discovery failed; check the issuer and client configuration") from None
    state, binding, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    async with transaction(storage) as session:
        assert scope.workspace_id is not None
        connection = await get_row(session, scope.workspace_id, connection_id, lock=True)
        if connection.version != selected.version or not connection.enabled:
            raise ServiceError("conflict", "Connection changed during OAuth preparation")
        await session.execute(
            insert(ConnectionAuthorizationRow)
            .values(
                id=new_object_id("cauth"),
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                connection_id=connection_id,
                principal_id=actor.id,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["connection_id", "principal_id"])
        )
        row = await session.scalar(
            select(ConnectionAuthorizationRow)
            .where(
                ConnectionAuthorizationRow.connection_id == connection_id,
                ConnectionAuthorizationRow.principal_id == actor.id,
            )
            .with_for_update()
        )
        assert row is not None
        row.generation += 1
        row.status = "pending"
        row.operation_id = row.operation_kind = row.operation_deadline = None
        row.oauth_state_hash = secret_hash(state)
        row.redirect_uri, row.return_uri = settings.callback_url, return_url
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        row.expires_at = now + timedelta(seconds=settings.flow_seconds)
        row.failure = None
        seal(
            row,
            {
                "identity": identity(connection),
                "verifier": verifier,
                "binding_hash": secret_hash(binding),
                "authority": ceiling.model_dump(mode="json"),
                "endpoints": endpoints.model_dump(mode="json"),
                "config": selected.config.oauth.model_dump(mode="json"),
                "redirect_uri": settings.callback_url,
                "return_uri": return_url,
                "scope": " ".join(selected.config.oauth.scopes),
            },
            keys,
        )
        oauth_tokens.audit(session, row, "authorize")
        result = oauth_tokens.view(row)
        row_id = row.id
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    params = {
        "response_type": "code",
        "client_id": selected.config.oauth.client_id,
        "redirect_uri": settings.callback_url,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": endpoints.resource,
        "scope": " ".join(selected.config.oauth.scopes),
    }
    separator = "&" if "?" in endpoints.authorization_endpoint else "?"
    return BrowserFlow(
        AuthorizationStart(
            authorization=result, redirect_url=endpoints.authorization_endpoint + separator + urlencode(params)
        ),
        cookie_name(row_id, state),
        binding,
    )


async def callback(
    storage: Storage,
    *,
    state: str,
    cookies: Mapping[str, str],
    code: str | None,
    error: str | None,
    issuer: str | None,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: OAuth,
) -> tuple[str, str]:
    # State lookup supplies ownership. No caller-supplied principal or resource selector is accepted.
    async with short_session(storage) as session:
        found = await session.scalar(
            select(ConnectionAuthorizationRow).where(ConnectionAuthorizationRow.oauth_state_hash == secret_hash(state))
        )
        if found is None:
            raise ServiceError("invalid_argument", "OAuth callback state is invalid or already used")
        authorization_id, connection_id = found.id, found.connection_id
        actor = await principal_for(
            session, found.principal_id, confinement=Scope(found.organization_id, found.workspace_id)
        )
        scope = await workspace_scope(session, actor, found.workspace_id, "run")
    owned = None
    async with transaction(storage) as session:
        assert scope.workspace_id is not None
        connection = await get_row(session, scope.workspace_id, connection_id, lock=True)
        row = await session.get(ConnectionAuthorizationRow, authorization_id, with_for_update=True)
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        if (
            row is None
            or row.status != "pending"
            or row.oauth_state_hash != secret_hash(state)
            or row.credential is None
        ):
            raise ServiceError("invalid_argument", "OAuth callback state is invalid or already used")
        bundle = reveal(row, keys)
        if not hmac.compare_digest(secret_hash(cookies.get(cookie_name(row.id, state), "")), bundle["binding_hash"]):
            raise ServiceError("forbidden", "OAuth callback does not belong to this browser")
        authorize(actor, scope, "run", authority=ExecutionAuthority.model_validate(bundle["authority"]))
        if (
            not connection.enabled
            or connection.auth != "oauth"
            or identity(connection) != bundle["identity"]
            or row.redirect_uri != settings.callback_url
            or row.return_uri not in settings.return_urls
            or row.redirect_uri != bundle["redirect_uri"]
            or row.return_uri != bundle["return_uri"]
            or row.expires_at is None
            or row.expires_at <= now
        ):
            raise ServiceError("invalid_argument", "OAuth authorization expired or changed")
        expected_issuer = bundle["endpoints"]["issuer"]
        if (issuer is not None and issuer != expected_issuer) or (
            bundle["endpoints"]["response_issuer_required"] and issuer is None
        ):
            raise ServiceError("invalid_argument", "OAuth callback issuer does not match")
        if error is not None:
            if row.operation_id is not None:
                raise ServiceError("conflict", "OAuth exchange is already in progress")
            invalidate(row, "reauthorization_required", "authorization_denied")
            oauth_tokens.audit(session, row, "denied")
        elif code is None:
            raise ServiceError("invalid_argument", "OAuth callback has no authorization code")
        elif row.operation_id is None:
            owned = oauth_tokens.claim(
                session, row, connection, bundle, kind="exchange", now=now, settings=settings, keys=keys
            )
        generation = row.generation
        return_uri = row.return_uri
        assert return_uri is not None
    if error is None:
        if owned is not None:
            await oauth_tokens.dispatch(storage, owned, keys=keys, policy=policy, settings=settings, code=code)
        await oauth_tokens.joined(storage, authorization_id, generation, settings=settings)
    return return_uri, cookie_name(authorization_id, state)


async def revoke(storage: Storage, actor: Principal, workspace_id: str, connection_id: str) -> AuthorizationView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
    assert scope.workspace_id is not None
    async with transaction(storage) as session:
        await get_row(session, scope.workspace_id, connection_id, lock=True)
        row = await session.scalar(
            select(ConnectionAuthorizationRow)
            .where(
                ConnectionAuthorizationRow.connection_id == connection_id,
                ConnectionAuthorizationRow.principal_id == actor.id,
            )
            .with_for_update()
        )
        if row is None:
            raise ServiceError("not_found", "OAuth authorization was not found")
        invalidate(row, "revoked", "revoked")
        oauth_tokens.audit(session, row, "revoke")
        return oauth_tokens.view(row)
