"""Authenticated one-shot managed completion; uncertain completion only inspects."""

import asyncio
import hmac
from collections.abc import Mapping

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy import func, select

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.composio import ComposioConfig
from a13n_service.providers.tools import ConnectionProvider
from a13n_service.resources.connections import managed
from a13n_service.resources.connections import managed_operations as operations
from a13n_service.resources.connections.managed_transport import open_provider
from a13n_service.resources.connections.managed_values import ManagedCompletion, cookie_name
from a13n_service.resources.connections.oauth_state import identity, reveal
from a13n_service.resources.connections.service import get_row, resolve
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow
from a13n_service.settings import Managed
from a13n_service.tenancy.authenticate import Authenticated, secret_hash
from a13n_service.tenancy.authorize import ExecutionAuthority, authorize
from a13n_service.tenancy.grants import workspace_scope


async def complete(
    storage: Storage,
    credential: Authenticated,
    workspace_id: str,
    connection_id: str,
    body: ManagedCompletion,
    cookies: Mapping[str, str],
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Managed,
    catalog: ProviderCatalog[ConnectionProvider],
) -> str:
    if credential.kind != "session":
        raise ServiceError("forbidden", "Complete enrollment using the authenticated browser session")
    actor = credential.principal
    joining = False
    owned = None
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        selected = await resolve(session, actor, scope, connection_id, verb="run")
        if selected.auth != "managed" or not isinstance(selected.config, ComposioConfig):
            raise ServiceError("invalid_argument", "Connection does not use managed authentication")
        connection = await get_row(session, selected.workspace_id, selected.id, lock=True)
        row = await session.get(ConnectionAuthorizationRow, body.authorization_id, with_for_update=True)
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        if (
            row is None
            or row.connection_id != selected.id
            or row.principal_id != actor.id
            or row.generation != body.generation
            or row.status != "pending"
            or row.credential is None
            or row.expires_at is None
            or row.expires_at <= now
        ):
            raise ServiceError("conflict", "Managed enrollment expired or changed; reconnect")
        bundle = reveal(row, keys)
        if (
            bundle["identity"] != identity(connection)
            or bundle["verifier_url"] != settings.verifier_url
            or bundle["return_url"] not in settings.return_urls
            or (bundle["session_id"] is not None and bundle["session_id"] != credential.credential_id)
            or not hmac.compare_digest(
                secret_hash(cookies.get(cookie_name(row.id, row.generation), "")), bundle["binding_hash"]
            )
        ):
            raise ServiceError("forbidden", "Managed enrollment does not belong to this browser session")
        authorize(actor, scope, "run", authority=ExecutionAuthority.model_validate(bundle["authority"]))
        inspect_only = False
        if row.operation_id is not None:
            if row.operation_kind != "complete" or row.operation_deadline is None:
                raise ServiceError("conflict", "Managed operation is in progress")
            inspect_only = row.failure == {"reason": "unknown_after_dispatch"} or row.operation_deadline <= now
            if not inspect_only:
                joining = True
        if (
            not joining
            and not inspect_only
            and (bundle["completion_method"] == "oauth_verifier") != (body.session_uri is not None)
        ):
            raise ServiceError("invalid_argument", "Managed completion requires the selected browser method")
        if not joining:
            owned = await operations.claim(
                session, row, connection, selected, bundle, kind="complete", settings=settings
            )
    if joining:
        await joined(storage, body, selected, actor, settings.operation_seconds)
        return bundle["return_url"]
    assert owned is not None
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
            inspected = await provider.inspect_setup(setup_ref=bundle["account_id"], context=managed.context(owned))
            if inspected is None:
                raise ServiceError("unavailable", "Managed account could not be inspected")
            managed.check_inspection(selected.config, bundle, inspected)
            if not inspect_only and body.session_uri is not None:
                inspected = await provider.complete_setup(
                    session_uri=body.session_uri.get_secret_value(),
                    context=managed.context(owned),
                    expected_external_ref=bundle["account_id"],
                )
            managed.check_inspection(selected.config, bundle, inspected, active=True)
        if not await operations.publish(storage, owned, keys=keys, bundle=bundle, active=True):
            raise ServiceError("conflict", "Managed enrollment changed before publication")
    except BaseException as error:
        await managed.failed(storage, owned, error, sent=sent, keys=keys)
        raise
    return bundle["return_url"]


async def joined(storage, body, selected, actor, seconds: float) -> None:
    """Observe the claimed completion without retaining a SQL scope or replaying its URI."""
    from a13n_service.infra.db import short_session
    from a13n_service.tenancy.grants import principal_for

    try:
        async with asyncio.timeout(seconds):
            while True:
                async with short_session(storage) as session:
                    current = await principal_for(session, actor.id, confinement=actor.confinement)
                    scope = await workspace_scope(session, current, selected.workspace_id, "run")
                    live = await resolve(session, current, scope, selected.id, verb="run")
                    row = await session.get(ConnectionAuthorizationRow, body.authorization_id)
                    if row is None or row.generation != body.generation or live.version != selected.version:
                        raise ServiceError("conflict", "Managed enrollment changed during completion")
                    if row.status == "active":
                        return
                    if row.status != "pending" or row.failure is not None:
                        raise ServiceError("conflict", "Managed completion did not establish an account; reconnect")
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise ServiceError("unavailable", "Managed completion is awaiting deadline recovery") from None
