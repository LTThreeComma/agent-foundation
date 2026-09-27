"""Scope rules of organization collections that hold shared (`workspace_id IS NULL`) and workspace rows.

Providers use them; the verb split for shared rows is tenancy's one rule in `authorize`.
"""

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from a13n_service.infra import cursors
from a13n_service.infra.errors import disabled, not_found
from a13n_service.resources.rows import SharedRow
from a13n_service.tenancy.access import readable_workspaces, workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Scope, Verb, WorkspaceScope, authorize


class SwitchableRow(SharedRow, Protocol):
    enabled: Mapped[bool]


async def writable_scope(
    session: AsyncSession, actor: Principal, organization_id: str, workspace_id: str | None
) -> Scope:
    """Where a new row goes: a workspace the caller may write, or `None`, which needs organization write."""
    if workspace_id is None:
        scope = Scope(organization_id)
        authorize(actor, scope, "write")
        return scope
    workspace = await workspace_scope(session, actor, workspace_id, "write")
    if workspace.organization_id != organization_id:
        raise not_found("workspace", workspace_id)
    return Scope(workspace.organization_id, workspace.workspace_id)


async def list_rows[R: SharedRow](
    session: AsyncSession,
    actor: Principal,
    row_type: type[R],
    organization_id: str,
    workspace_id: str | None,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[Sequence[R], str | None]:
    """One page of the rows a list returns: shared rows plus those of the requested workspace.

    Without a workspace filter, rows of every workspace the caller reads, so only organization-scope grants
    enumerate the whole organization.
    """
    # The workspace as resolved, so a cursor continues its list whether the caller names it by ID or key.
    resolved: str | None = None
    if workspace_id is None:
        authorize(actor, Scope(organization_id), "read")
        visible: ColumnElement[bool] = row_type.workspace_id.in_(readable_workspaces(actor))
    else:
        workspace = await workspace_scope(session, actor, workspace_id, "read")
        if workspace.organization_id != organization_id:
            raise not_found("workspace", workspace_id)
        resolved = workspace.workspace_id
        visible = row_type.workspace_id == resolved
    return await cursors.id_page(
        session,
        select(row_type).where(
            row_type.organization_id == organization_id, or_(row_type.workspace_id.is_(None), visible)
        ),
        row_type.id,
        kind=row_type.KIND,
        owner=cursors.query_owner(organization_id, resolved),
        cursor=cursor,
        limit=limit,
    )


async def usable_row[R: SwitchableRow](
    session: AsyncSession,
    actor: Principal,
    row_type: type[R],
    scope: WorkspaceScope,
    row_id: str,
    *,
    verb: Verb,
    authority: ExecutionAuthority | None,
) -> R:
    """An enabled row usable in the workspace, its own or shared, read in the caller's session."""
    row = await session.get(row_type, row_id)
    if (
        row is None
        or row.organization_id != scope.organization_id
        or row.workspace_id not in {None, scope.workspace_id}
    ):
        raise not_found(row_type.KIND, row_id)
    authorize(actor, Scope(row.organization_id, row.workspace_id), verb, authority=authority)
    if not row.enabled:
        raise disabled(row_type.KIND, row_id)
    return row
