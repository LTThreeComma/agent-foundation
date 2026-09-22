"""Current SQL identity/grants projected into detached authorization inputs."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError
from a13n_service.tenancy.authorize import Grant, Principal, Scope, Verb, authorize
from a13n_service.tenancy.tables import GrantRow, PrincipalRow, WorkspaceRow


async def principal_for(session: AsyncSession, principal_id: str, *, confinement: Scope | None = None) -> Principal:
    row = await session.get(PrincipalRow, principal_id)
    if row is None or row.status != "active":
        raise ServiceError("unauthenticated", "Authentication is required")
    if row.kind not in {"user", "service_account"}:
        raise ServiceError("forbidden", "Principal kind is unsupported")
    if row.kind == "service_account":
        home = await session.get(WorkspaceRow, row.home_workspace_id)
        if home is None or (confinement is not None and confinement != Scope(home.organization_id, home.id)):
            raise ServiceError("forbidden", "Service account cannot leave its home workspace")
        confinement = Scope(home.organization_id, home.id)
    grants = (await session.scalars(select(GrantRow).where(GrantRow.principal_id == row.id))).all()
    principal = Principal(
        row.id,
        "user" if row.kind == "user" else "service_account",
        tuple(Grant(g.organization_id, g.workspace_id, g.role) for g in grants),
        confinement,
        name=row.name,
        email=row.email,
    )
    return principal


async def workspace_scope(
    session: AsyncSession, principal: Principal, workspace_id: str, verb: Verb, *, require_active: bool = True
) -> Scope:
    workspace = await session.get(WorkspaceRow, workspace_id)
    if workspace is None:
        raise ServiceError("not_found", "Workspace was not found", {"kind": "workspace", "id": workspace_id})
    scope = Scope(workspace.organization_id, workspace.id)
    authorize(principal, scope, verb)
    if require_active and workspace.archived_at is not None and verb != "read":
        raise ServiceError("disabled", "Workspace is archived", {"kind": "workspace", "id": workspace.id})
    return scope
