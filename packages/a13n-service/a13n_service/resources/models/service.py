"""Authorized model configuration; endpoint preparation never holds a SQL session."""

import json

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model import ModelProviderDefinition
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.crypto import KeyRing, SecretLocation
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.models.schemas import (
    ModelCreate,
    ModelPage,
    ModelView,
    ProviderCreate,
    ProviderPage,
    ProviderView,
)
from a13n_service.resources.models.tables import ModelProviderRow, ModelRow
from a13n_service.tenancy.authorize import Principal, Scope, Verb, authorize
from a13n_service.tenancy.grants import readable_workspaces, workspace_scope
from a13n_service.tenancy.tables import OrganizationRow


async def configuration_scope(
    session: AsyncSession, actor: Principal, organization_id: str, workspace_id: str | None, verb: Verb
) -> Scope:
    scope = Scope(organization_id, workspace_id)
    authorize(actor, scope, verb)
    if workspace_id is not None:
        actual = await workspace_scope(session, actor, workspace_id, verb)
        if actual != scope:
            raise ServiceError("invalid_argument", "Workspace belongs to another organization")
    elif await session.get(OrganizationRow, organization_id) is None:
        raise ServiceError("not_found", "Organization was not found")
    return scope


def provider_view(row: ModelProviderRow) -> ProviderView:
    return ProviderView(
        id=row.id,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        type=row.type,
        name=row.name,
        config=row.config,
        credential_configured=row.credential is not None,
        enabled=row.enabled,
        version=row.version,
    )


async def create_provider(
    storage: Storage,
    actor: Principal,
    organization_id: str,
    body: ProviderCreate,
    *,
    catalog: ProviderCatalog[ModelProviderDefinition],
    policy: EndpointPolicy,
    keys: KeyRing,
) -> ProviderView:
    async with short_session(storage) as session:
        await configuration_scope(session, actor, organization_id, body.workspace_id, "write")
    try:
        definition = catalog.require(body.type)
        connection = definition.bind(body.config, body.credential)
        if connection.endpoint is not None:
            await policy.validate(connection.endpoint)
        configuration = connection.configuration.model_dump(mode="json")
    except ValueError:
        raise ServiceError("invalid_argument", "Invalid model provider configuration or credential") from None
    provider_id = new_object_id("mprov")
    envelope = None
    if body.credential is not None:
        envelope = keys.protect(
            json.dumps(body.credential, allow_nan=False).encode(),
            SecretLocation(organization_id, "model_providers", "credential", provider_id),
        ).model_dump(mode="json")
    async with transaction(storage) as session:
        row = ModelProviderRow(
            id=provider_id,
            organization_id=organization_id,
            workspace_id=body.workspace_id,
            type=body.type,
            name=body.name,
            config=configuration,
            credential=envelope,
            enabled=True,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        record(
            session,
            organization_id=organization_id,
            workspace_id=body.workspace_id,
            actor_id=actor.id,
            action="model_provider.create",
            target_kind="model_provider",
            target_id=row.id,
        )
        await session.flush()
        return provider_view(row)


async def create_model(
    storage: Storage,
    actor: Principal,
    organization_id: str,
    body: ModelCreate,
    *,
    catalog: ProviderCatalog[ModelProviderDefinition],
) -> ModelView:
    async with transaction(storage) as session:
        await configuration_scope(session, actor, organization_id, body.workspace_id, "write")
        provider = await session.get(ModelProviderRow, body.provider_id, with_for_update=True)
        if provider is None or provider.organization_id != organization_id:
            raise ServiceError("not_found", "Model provider was not found")
        authorize(actor, Scope(provider.organization_id, provider.workspace_id), "read")
        if not provider.enabled or (provider.workspace_id is not None and provider.workspace_id != body.workspace_id):
            raise ServiceError("invalid_argument", "Model provider is disabled or confined to another workspace")
        definition = catalog.require(provider.type)
        if body.config.model_api not in definition.supported_model_apis:
            raise ServiceError("invalid_argument", "Model API is not supported by this provider")
        if body.pricing is not None and (
            body.pricing.provider != provider.type or body.pricing.model != body.config.model_name
        ):
            raise ServiceError("invalid_argument", "Pricing must describe the selected provider and model")
        existing = await session.scalar(
            select(ModelRow.id).where(ModelRow.provider_id == provider.id, ModelRow.key == body.key)
        )
        if existing is not None:
            raise ServiceError("already_exists", "Model key already exists")
        row = ModelRow(
            id=new_object_id("mdl"),
            organization_id=organization_id,
            workspace_id=body.workspace_id,
            provider_id=provider.id,
            key=body.key,
            name=body.name,
            config=body.config.model_dump(mode="json"),
            pricing=body.pricing.model_dump(mode="json") if body.pricing is not None else None,
            enabled=True,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        record(
            session,
            organization_id=organization_id,
            workspace_id=body.workspace_id,
            actor_id=actor.id,
            action="model.create",
            target_kind="model",
            target_id=row.id,
        )
        await session.flush()
        return ModelView.model_validate(row)


async def get_provider(storage: Storage, actor: Principal, organization_id: str, provider_id: str) -> ProviderView:
    async with short_session(storage) as session:
        row = await session.get(ModelProviderRow, provider_id)
        if row is None or row.organization_id != organization_id:
            raise ServiceError("not_found", "Model provider was not found")
        authorize(actor, Scope(row.organization_id, row.workspace_id), "read")
        return provider_view(row)


async def get_model(storage: Storage, actor: Principal, organization_id: str, model_id: str) -> ModelView:
    async with short_session(storage) as session:
        row = await session.get(ModelRow, model_id)
        if row is None or row.organization_id != organization_id:
            raise ServiceError("not_found", "Model was not found")
        authorize(actor, Scope(row.organization_id, row.workspace_id), "read")
        return ModelView.model_validate(row)


async def list_models(
    storage: Storage,
    actor: Principal,
    organization_id: str,
    *,
    workspace_id: str | None,
    limit: int,
    cursor: str | None,
) -> ModelPage:
    async with short_session(storage) as session:
        await configuration_scope(session, actor, organization_id, workspace_id, "read")
        owner = organization_id + ":" + (workspace_id or "*")
        after = cursors.id_position(cursor, "models", owner)
        query = select(ModelRow).where(
            ModelRow.organization_id == organization_id,
            ModelRow.id > after,
            or_(ModelRow.workspace_id.is_(None), ModelRow.workspace_id.in_(readable_workspaces(actor))),
        )
        if workspace_id is not None:
            query = query.where(or_(ModelRow.workspace_id.is_(None), ModelRow.workspace_id == workspace_id))
        rows = (await session.scalars(query.order_by(ModelRow.id).limit(limit + 1))).all()
        return ModelPage(
            items=[ModelView.model_validate(row) for row in rows[:limit]],
            next_cursor=cursors.encode("models", owner, rows[limit - 1].id) if len(rows) > limit else None,
        )


async def list_providers(
    storage: Storage,
    actor: Principal,
    organization_id: str,
    *,
    workspace_id: str | None,
    limit: int,
    cursor: str | None,
) -> ProviderPage:
    async with short_session(storage) as session:
        await configuration_scope(session, actor, organization_id, workspace_id, "read")
        owner = organization_id + ":" + (workspace_id or "*")
        after = cursors.id_position(cursor, "providers", owner)
        query = select(ModelProviderRow).where(
            ModelProviderRow.organization_id == organization_id,
            ModelProviderRow.id > after,
            or_(ModelProviderRow.workspace_id.is_(None), ModelProviderRow.workspace_id.in_(readable_workspaces(actor))),
        )
        if workspace_id is not None:
            query = query.where(
                or_(ModelProviderRow.workspace_id.is_(None), ModelProviderRow.workspace_id == workspace_id)
            )
        rows = (await session.scalars(query.order_by(ModelProviderRow.id).limit(limit + 1))).all()
        return ProviderPage(
            items=[provider_view(row) for row in rows[:limit]],
            next_cursor=cursors.encode("providers", owner, rows[limit - 1].id) if len(rows) > limit else None,
        )
