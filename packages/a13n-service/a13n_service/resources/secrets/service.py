"""Write-only secrets: each value is encrypted for its own row and revealed only to execution."""

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import JsonValue, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import Storage, short_session, transaction, unique_key
from a13n_service.infra.errors import not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.resources.secrets.schemas import (
    Secret,
    SecretCreate,
    SecretPage,
    SecretRequirement,
    SecretUpdate,
)
from a13n_service.resources.secrets.tables import SecretRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


def _location(row: SecretRow) -> SecretLocation:
    return SecretLocation(row.organization_id, "secrets", "ciphertext", row.id)


def _view(row: SecretRow) -> Secret:
    return Secret(
        id=row.id,
        workspace_id=row.workspace_id,
        key=row.key,
        version=row.version,
        created_by_id=row.created_by_id,
        updated_by_id=row.updated_by_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _audit(session: AsyncSession, actor: Principal, row: SecretRow, verb: str) -> None:
    details: dict[str, JsonValue] = {"key": row.key}
    audit_row(session, actor, row, verb, details)


async def create_secret(
    storage: Storage, keys: KeyRing, actor: Principal, workspace_id: str, body: SecretCreate
) -> Secret:
    with unique_key(SecretRow.KIND, "uq_secrets_workspace_id_key", body.key):
        async with transaction(storage) as session:
            scope = await workspace_scope(session, actor, workspace_id, "write")
            row = SecretRow(
                id=new_object_id("sec"),
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                key=body.key,
                created_by_id=actor.id,
                updated_by_id=actor.id,
            )
            row.ciphertext = keys.protect(body.value.get_secret_value().encode(), _location(row)).model_dump()
            session.add(row)
            _audit(session, actor, row, "create")
            await session.flush()
            return _view(row)


async def list_secrets(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> SecretPage:
    """Metadata for the workspace's secrets; no value is returned."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(SecretRow).where(SecretRow.workspace_id == scope.workspace_id),
            SecretRow.id,
            kind="secrets",
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
        )
        return SecretPage(items=[_view(row) for row in rows], next_cursor=next_cursor)


async def get_secret(storage: Storage, actor: Principal, workspace_id: str, secret_id: str) -> Secret:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, SecretRow, scope, secret_id, "read")
        return _view(row)


async def replace_secret(
    storage: Storage,
    keys: KeyRing,
    actor: Principal,
    workspace_id: str,
    secret_id: str,
    body: SecretUpdate,
    *,
    if_match: str | None,
) -> Secret:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, SecretRow, scope, secret_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        row.ciphertext = keys.protect(body.value.get_secret_value().encode(), _location(row)).model_dump()
        row.updated_by_id = actor.id
        _audit(session, actor, row, "update")
        await session.flush()
        return _view(row)


async def delete_secret(
    storage: Storage, actor: Principal, workspace_id: str, secret_id: str, *, if_match: str | None
) -> None:
    """Release the key for a replacement; accepted runs keep no copy of the value."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, SecretRow, scope, secret_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        _audit(session, actor, row, "delete")
        await session.delete(row)


@dataclass(frozen=True, slots=True)
class StoredSecret:
    envelope: Envelope
    location: SecretLocation


async def required_secrets(
    storage: Storage, workspace_id: str, requirements: Sequence[SecretRequirement]
) -> dict[str, StoredSecret]:
    """Resolve keys within one workspace and detach encrypted values without revealing plaintext."""
    if not requirements:
        return {}
    async with short_session(storage) as session:
        rows = (
            await session.scalars(
                select(SecretRow).where(
                    SecretRow.workspace_id == workspace_id,
                    SecretRow.key.in_({requirement.key for requirement in requirements}),
                )
            )
        ).all()
        found = {row.key: StoredSecret(Envelope.model_validate(row.ciphertext), _location(row)) for row in rows}
    selected = {}
    for requirement in requirements:
        value = found.get(requirement.key)
        if value is None:
            raise not_found(SecretRow.KIND, requirement.key)
        selected[requirement.key] = value
    return selected


async def resolve_secrets(
    storage: Storage,
    keys: KeyRing,
    *,
    workspace_id: str,
    requirements: Sequence[SecretRequirement],
) -> dict[str, SecretStr]:
    """Reveal the current value for each key during an authorized tool call, after closing the session."""
    selected = await required_secrets(storage, workspace_id, requirements)
    return {key: SecretStr(keys.reveal(value.envelope, value.location).decode()) for key, value in selected.items()}
