"""Write-only secrets: each value is encrypted for its own row and revealed only to execution."""

from collections.abc import Sequence

from pydantic import JsonValue, SecretStr
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import Storage, short_session, transaction, unique_key
from a13n_service.infra.errors import not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.rows import audit_row
from a13n_service.resources.secrets.schemas import (
    Secret,
    SecretCreate,
    SecretPage,
    SecretRequirement,
    SecretUpdate,
)
from a13n_service.resources.secrets.tables import SecretRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, Verb, allowed_verbs


def _location(row: SecretRow) -> SecretLocation:
    return SecretLocation(row.organization_id, "secrets", "ciphertext", row.id)


def _view(row: SecretRow) -> Secret:
    return Secret(
        id=row.id,
        workspace_id=row.workspace_id,
        key=row.key,
        scope="workspace" if row.principal_id is None else "user",
        principal_id=row.principal_id,
        version=row.version,
        created_by_id=row.created_by_id,
        updated_by_id=row.updated_by_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _visible_to(principal_id: str) -> ColumnElement[bool]:
    """Private secrets exist only for their owner, whatever the caller's workspace role."""
    return or_(SecretRow.principal_id.is_(None), SecretRow.principal_id == principal_id)


def _change_verb(actor: Principal, owner_id: str | None) -> Verb:
    """Workspace secrets, without an owner, need `write`; a private secret needs `run` from its owner and
    `admin` from anyone else."""
    if owner_id is None:
        return "write"
    return "run" if owner_id == actor.id else "admin"


async def _resolve_changeable(
    session: AsyncSession, actor: Principal, workspace_id: str, secret_id: str, *, others: bool = False
) -> SecretRow:
    """The locked secret the actor may change. With `others`, workspace admins also find every user's private
    secret; anyone else finds only their own, as reads do."""
    scope = await workspace_scope(session, actor, workspace_id, "read")
    query = select(SecretRow).where(SecretRow.workspace_id == scope.workspace_id, SecretRow.id == secret_id)
    if not (others and "admin" in allowed_verbs(actor, scope)):
        query = query.where(_visible_to(actor.id))
    row = await session.scalar(query.with_for_update())
    if row is None:
        raise not_found(SecretRow.KIND, secret_id)
    await workspace_scope(session, actor, scope.workspace_id, _change_verb(actor, row.principal_id))
    return row


def _audit(session: AsyncSession, actor: Principal, row: SecretRow, verb: str) -> None:
    details: dict[str, JsonValue] = {"key": row.key, "scope": "workspace" if row.principal_id is None else "user"}
    audit_row(session, actor, row, verb, details)


async def create_secret(
    storage: Storage, keys: KeyRing, actor: Principal, workspace_id: str, body: SecretCreate
) -> Secret:
    owner_id = actor.id if body.scope == "user" else None
    with unique_key(SecretRow.KIND, "uq_secrets_owner_key", body.key):
        async with transaction(storage) as session:
            scope = await workspace_scope(session, actor, workspace_id, _change_verb(actor, owner_id))
            row = SecretRow(
                id=new_object_id("sec"),
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                principal_id=owner_id,
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
    """Workspace secrets and the caller's own private ones."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(SecretRow).where(SecretRow.workspace_id == scope.workspace_id, _visible_to(actor.id)),
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
        row = await session.scalar(
            select(SecretRow).where(
                SecretRow.workspace_id == scope.workspace_id, SecretRow.id == secret_id, _visible_to(actor.id)
            )
        )
        if row is None:
            raise not_found(SecretRow.KIND, secret_id)
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
        row = await _resolve_changeable(session, actor, workspace_id, secret_id)
        require_match(if_match, row.id, row.version)
        row.ciphertext = keys.protect(body.value.get_secret_value().encode(), _location(row)).model_dump()
        row.updated_by_id = actor.id
        _audit(session, actor, row, "update")
        await session.flush()
        return _view(row)


async def delete_secret(
    storage: Storage, actor: Principal, workspace_id: str, secret_id: str, *, if_match: str | None
) -> None:
    """Later executions requiring the key fail; accepted runs keep no copy of the value."""
    async with transaction(storage) as session:
        row = await _resolve_changeable(session, actor, workspace_id, secret_id, others=True)
        require_match(if_match, row.id, row.version)
        _audit(session, actor, row, "delete")
        await session.delete(row)


async def resolve_secrets(
    storage: Storage,
    keys: KeyRing,
    *,
    workspace_id: str,
    principal_id: str,
    requirements: Sequence[SecretRequirement],
) -> dict[str, SecretStr]:
    """Plaintext by requirement key for one execution: workspace secrets, or the run principal's own.

    Rows are read in one short session and decrypted after it closes. Requirement keys are unique per agent
    revision. A missing secret is `not_found` naming only its key; values never enter errors or logs.
    """
    if not requirements:
        return {}
    async with short_session(storage) as session:
        rows = (
            await session.scalars(
                select(SecretRow).where(
                    SecretRow.workspace_id == workspace_id,
                    SecretRow.key.in_({requirement.key for requirement in requirements}),
                    _visible_to(principal_id),
                )
            )
        ).all()
    found = {(row.key, row.principal_id is not None): row for row in rows}
    values: dict[str, SecretStr] = {}
    for requirement in requirements:
        row = found.get((requirement.key, requirement.scope == "user"))
        if row is None:
            raise not_found(SecretRow.KIND, requirement.key)
        plaintext = keys.reveal(Envelope.model_validate(row.ciphertext), _location(row))
        values[requirement.key] = SecretStr(plaintext.decode())
    return values
