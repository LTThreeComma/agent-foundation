"""Local revocation precedes one bounded best-effort remote account revocation."""

import asyncio

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.connector.contracts import ConnectionBinding, ConnectorProviderError
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy import select

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.composio import ComposioConfig
from a13n_service.providers.tools import ConnectionProvider
from a13n_service.resources.connections import managed, oauth_tokens
from a13n_service.resources.connections import managed_operations as operations
from a13n_service.resources.connections.managed_transport import open_provider
from a13n_service.resources.connections.oauth_state import invalidate, reveal
from a13n_service.resources.connections.oauth_values import AuthorizationView
from a13n_service.resources.connections.schemas import parse_config
from a13n_service.resources.connections.service import ResolvedConnection, get_row
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow
from a13n_service.settings import Managed
from a13n_service.tenancy.authorize import Principal, execution_authority
from a13n_service.tenancy.grants import workspace_scope


async def revoke(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    connection_id: str,
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Managed,
    catalog: ProviderCatalog[ConnectionProvider],
) -> AuthorizationView:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        connection = await get_row(session, scope.workspace_id, connection_id, lock=True)
        selected = ResolvedConnection(
            connection.id,
            connection.organization_id,
            connection.workspace_id,
            connection.type,
            connection.version,
            parse_config(connection.type, connection.config),
            connection.auth,
            connection.credential,
        )
        if not isinstance(selected.config, ComposioConfig) or selected.auth != "managed":
            raise ServiceError("invalid_argument", "Connection does not use managed authentication")
        row = await session.scalar(
            select(ConnectionAuthorizationRow)
            .where(
                ConnectionAuthorizationRow.connection_id == selected.id,
                ConnectionAuthorizationRow.principal_id == actor.id,
            )
            .with_for_update()
        )
        if row is None:
            raise ServiceError("not_found", "Managed authorization was not found")
        if row.status == "revoked":
            return oauth_tokens.view(row)
        bundle = reveal(row, keys) if row.credential is not None else {}
        invalidate(row, "revoked", "remote_account_unknown")
        oauth_tokens.audit(session, row, "revoke")
        if not bundle.get("account_id") or not connection.enabled:
            return oauth_tokens.view(row)
        bundle["authority"] = execution_authority(actor, scope).model_dump(mode="json")
        owned = await operations.claim(session, row, connection, selected, bundle, kind="revoke", settings=settings)
    sent = False

    async def before(request):
        nonlocal sent
        await operations.before_request(storage, owned)
        sent = sent or request.method == "POST"

    try:
        async with (
            asyncio.timeout(settings.operation_seconds),
            open_provider(
                selected,
                managed.definition(catalog),
                keys=keys,
                policy=policy,
                before_request=before,
            ) as provider,
        ):
            account = provider.connect(
                ConnectionBinding(
                    connector_key=selected.config.app,
                    external_ref=bundle["account_id"],
                    external_user_correlation=bundle["user_id"],
                )
            )
            inspected = await account.inspect()
            managed.check_inspection(selected.config, bundle, inspected)
            await account.revoke(operation_id=owned.operation_id)
        await operations.publish(storage, owned, keys=keys)
    except ConnectorProviderError as error:
        if error.http_status == 400:
            await operations.publish(storage, owned, keys=keys, failure="remote_revoke_unsupported")
        else:
            await managed.failed(storage, owned, error, sent=sent, keys=keys)
    except BaseException as error:
        await managed.failed(storage, owned, error, sent=sent, keys=keys)
        if not isinstance(error, Exception):
            raise
    async with short_session(storage) as session:
        row = await session.get(ConnectionAuthorizationRow, owned.authorization_id)
        assert row is not None
        return oauth_tokens.view(row)
