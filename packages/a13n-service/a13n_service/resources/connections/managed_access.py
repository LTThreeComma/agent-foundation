"""Fresh private account binding checks shared by discovery and native execution."""

from dataclasses import dataclass, field

from sqlalchemy import select

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.connections.oauth_state import identity, reveal
from a13n_service.resources.connections.service import ResolvedConnection, get_row
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Scope, authorize
from a13n_service.tenancy.grants import principal_for, workspace_scope


@dataclass(frozen=True)
class ManagedAccess:
    authorization_id: str
    generation: int
    principal_id: str
    bundle: dict = field(repr=False)


async def access(storage: Storage, actor: Principal, selected: ResolvedConnection, *, keys: KeyRing) -> ManagedAccess:
    async with short_session(storage) as session:
        connection = await get_row(session, selected.workspace_id, selected.id)
        row = await session.scalar(
            select(ConnectionAuthorizationRow).where(
                ConnectionAuthorizationRow.connection_id == selected.id,
                ConnectionAuthorizationRow.principal_id == actor.id,
            )
        )
        if row is None or row.status != "active" or row.credential is None:
            raise ServiceError(
                "disabled", "Connect your personal managed account", {"reason": "authorization_required"}
            )
        bundle = reveal(row, keys)
        if selected.auth != "managed" or not connection.enabled or bundle["identity"] != identity(connection):
            raise ServiceError("disabled", "Managed account requires reauthorization")
        return ManagedAccess(row.id, row.generation, actor.id, bundle)


async def check(storage: Storage, owned: ManagedAccess, selected: ResolvedConnection) -> None:
    async with short_session(storage) as session:
        connection = await get_row(session, selected.workspace_id, selected.id)
        row = await session.get(ConnectionAuthorizationRow, owned.authorization_id)
        actor = await principal_for(
            session, owned.principal_id, confinement=Scope(selected.organization_id, selected.workspace_id)
        )
        scope = await workspace_scope(session, actor, selected.workspace_id, "run")
        authorize(actor, scope, "run", authority=ExecutionAuthority.model_validate(owned.bundle["authority"]))
        if (
            row is None
            or row.connection_id != selected.id
            or row.principal_id != owned.principal_id
            or row.status != "active"
            or row.generation != owned.generation
            or row.credential is None
            or connection.version != selected.version
            or not connection.enabled
            or identity(connection) != owned.bundle["identity"]
        ):
            raise ServiceError("disabled", "Managed account changed; reconnect and start a fresh run")


async def discover(
    storage: Storage,
    actor: Principal,
    selected: ResolvedConnection,
    *,
    keys: KeyRing,
    policy,
    definition,
):
    from a13n_harness.providers.connector.contracts import ConnectionBinding

    from a13n_service.providers.composio import ComposioConfig
    from a13n_service.providers.tools import ToolInfo
    from a13n_service.resources.connections.managed import check_inspection
    from a13n_service.resources.connections.managed_transport import actions, open_provider
    from a13n_service.resources.connections.schemas import ConnectionTest

    assert isinstance(selected.config, ComposioConfig)
    owned = await access(storage, actor, selected, keys=keys)

    async def before(request):
        await check(storage, owned, selected)

    async with open_provider(selected, definition, keys=keys, policy=policy, before_request=before) as provider:
        account = provider.connect(
            ConnectionBinding(
                connector_key=selected.config.app,
                external_ref=owned.bundle["account_id"],
                external_user_correlation=owned.bundle["user_id"],
            )
        )
        check_inspection(selected.config, owned.bundle, await account.inspect(), active=True)
        tools = await actions(provider, selected.config)
    await check(storage, owned, selected)
    return ConnectionTest(
        connection_id=selected.id,
        version=selected.version,
        tools=[ToolInfo(name=tool.key, description=tool.description, input_schema=tool.input_schema) for tool in tools],
    )
