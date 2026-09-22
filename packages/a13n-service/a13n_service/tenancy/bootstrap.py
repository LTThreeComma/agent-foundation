"""Create the first tenant and administrator atomically, once."""

from dataclasses import dataclass

from anyio.to_thread import run_sync
from argon2 import PasswordHasher
from pydantic import BaseModel, EmailStr, Field, SecretStr
from sqlalchemy import select

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, advisory_lock, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.tenancy.tables import GrantRow, OrganizationRow, PasswordRow, PrincipalRow, WorkspaceRow


class BootstrapInput(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=1024)


@dataclass(frozen=True)
class Bootstrapped:
    organization_id: str
    workspace_id: str
    principal_id: str


async def bootstrap(storage: Storage, request: BootstrapInput) -> Bootstrapped:
    password_hash = await run_sync(PasswordHasher().hash, request.password.get_secret_value())
    result = Bootstrapped(new_object_id("org"), new_object_id("ws"), new_object_id("usr"))
    async with transaction(storage) as session:
        await advisory_lock(session, 0xA13B007)
        if await session.scalar(select(OrganizationRow.id).limit(1)):
            raise ValueError("Service is already bootstrapped")
        session.add(OrganizationRow(id=result.organization_id, key="default", name="Default organization"))
        await session.flush()
        session.add(
            WorkspaceRow(
                id=result.workspace_id, organization_id=result.organization_id, key="default", name="Default workspace"
            )
        )
        session.add(
            PrincipalRow(id=result.principal_id, kind="user", name=str(request.email), email=str(request.email))
        )
        await session.flush()
        session.add(PasswordRow(principal_id=result.principal_id, hash=password_hash))
        session.add(
            GrantRow(
                id=new_object_id("rb"),
                organization_id=result.organization_id,
                principal_id=result.principal_id,
                role="admin",
                created_by_id=result.principal_id,
            )
        )
        record(
            session,
            organization_id=result.organization_id,
            workspace_id=result.workspace_id,
            actor_id=result.principal_id,
            action="organization.bootstrap",
            target_kind="organization",
            target_id=result.organization_id,
        )
    return result
