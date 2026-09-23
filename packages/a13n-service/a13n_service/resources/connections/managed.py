"""Principal-owned hosted enrollment through the shared native Connector provider."""

import asyncio
import secrets
from datetime import timedelta

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.connector import ConnectorProviderDefinition
from a13n_harness.providers.connector.contracts import (
    AdapterConnectionStatus,
    ConnectionInspection,
    ConnectorProviderError,
    SetupCompletionMethod,
    SetupContext,
)
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.composio import ComposioConfig
from a13n_service.providers.tools import ConnectionProvider
from a13n_service.resources.connections import managed_operations as operations
from a13n_service.resources.connections import oauth_tokens
from a13n_service.resources.connections.managed_transport import open_provider
from a13n_service.resources.connections.managed_values import ManagedClaim, cookie_name
from a13n_service.resources.connections.oauth_state import identity, seal
from a13n_service.resources.connections.oauth_values import AuthorizationStart, BrowserFlow
from a13n_service.resources.connections.service import get_row, resolve
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow
from a13n_service.settings import Managed
from a13n_service.tenancy.authenticate import Authenticated, secret_hash
from a13n_service.tenancy.authorize import execution_authority
from a13n_service.tenancy.grants import workspace_scope


def definition(catalog: ProviderCatalog[ConnectionProvider]) -> ConnectorProviderDefinition:
    value = catalog.require("composio")
    if not isinstance(value, ConnectorProviderDefinition):
        raise ServiceError("unavailable", "Managed provider is unavailable")
    return value


def context(owned: ManagedClaim) -> SetupContext:
    config = owned.selected.config
    assert isinstance(config, ComposioConfig)
    return SetupContext(
        connector_key=config.app,
        external_user_correlation=owned.bundle["user_id"],
        callback_url=owned.bundle["verifier_url"],
    )


def check_inspection(
    config: ComposioConfig, bundle: dict, value: ConnectionInspection, *, active: bool = False
) -> None:
    scheme = value.safe_metadata.get("auth_scheme")
    if (
        value.external_ref != bundle["account_id"]
        or value.external_user_correlation != bundle["user_id"]
        or value.connector_key != config.app
        or value.safe_metadata.get("auth_config_id") != config.auth_config_id
        or scheme not in {"OAUTH2", "API_KEY", "BEARER_TOKEN", "BASIC"}
        or (bundle.get("scheme") is not None and scheme != bundle["scheme"])
        or value.status not in {AdapterConnectionStatus.pending, AdapterConnectionStatus.ready}
        or (active and value.status != AdapterConnectionStatus.ready)
    ):
        raise ServiceError(
            "conflict",
            "Managed account does not match the selected authorization",
            {"reason": "account_binding_rejected"},
        )


async def start(
    storage: Storage,
    credential: Authenticated,
    workspace_id: str,
    connection_id: str,
    return_url: str,
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Managed,
    catalog: ProviderCatalog[ConnectionProvider],
) -> BrowserFlow:
    if settings.verifier_url is None or return_url not in settings.return_urls:
        raise ServiceError("invalid_argument", "Managed verifier or return target is not configured")
    try:
        await policy.validate(settings.verifier_url, resolve_dns=False)
        await policy.validate(return_url, resolve_dns=False)
    except ValueError:
        raise ServiceError("invalid_argument", "Managed browser target is not permitted") from None
    actor = credential.principal
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        selected = await resolve(session, actor, scope, connection_id, verb="run")
        if selected.auth != "managed" or not isinstance(selected.config, ComposioConfig):
            raise ServiceError("invalid_argument", "Connection does not use managed authentication")
        ceiling = execution_authority(actor, scope)
    binding = secrets.token_urlsafe(32)
    async with transaction(storage) as session:
        connection = await get_row(session, selected.workspace_id, connection_id, lock=True)
        if connection.version != selected.version or not connection.enabled:
            raise ServiceError("conflict", "Connection changed during enrollment")
        await session.execute(
            insert(ConnectionAuthorizationRow)
            .values(
                id=new_object_id("cauth"),
                organization_id=selected.organization_id,
                workspace_id=selected.workspace_id,
                connection_id=connection_id,
                principal_id=actor.id,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["connection_id", "principal_id"])
        )
        row = await session.scalar(
            select(ConnectionAuthorizationRow)
            .where(
                ConnectionAuthorizationRow.connection_id == connection_id,
                ConnectionAuthorizationRow.principal_id == actor.id,
            )
            .with_for_update()
        )
        assert row is not None
        row.generation += 1
        row.status = "pending"
        row.oauth_state_hash = None
        row.redirect_uri, row.return_uri = settings.verifier_url, return_url
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        row.expires_at = now + timedelta(seconds=settings.flow_seconds)
        bundle = {
            "identity": identity(connection),
            "authority": ceiling.model_dump(mode="json"),
            "user_id": new_object_id("usr"),
            "binding_hash": secret_hash(binding),
            "session_id": credential.credential_id if credential.kind == "session" else None,
            "verifier_url": settings.verifier_url,
            "return_url": return_url,
        }
        seal(row, bundle, keys)
        owned = await operations.claim(session, row, connection, selected, bundle, kind="setup", settings=settings)
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
                definition(catalog),
                keys=keys,
                policy=policy,
                before_request=before,
            ) as provider,
        ):
            started = await provider.start_setup(setup=selected.config.setup(), context=context(owned))
            if started.external_ref is None or started.redirect_url is None:
                raise ServiceError("unavailable", "Managed enrollment did not return an account and link")
            bundle["account_id"] = started.external_ref
            inspected = await provider.inspect_setup(setup_ref=started.external_ref, context=context(owned))
            if inspected is None:
                raise ServiceError("unavailable", "Managed account could not be inspected")
            check_inspection(selected.config, bundle, inspected)
            bundle["scheme"] = inspected.safe_metadata["auth_scheme"]
            expected = (
                SetupCompletionMethod.oauth_verifier
                if bundle["scheme"] == "OAUTH2"
                else SetupCompletionMethod.browser_confirmation
            )
            if started.completion_method != expected:
                raise ServiceError("conflict", "Managed completion method does not match the account")
            bundle["completion_method"] = expected.value
        if not await operations.publish(storage, owned, keys=keys, bundle=bundle, expires_at=started.expires_at):
            raise ServiceError("conflict", "Managed enrollment changed before publication")
    except BaseException as error:
        await failed(storage, owned, error, sent=sent, keys=keys)
        raise
    async with short_session(storage) as session:
        row = await session.get(ConnectionAuthorizationRow, owned.authorization_id)
        assert row is not None
        response = AuthorizationStart(authorization=oauth_tokens.view(row), redirect_url=started.redirect_url)
    return BrowserFlow(response, cookie_name(owned.authorization_id, owned.generation), binding)


async def failed(storage: Storage, owned: ManagedClaim, error: BaseException, *, sent: bool, keys: KeyRing) -> None:
    import anyio

    binding_rejected = (
        isinstance(error, ConnectorProviderError) and error.code in {"connection_substitution", "provider_mismatch"}
    ) or (isinstance(error, ServiceError) and error.details.get("reason") == "account_binding_rejected")
    unknown = sent and (not isinstance(error, ConnectorProviderError) or error.outcome_unknown)
    with anyio.move_on_after(3, shield=True):
        await operations.publish(
            storage,
            owned,
            keys=keys,
            failure="account_binding_rejected"
            if binding_rejected
            else "unknown_after_dispatch"
            if unknown
            else "provider_rejected",
        )
