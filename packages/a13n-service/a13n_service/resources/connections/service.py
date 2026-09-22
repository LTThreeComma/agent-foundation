"""Connection management and the single credential reveal boundary."""

import json
from dataclasses import dataclass, field
from typing import Literal

from a13n_harness.providers.catalog import ProviderCatalog, ProviderNotSelected
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.tools import ToolSourceDefinition
from a13n_service.resources.connections.schemas import (
    BearerCredential,
    ConnectionAuthentication,
    ConnectionCreate,
    ConnectionPage,
    ConnectionUpdate,
    ConnectionView,
    Credential,
    HeadersCredential,
    MCPConfig,
)
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Scope, authorize
from a13n_service.tenancy.grants import workspace_scope


@dataclass(frozen=True)
class ResolvedConnection:
    id: str
    organization_id: str
    workspace_id: str
    type: str
    version: int
    config: MCPConfig
    auth: ConnectionAuthentication
    credential: dict | None = field(repr=False)


def view(row: ConnectionRow) -> ConnectionView:
    return ConnectionView(
        id=row.id,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        type=row.type,
        name=row.name,
        config=MCPConfig.model_validate(row.config),
        auth=row.auth,
        credential_configured=row.credential is not None,
        enabled=row.enabled,
        version=row.version,
    )


async def get_row(session: AsyncSession, workspace_id: str, connection_id: str, *, lock: bool = False) -> ConnectionRow:
    query = select(ConnectionRow).where(ConnectionRow.workspace_id == workspace_id, ConnectionRow.id == connection_id)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise ServiceError("not_found", "Connection was not found")
    return row


def protect(
    keys: KeyRing,
    organization_id: str,
    connection_id: str,
    auth: ConnectionAuthentication,
    credential: Credential | None,
) -> dict | None:
    if credential is None:
        return None
    if auth == "bearer" and isinstance(credential, BearerCredential):
        value = {"token": credential.token.get_secret_value()}
    elif auth == "headers" and isinstance(credential, HeadersCredential):
        value = {"headers": {name: secret.get_secret_value() for name, secret in credential.headers.items()}}
    else:
        raise ServiceError("invalid_argument", "Credential does not match Connection authentication")
    return keys.protect(
        json.dumps(value).encode(), SecretLocation(organization_id, "connections", "credential", connection_id)
    ).model_dump(mode="json")


def authentication_headers(selected: ResolvedConnection, keys: KeyRing) -> dict[str, str]:
    if selected.auth == "none":
        return {}
    if selected.credential is None:
        raise ServiceError("disabled", "Connection credential is not configured", {"connection_id": selected.id})
    plaintext = keys.reveal(
        Envelope.model_validate(selected.credential),
        SecretLocation(selected.organization_id, "connections", "credential", selected.id),
    )
    if selected.auth == "bearer":
        credential = BearerCredential.model_validate_json(plaintext)
        return {"authorization": "Bearer " + credential.token.get_secret_value()}
    credential = HeadersCredential.model_validate_json(plaintext)
    return {name: value.get_secret_value() for name, value in credential.headers.items()}


async def resolve(
    session: AsyncSession,
    actor: Principal,
    scope: Scope,
    connection_id: str,
    *,
    verb: Literal["read", "run"],
    authority: ExecutionAuthority | None = None,
) -> ResolvedConnection:
    assert scope.workspace_id is not None
    row = await get_row(session, scope.workspace_id, connection_id)
    authorize(actor, Scope(row.organization_id, row.workspace_id), verb, authority=authority)
    if not row.enabled:
        raise ServiceError("disabled", "Connection is disabled", {"connection_id": row.id})
    return ResolvedConnection(
        row.id,
        row.organization_id,
        row.workspace_id,
        row.type,
        row.version,
        MCPConfig.model_validate(row.config),
        row.auth,
        row.credential,
    )


async def create(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    body: ConnectionCreate,
    *,
    catalog: ProviderCatalog[ToolSourceDefinition],
    keys: KeyRing,
    policy: EndpointPolicy,
) -> ConnectionView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
    assert scope.workspace_id is not None
    try:
        catalog.require(body.type)
    except ProviderNotSelected:
        raise ServiceError("invalid_argument", "Connection provider is unavailable") from None
    try:
        await policy.validate(body.config.url)
    except ValueError:
        raise ServiceError("invalid_argument", "Connection endpoint is not permitted") from None
    connection_id = new_object_id("conn")
    credential = protect(keys, scope.organization_id, connection_id, body.auth, body.credential)
    async with transaction(storage) as session:
        row = ConnectionRow(
            id=connection_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            type=body.type,
            name=body.name,
            config=body.config.model_dump(mode="json"),
            auth=body.auth,
            credential=credential,
            enabled=True,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        record(
            session,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            actor_id=actor.id,
            action="connection.create",
            target_kind="connection",
            target_id=row.id,
        )
        await session.flush()
        return view(row)


async def get(storage: Storage, actor: Principal, workspace_id: str, connection_id: str) -> ConnectionView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        return view(await get_row(session, scope.workspace_id, connection_id))


async def list_connections(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> ConnectionPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        position = cursors.id_position(cursor, "connections", scope.workspace_id)
        rows = list(
            await session.scalars(
                select(ConnectionRow)
                .where(ConnectionRow.workspace_id == scope.workspace_id, ConnectionRow.id > position)
                .order_by(ConnectionRow.id)
                .limit(limit + 1)
            )
        )
        return ConnectionPage(
            items=[view(row) for row in rows[:limit]],
            next_cursor=cursors.encode("connections", scope.workspace_id, rows[limit - 1].id)
            if len(rows) > limit
            else None,
        )


async def update(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    connection_id: str,
    body: ConnectionUpdate,
    *,
    if_match: str | None,
    keys: KeyRing,
    policy: EndpointPolicy,
) -> ConnectionView:
    fields = body.model_fields_set
    if any(getattr(body, name) is None for name in fields - {"credential"}):
        raise ServiceError("invalid_argument", "Only the credential may be cleared")
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        assert scope.workspace_id is not None
        current = await get_row(session, scope.workspace_id, connection_id)
        require_match(if_match, current.id, current.version)
    if body.config is not None:
        try:
            await policy.validate(body.config.url)
        except ValueError:
            raise ServiceError("invalid_argument", "Connection endpoint is not permitted") from None
    async with transaction(storage) as session:
        row = await get_row(session, scope.workspace_id, connection_id, lock=True)
        require_match(if_match, row.id, row.version)
        auth = body.auth or row.auth
        if auth != row.auth and "credential" not in fields:
            raise ServiceError(
                "invalid_argument",
                "ConnectionAuthentication changes require an explicit credential replacement or removal",
            )
        if "credential" in fields:
            row.credential = protect(keys, row.organization_id, row.id, auth, body.credential)
        config = body.config or MCPConfig.model_validate(row.config)
        identity_changed = config.url != row.config["url"] or auth != row.auth or "credential" in fields
        if identity_changed and (
            body.config is None or "recovery_retry_safe_tools" not in body.config.model_fields_set
        ):
            config = config.model_copy(update={"recovery_retry_safe_tools": ()})
        row.config = config.model_dump(mode="json")
        row.auth = auth
        if body.name is not None:
            row.name = body.name
        if body.enabled is not None:
            row.enabled = body.enabled
        row.updated_by_id = actor.id
        record(
            session,
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            actor_id=actor.id,
            action="connection.update",
            target_kind="connection",
            target_id=row.id,
        )
        await session.flush()
        await session.refresh(row)
        return view(row)
