"""Current SQL identity/grants projected into detached authorization inputs."""

from sqlalchemy import and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError
from a13n_service.tenancy.authorize import Grant, Principal, Scope, Verb, allowed_verbs, authorize
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


async def resolve_workspace(session: AsyncSession, reference: str) -> WorkspaceRow:
    workspace = await session.get(WorkspaceRow, reference)
    if workspace is not None:
        return workspace
    rows = (await session.scalars(select(WorkspaceRow).where(WorkspaceRow.key == reference).limit(2))).all()
    if not rows:
        raise ServiceError("not_found", "Workspace was not found")
    if len(rows) > 1:
        raise ServiceError("conflict", "Workspace key is ambiguous; use a workspace ID")
    return rows[0]


async def workspace_scope(
    session: AsyncSession, principal: Principal, workspace_id: str, verb: Verb, *, require_active: bool = True
) -> Scope:
    workspace = await resolve_workspace(session, workspace_id)
    scope = Scope(workspace.organization_id, workspace.id)
    authorize(principal, scope, verb)
    if require_active and workspace.archived_at is not None and verb != "read":
        raise ServiceError("disabled", "Workspace is archived", {"kind": "workspace", "id": workspace.id})
    return scope


def readable_workspaces(principal: Principal):
    # Validate role vocabulary through the canonical authorization owner.
    allowed_verbs(principal, Scope(""))
    predicates = []
    for grant in principal.grants:
        scope = Scope(grant.organization_id, grant.workspace_id)
        if "read" not in allowed_verbs(principal, scope):
            continue
        clause = WorkspaceRow.organization_id == grant.organization_id
        if grant.workspace_id is not None:
            clause = and_(clause, WorkspaceRow.id == grant.workspace_id)
        predicates.append(clause)
    query = select(WorkspaceRow.id).where(or_(*predicates) if predicates else false())
    if principal.confinement is not None:
        query = query.where(WorkspaceRow.id == principal.confinement.workspace_id)
    return query
