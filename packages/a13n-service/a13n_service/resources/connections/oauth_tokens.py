"""One claimed token dispatch and generation-fenced publication, shared by all callers."""

import asyncio
import base64
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import quote

import anyio
import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbound import open_http
from a13n_service.providers.oauth import TokenResponse
from a13n_service.resources.connections.oauth_state import identity, invalidate, seal
from a13n_service.resources.connections.oauth_values import AuthorizationView, OAuthClaim
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow, ConnectionRow
from a13n_service.settings import OAuth


def view(row: ConnectionAuthorizationRow) -> AuthorizationView:
    return AuthorizationView(
        id=row.id,
        connection_id=row.connection_id,
        status=row.status,
        expires_at=row.expires_at,
        generation=row.generation,
        operation_kind=row.operation_kind,
        failure=row.failure,
    )


def audit(session: AsyncSession, row: ConnectionAuthorizationRow, action: str) -> None:
    record(
        session,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        actor_id=row.principal_id,
        action="connection_authorization." + action,
        target_kind="connection_authorization",
        target_id=row.id,
    )


def client_secret(connection: ConnectionRow, keys: KeyRing) -> str | None:
    if connection.credential is None:
        return None
    import json

    return json.loads(
        keys.reveal(
            Envelope.model_validate(connection.credential),
            SecretLocation(connection.organization_id, "connections", "credential", connection.id),
        )
    )["client_secret"]


def claim(
    session: AsyncSession,
    row: ConnectionAuthorizationRow,
    connection: ConnectionRow,
    bundle: dict,
    *,
    kind: Literal["exchange", "refresh"],
    now: datetime,
    settings: OAuth,
    keys: KeyRing,
) -> OAuthClaim:
    assert kind in {"exchange", "refresh"}
    row.generation += 1
    operation_id = new_object_id("oauthop")
    row.operation_id = operation_id
    row.operation_kind = kind
    deadline = now + timedelta(seconds=settings.operation_seconds)
    row.operation_deadline = deadline
    row.failure = None
    audit(session, row, kind + "_claim")
    assert row.operation_id is not None and row.operation_deadline is not None
    return OAuthClaim(
        row.id,
        row.connection_id,
        row.workspace_id,
        row.principal_id,
        row.generation,
        operation_id,
        kind,
        identity(connection),
        bundle,
        client_secret(connection, keys),
        deadline,
    )


def matches(row: ConnectionAuthorizationRow, owned: OAuthClaim, connection: ConnectionRow, now: datetime) -> bool:
    return (
        row.generation == owned.generation
        and row.operation_id == owned.operation_id
        and row.operation_kind == owned.kind
        and row.status == ("pending" if owned.kind == "exchange" else "active")
        and connection.enabled
        and connection.auth == "oauth"
        and identity(connection) == owned.identity
        and row.operation_deadline is not None
        and row.operation_deadline > now
    )


async def finish(
    storage: Storage, owned: OAuthClaim, *, keys: KeyRing, tokens: TokenResponse | None, failure: str | None
) -> bool:
    async with transaction(storage) as session:
        connection = await session.get(ConnectionRow, owned.connection_id, with_for_update=True)
        row = await session.get(ConnectionAuthorizationRow, owned.authorization_id, with_for_update=True)
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        if row is None or connection is None or not matches(row, owned, connection, now):
            return False
        if tokens is None:
            invalidate(row, "reauthorization_required", failure or "unknown_after_dispatch", retain_operation=True)
            audit(session, row, "failed")
        else:
            bundle = owned.bundle
            seal(
                row,
                {
                    "identity": owned.identity,
                    "endpoints": bundle["endpoints"],
                    "config": bundle["config"],
                    "access_token": tokens.access_token.get_secret_value(),
                    "refresh_token": tokens.refresh_token.get_secret_value()
                    if tokens.refresh_token
                    else (bundle.get("refresh_token") if owned.kind == "refresh" else None),
                    "scope": tokens.scope if tokens.scope is not None else bundle.get("scope"),
                },
                keys,
            )
            row.status = "active"
            row.expires_at = now + timedelta(seconds=tokens.expires_in) if tokens.expires_in is not None else None
            row.oauth_state_hash = None
            row.operation_id = row.operation_kind = row.operation_deadline = None
            row.failure = None
            audit(session, row, "activated")
        return True


async def dispatch(
    storage: Storage,
    owned: OAuthClaim,
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: OAuth,
    code: str | None = None,
) -> None:
    bundle = owned.bundle
    config = bundle["config"]
    fields = {
        "grant_type": "authorization_code" if owned.kind == "exchange" else "refresh_token",
        "client_id": config["client_id"],
        "resource": bundle["endpoints"]["resource"],
    }
    if owned.kind == "exchange":
        assert code is not None
        fields.update(code=code, code_verifier=bundle["verifier"], redirect_uri=bundle["redirect_uri"])
    else:
        fields["refresh_token"] = bundle["refresh_token"]
    headers = {}
    if config["token_endpoint_auth_method"] == "client_secret_basic":
        assert owned.client_secret is not None
        pair = quote(config["client_id"], safe="") + ":" + quote(owned.client_secret, safe="")
        headers["authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
    elif config["token_endpoint_auth_method"] == "client_secret_post":
        assert owned.client_secret is not None
        fields["client_secret"] = owned.client_secret
    possibly_sent = False

    async def mark_dispatch(request: httpx2.Request) -> None:
        nonlocal possibly_sent
        possibly_sent = True

    tokens = None
    failure = None
    try:
        async with (
            asyncio.timeout(settings.operation_seconds),
            open_http(
                policy, timeout=settings.operation_seconds, max_bytes=65536, before_request=mark_dispatch
            ) as client,
        ):
            response = await client.post(bundle["endpoints"]["token_endpoint"], data=fields, headers=headers)
            if response.status_code in {400, 401, 403}:
                failure = "token_rejected"
            else:
                response.raise_for_status()
                tokens = TokenResponse.model_validate_json(response.content)
                if tokens.scope is not None and not set(tokens.scope.split()) <= set(config["scopes"]):
                    tokens = None
                    failure = "unexpected_token_scope"
    except BaseException as error:
        failure = "unknown_after_dispatch" if possibly_sent else "rejected_before_dispatch"
        with anyio.move_on_after(3, shield=True):
            await finish(storage, owned, keys=keys, tokens=None, failure=failure)
        if isinstance(error, asyncio.CancelledError):
            raise
        if not isinstance(error, Exception):
            raise
        return
    await finish(storage, owned, keys=keys, tokens=tokens, failure=failure)


async def joined(storage: Storage, authorization_id: str, generation: int, *, settings: OAuth) -> AuthorizationView:
    try:
        async with asyncio.timeout(settings.operation_seconds + 1):
            while True:
                async with short_session(storage) as session:
                    row = await session.get(ConnectionAuthorizationRow, authorization_id)
                    if row is None or row.generation != generation:
                        raise ServiceError("conflict", "OAuth authorization changed during the operation")
                    result = view(row)
                    if row.operation_id is None or row.status == "reauthorization_required":
                        return result
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise ServiceError("unavailable", "OAuth operation is awaiting deadline recovery") from None


async def recover_deadlines(storage: Storage) -> int:
    async with transaction(storage) as session:
        rows = list(
            await session.scalars(
                select(ConnectionAuthorizationRow)
                .where(
                    ConnectionAuthorizationRow.status.in_(("pending", "active", "revoked")),
                    or_(
                        and_(
                            ConnectionAuthorizationRow.operation_deadline <= func.clock_timestamp(),
                            ConnectionAuthorizationRow.failure.is_(None),
                        ),
                        and_(
                            ConnectionAuthorizationRow.status == "pending",
                            ConnectionAuthorizationRow.expires_at <= func.clock_timestamp(),
                        ),
                    ),
                )
                .order_by(ConnectionAuthorizationRow.operation_deadline, ConnectionAuthorizationRow.id)
                .limit(32)
                .with_for_update(skip_locked=True)
            )
        )
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        for row in rows:
            if row.operation_kind in {"complete", "revoke"} and (
                row.status == "revoked" or (row.expires_at is not None and row.expires_at > now)
            ):
                row.failure = {"reason": "unknown_after_dispatch"}
                audit(session, row, "expired")
                continue
            invalidate(
                row,
                "reauthorization_required",
                "operation_deadline_unknown" if row.operation_id else "authorization_expired",
                retain_operation=row.operation_id is not None,
            )
            audit(session, row, "expired")
        return len(rows)
