"""Refresh on use and live-session authorization checks without holding SQL over I/O."""

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy import func, select

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.connections import oauth_tokens
from a13n_service.resources.connections.oauth_state import identity, invalidate, reveal
from a13n_service.resources.connections.oauth_values import OAuthAccess
from a13n_service.resources.connections.service import ResolvedConnection, get_row
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow
from a13n_service.settings import OAuth
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Scope, authorize


def required(reason: str) -> ServiceError:
    return ServiceError("disabled", "Reconnect this OAuth Connection before using its tools", {"reason": reason})


async def access(
    storage: Storage,
    actor: Principal,
    selected: ResolvedConnection,
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: OAuth,
    authority: ExecutionAuthority | None = None,
) -> OAuthAccess:
    authorize(actor, Scope(selected.organization_id, selected.workspace_id), "run", authority=authority)
    owned = None
    failure = None
    async with transaction(storage) as session:
        connection = await get_row(session, selected.workspace_id, selected.id, lock=True)
        if connection.version != selected.version or not connection.enabled or connection.auth != "oauth":
            raise required("connection_changed")
        row = await session.scalar(
            select(ConnectionAuthorizationRow)
            .where(
                ConnectionAuthorizationRow.connection_id == selected.id,
                ConnectionAuthorizationRow.principal_id == actor.id,
            )
            .with_for_update()
        )
        if row is None or row.status != "active" or row.credential is None:
            raise required("authorization_required" if row is None else row.status)
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        bundle = reveal(row, keys)
        if bundle["identity"] != identity(connection):
            invalidate(row, "reauthorization_required", "connection_changed")
            failure = "connection_changed"
        elif row.operation_id is not None:
            if row.operation_deadline <= now:
                invalidate(row, "reauthorization_required", "operation_deadline_unknown", retain_operation=True)
                failure = "operation_deadline_unknown"
        elif row.expires_at is None or row.expires_at > now:
            return OAuthAccess(row.id, row.generation, bundle["access_token"])
        elif not bundle.get("refresh_token") or not bundle["endpoints"]["refresh_supported"]:
            invalidate(row, "reauthorization_required", "access_token_expired")
            failure = "access_token_expired"
        else:
            owned = oauth_tokens.claim(
                session, row, connection, bundle, kind="refresh", now=now, settings=settings, keys=keys
            )
        if failure is not None:
            oauth_tokens.audit(session, row, "expired")
        authorization_id, generation = row.id, row.generation
    if failure is not None:
        raise required(failure)
    if owned is not None:
        await oauth_tokens.dispatch(storage, owned, keys=keys, policy=policy, settings=settings)
    outcome = await oauth_tokens.joined(storage, authorization_id, generation, settings=settings)
    if outcome.status != "active":
        raise required((outcome.failure or {}).get("reason", outcome.status))
    async with short_session(storage) as session:
        row = await session.get(ConnectionAuthorizationRow, authorization_id)
        if row is None or row.generation != generation or row.status != "active" or row.credential is None:
            raise required("authorization_changed")
        return OAuthAccess(row.id, row.generation, reveal(row, keys)["access_token"])


async def check_session(storage: Storage, token: OAuthAccess, *, principal_id: str, connection_id: str) -> None:
    async with short_session(storage) as session:
        row = await session.get(ConnectionAuthorizationRow, token.authorization_id)
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        if (
            row is None
            or row.principal_id != principal_id
            or row.connection_id != connection_id
            or row.status != "active"
            or row.generation != token.generation
            or row.operation_id is not None
            or (row.expires_at is not None and row.expires_at <= now)
        ):
            raise required("authorization_changed_or_expired")


async def rejected(storage: Storage, token: OAuthAccess) -> None:
    async with transaction(storage) as session:
        row = await session.get(ConnectionAuthorizationRow, token.authorization_id, with_for_update=True)
        if row is not None and row.generation == token.generation and row.status == "active":
            invalidate(row, "reauthorization_required", "access_token_rejected")
            oauth_tokens.audit(session, row, "rejected")
