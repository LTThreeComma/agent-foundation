"""Managed operation ownership, fresh authority, and publication fences."""

from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.connections import oauth_tokens
from a13n_service.resources.connections.managed_values import ManagedClaim
from a13n_service.resources.connections.oauth_state import identity, invalidate, seal
from a13n_service.resources.connections.service import ResolvedConnection, get_row
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow, ConnectionRow
from a13n_service.settings import Managed
from a13n_service.tenancy.authorize import ExecutionAuthority, Scope, authorize
from a13n_service.tenancy.grants import principal_for, workspace_scope


async def claim(
    session: AsyncSession,
    row: ConnectionAuthorizationRow,
    connection: ConnectionRow,
    selected: ResolvedConnection,
    bundle: dict,
    *,
    kind: Literal["setup", "complete", "revoke"],
    settings: Managed,
) -> ManagedClaim:
    now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    operation_id = new_object_id("authop")
    row.operation_id = operation_id
    row.operation_kind = kind
    deadline = now + timedelta(seconds=settings.operation_seconds)
    row.operation_deadline = deadline
    row.failure = None
    oauth_tokens.audit(session, row, kind + "_claim")
    return ManagedClaim(
        row.id,
        row.generation,
        operation_id,
        kind,
        row.principal_id,
        selected,
        identity(connection),
        deadline,
        bundle,
    )


async def check_authority(session: AsyncSession, owned: ManagedClaim) -> None:
    actor = await principal_for(
        session,
        owned.principal_id,
        confinement=Scope(owned.selected.organization_id, owned.selected.workspace_id),
    )
    scope = await workspace_scope(session, actor, owned.selected.workspace_id, "run")
    authorize(actor, scope, "run", authority=ExecutionAuthority.model_validate(owned.bundle["authority"]))


async def matches(session: AsyncSession, owned: ManagedClaim, *, lock: bool = False) -> ConnectionAuthorizationRow:
    connection = await get_row(session, owned.selected.workspace_id, owned.selected.id, lock=lock)
    row = await session.get(ConnectionAuthorizationRow, owned.authorization_id, with_for_update=lock)
    now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    if (
        row is None
        or row.generation != owned.generation
        or row.operation_id != owned.operation_id
        or row.operation_kind != owned.kind
        or row.principal_id != owned.principal_id
        or row.status != ("revoked" if owned.kind == "revoke" else "pending")
        or (owned.kind != "revoke" and (row.expires_at is None or row.expires_at <= now))
        or connection.version != owned.selected.version
        or not connection.enabled
        or connection.auth != "managed"
        or identity(connection) != owned.identity
        or row.operation_deadline is None
        or row.operation_deadline <= now
    ):
        raise ServiceError("conflict", "Managed authorization changed or expired")
    return row


async def before_request(storage: Storage, owned: ManagedClaim) -> None:
    async with short_session(storage) as session:
        await matches(session, owned)
        await check_authority(session, owned)


async def publish(
    storage: Storage,
    owned: ManagedClaim,
    *,
    keys: KeyRing,
    bundle: dict | None = None,
    active: bool = False,
    expires_at: datetime | None = None,
    failure: str | None = None,
) -> bool:
    async with transaction(storage) as session:
        try:
            row = await matches(session, owned, lock=True)
        except ServiceError:
            return False
        if failure is not None:
            if owned.kind == "revoke" or (owned.kind == "complete" and failure == "unknown_after_dispatch"):
                row.failure = {"reason": failure}
            else:
                invalidate(row, "reauthorization_required", failure, retain_operation=True)
            oauth_tokens.audit(session, row, "failed")
            return True
        await check_authority(session, owned)
        if owned.kind != "revoke":
            assert bundle is not None
            seal(row, bundle, keys)
            row.status = "active" if active else "pending"
            if active:
                row.expires_at = None
            elif expires_at is not None and row.expires_at is not None:
                row.expires_at = min(row.expires_at, expires_at)
        row.operation_id = row.operation_kind = row.operation_deadline = None
        row.failure = None
        oauth_tokens.audit(session, row, "activated" if active else owned.kind + "_completed")
        return True
