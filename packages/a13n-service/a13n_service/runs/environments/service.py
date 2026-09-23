"""Environment instances as workspace resources: reads, managed reservation, device registration, rename, stop
and delete.

Stop and delete arbitrate with active use under the environment lock and only begin an operation; the provider
calls happen in the fenced lifecycle, never in the request.
"""

import anyio
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.management import Environment
from a13n_harness.providers.environment.models import EnvironmentError as OperationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments.adapters import Target, close, construct, provider_identity
from a13n_service.runs.environments.lifecycle import (
    PERMANENT,
    PHASES,
    begin,
    in_use,
    mounted,
    reserve,
    settled,
    supports,
)
from a13n_service.runs.environments.schemas import (
    DeviceRegistration,
    EnvironmentPage,
    EnvironmentUpdate,
    EnvironmentView,
    Handle,
    ManagedEnvironmentCreate,
)
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, allowed_verbs


def _audit(session: AsyncSession, actor: Principal, environment: EnvironmentRow, verb: str) -> None:
    record(
        session,
        WorkspaceScope(environment.organization_id, environment.workspace_id),
        actor_id=actor.id,
        action=f"environment.{verb}",
        target_kind="environment",
        target_id=environment.id,
    )


async def _find(
    session: AsyncSession, scope: WorkspaceScope, environment_id: str, *, lock: bool = False
) -> EnvironmentRow:
    query = select(EnvironmentRow).where(
        EnvironmentRow.workspace_id == scope.workspace_id, EnvironmentRow.id == environment_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None:
        raise not_found("environment", environment_id)
    return row


def _require_manager(actor: Principal, scope: WorkspaceScope, environment: EnvironmentRow) -> None:
    """A private device is managed by its owner, or by a workspace administrator removing it."""
    if environment.owner_principal_id not in {None, actor.id} and "admin" not in allowed_verbs(actor, scope):
        raise ServiceError("forbidden", "Private environments are managed by their owner", {"verb": "write"})


async def list_environments(
    storage: Storage, actor: Principal, workspace_id: str, *, status: str | None, limit: int, cursor: str | None
) -> EnvironmentPage:
    """Instances of the workspace; deleted tombstones only when asked for with `status=deleted`."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(EnvironmentRow).where(
                EnvironmentRow.workspace_id == scope.workspace_id,
                EnvironmentRow.status == status if status is not None else EnvironmentRow.status != "deleted",
            ),
            EnvironmentRow.id,
            kind="environments",
            owner=cursors.query_owner(scope.workspace_id, status),
            cursor=cursor,
            limit=limit,
        )
    return EnvironmentPage(items=[EnvironmentView.model_validate(row) for row in rows], next_cursor=next_cursor)


async def get_environment(
    storage: Storage, actor: Principal, workspace_id: str, environment_id: str
) -> EnvironmentView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return EnvironmentView.model_validate(await _find(session, scope, environment_id))


async def reserve_environment(
    runtime: Runtime, actor: Principal, workspace_id: str, body: ManagedEnvironmentCreate
) -> EnvironmentView:
    """A workspace-managed sandbox for threads to mount, reserved as acceptance reserves a primary sandbox.

    Maintenance dispatches its create operation. From then on the template's live idle policy applies as to any
    instance: `stop_after_seconds` without a run using it stops it and, while no thread mounts it either,
    `delete_after_seconds` deletes it; both count from its last use, becoming ready included.
    """
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        limit = runtime.settings.environments.managed_count
        environment = await reserve(session, actor, scope, body.template_id, limit=limit, name=body.name)
        _audit(session, actor, environment, "create")
        return EnvironmentView.model_validate(environment)


async def register_device(
    runtime: Runtime, actor: Principal, workspace_id: str, body: DeviceRegistration
) -> EnvironmentView:
    """A private, connect-only device: its identity is verified outside any transaction, then recorded."""
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        provider = await resolve_provider(session, actor, EnvironmentProviderRow, scope, body.provider_id)
    if runtime.registry.get("environment", provider.type).supports_managed:
        raise invalid("provider_id", f"{provider.type} environments are created from templates")
    environment_id = new_object_id("env")
    # The state names the device; its endpoint and credential stay with the provider.
    state = runtime.registry.environment_device_state(provider.type, body.device_id)
    await _verify(runtime, Target(environment_id, provider, {}, state))
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        current = await resolve_provider(session, actor, EnvironmentProviderRow, scope, body.provider_id)
        if current.version != provider.version:
            raise conflict("environment_provider", body.provider_id, "changed_during_registration")
        environment = EnvironmentRow(
            id=environment_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            provider_id=body.provider_id,
            provider_identity=provider_identity(runtime.registry, current),
            template_id=None,
            device_id=body.device_id,
            owner_principal_id=actor.id,
            name=body.name or body.device_id,
            status="ready",
            handle=Handle(recipe={}, state=state).model_dump(mode="json"),
            generation=0,
            created_by_id=actor.id,
        )
        session.add(environment)
        await session.flush()
        _audit(session, actor, environment, "register")
        return EnvironmentView.model_validate(environment)


async def _verify(runtime: Runtime, target: Target) -> None:
    """Open and close one session on the device, proving its endpoint, credential and native identity.

    A refusal that repeating cannot overcome (denied, missing, invalid) conflicts with the provider's configuration;
    anything else is the device being unavailable for now.
    """
    adapter: Environment | None = None
    try:
        with anyio.fail_after(runtime.settings.providers.operation_seconds):
            adapter = await construct(runtime, target, operation_id=None, allow_create=False)
            await adapter.prepare()
    except EnvironmentProviderError as error:
        if error.category in PERMANENT:
            raise conflict("environment_provider", target.provider.id, error.code) from None
        raise _unverified(target, error.code) from None
    except (OperationError, TimeoutError) as error:
        raise _unverified(target, "environment_timeout" if isinstance(error, TimeoutError) else error.code) from None
    finally:
        if adapter is not None:
            await close(adapter)


def _unverified(target: Target, reason: str) -> ServiceError:
    return ServiceError(
        "unavailable",
        "The device could not be verified",
        {"dependency": f"environment:{target.provider.type}", "reason": reason},
    )


async def rename_environment(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    environment_id: str,
    body: EnvironmentUpdate,
    *,
    if_match: str | None,
) -> EnvironmentView:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        environment = await _find(session, scope, environment_id, lock=True)
        require_match(if_match, environment.id, environment.version)
        _require_manager(actor, scope, environment)
        if environment.name != body.name:
            environment.name = body.name
            await session.flush()
            _audit(session, actor, environment, "update")
        return EnvironmentView.model_validate(environment)


async def stop_environment(
    runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str, *, if_match: str | None
) -> EnvironmentView:
    """Begin stopping an idle ready instance; a run accepted afterwards waits for the stop, then starts it."""
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        environment = await _find(session, scope, environment_id, lock=True)
        require_match(if_match, environment.id, environment.version)
        _require_manager(actor, scope, environment)
        if environment.template_id is None or environment.provider_identity is None:
            raise conflict("environment", environment.id, "connect_only")
        if environment.status != "ready":
            raise conflict("environment", environment.id, f"environment_{environment.status}")
        if not supports(runtime.registry.get("environment", environment.provider_identity["type"]), "stopping"):
            raise conflict("environment", environment.id, "stop_unsupported")
        if await session.scalar(select(in_use(environment.id))):
            raise conflict("environment", environment.id, "in_use")
        await begin(session, environment, "stopping")
        await session.flush()
        _audit(session, actor, environment, "stop")
        return EnvironmentView.model_validate(environment)


async def delete_environment(
    runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str, *, if_match: str | None
) -> EnvironmentView:
    """Retire an instance no thread mounts and no active run uses; a managed one is destroyed by the lifecycle.

    A different operation replaces an outstanding one only once it provably can no longer take effect.
    """
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        environment = await _find(session, scope, environment_id, lock=True)
        require_match(if_match, environment.id, environment.version)
        _require_manager(actor, scope, environment)
        if environment.status in {"deleting", "deleted"}:
            return EnvironmentView.model_validate(environment)
        if await session.scalar(select(mounted(environment.id))):
            raise conflict("environment", environment.id, "mounted")
        if await session.scalar(select(in_use(environment.id))):
            raise conflict("environment", environment.id, "in_use")
        if environment.status in PHASES and not settled(environment):
            raise conflict("environment", environment.id, "operation_unresolved")
        if environment.template_id is None or environment.provider_identity is None:
            # Nothing to destroy: a device keeps running outside the Service, and an unclaimed reservation
            # never reached its provider.
            environment.status = "deleted"
            environment.operation_id = environment.operation_started_at = environment.operation_deadline = None
            environment.handle = environment.failure = None
        elif not supports(runtime.registry.get("environment", environment.provider_identity["type"]), "deleting"):
            raise conflict("environment", environment.id, "destroy_unsupported")
        else:
            await begin(session, environment, "deleting")
        await session.flush()
        _audit(session, actor, environment, "delete")
        return EnvironmentView.model_validate(environment)
