"""What every resource kind does with its rows: find one in scope, apply a partial change, audit it, and report a
key another row already holds.

A row class names its resource kind in `KIND`, which its errors and audit events carry.
"""

from collections.abc import Sequence
from typing import ClassVar, Protocol

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from a13n_service.infra.audit import record
from a13n_service.infra.errors import not_found
from a13n_service.tenancy.access import refuse_archived
from a13n_service.tenancy.authorize import Principal, Scope, Verb, WorkspaceScope, allowed_verbs, authorize
from a13n_service.tenancy.tables import WorkspaceRow


class _Row(Protocol):
    KIND: ClassVar[str]
    id: Mapped[str]
    organization_id: Mapped[str]


class SharedRow(_Row, Protocol):
    """A row of an organization collection: shared with every workspace (`workspace_id` None) or confined to one."""

    workspace_id: Mapped[str | None]


class WorkspaceOwnedRow(_Row, Protocol):
    """A row of one workspace."""

    workspace_id: Mapped[str]


type Row = SharedRow | WorkspaceOwnedRow


class _EditedShared(SharedRow, Protocol):
    updated_by_id: Mapped[str]


class _EditedWorkspaceOwned(WorkspaceOwnedRow, Protocol):
    updated_by_id: Mapped[str]


type EditedRow = _EditedShared | _EditedWorkspaceOwned


async def find_row[R: Row](
    session: AsyncSession,
    actor: Principal,
    row_type: type[R],
    within: Scope | WorkspaceScope,
    row_id: str,
    verb: Verb,
    *,
    lock: bool = False,
    require_active: bool = True,
) -> R:
    """Row `row_id` of `within`, an organization collection or one workspace, on which the actor may `verb`.

    The path's organization is checked first. A row the actor cannot read is not found, revealing nothing. Every
    verb but `read` is refused on a row of an archived workspace unless `require_active=False` (offboarding):
    resource rows apply that rule here, as `workspace_scope` does for workspace paths.
    """
    authorize(actor, Scope(within.organization_id), "read")
    query = select(row_type).where(row_type.id == row_id, row_type.organization_id == within.organization_id)
    if within.workspace_id is not None:
        query = query.where(row_type.workspace_id == within.workspace_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None or "read" not in allowed_verbs(actor, _scope(row)):
        raise not_found(row_type.KIND, row_id)
    authorize(actor, _scope(row), verb)
    if require_active and verb != "read" and row.workspace_id is not None:
        refuse_archived(await session.get_one(WorkspaceRow, row.workspace_id))
    return row


def given(body: BaseModel, *names: str) -> dict[str, object]:
    """The named fields of a partial update that carry a value; one left out or null keeps the row's."""
    return {name: value for name in names if (value := getattr(body, name)) is not None}


def record_update(session: AsyncSession, actor: Principal, row: EditedRow, changed: Sequence[str]) -> bool:
    """Stamp and audit an update of the `changed` fields; one that changes nothing keeps the version and records
    nothing (False)."""
    if not changed:
        return False
    row.updated_by_id = actor.id
    audit_row(session, actor, row, "update", {"fields": list(changed)})
    return True


def audit_row(
    session: AsyncSession, actor: Principal, row: Row, verb: str, details: dict[str, JsonValue] | None = None
) -> None:
    """Record `<kind>.<verb>` on the row, in its own scope and the changing transaction."""
    record(
        session,
        _scope(row),
        actor_id=actor.id,
        action=f"{row.KIND}.{verb}",
        target_kind=row.KIND,
        target_id=row.id,
        details=details,
    )


def _scope(row: Row) -> Scope:
    return Scope(row.organization_id, row.workspace_id)
