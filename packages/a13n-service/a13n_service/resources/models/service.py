"""Models in the organization collection, and their resolution for execution and for referencing resources.

A model's scope must be covered by its provider's: a shared provider serves shared and workspace models, a
workspace provider only models of its own workspace. A model spends its provider's credential, so creating one
or changing its configuration needs `write` on the provider too: models under an organization-shared provider
are configured by organization-scope grants, and workspaces use them.
"""

from dataclasses import dataclass

from a13n_harness import ModelCapability
from a13n_harness.pricing import ModelPricingEntry
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.toolsets.file_media import NativeInputMediaKind
from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, assign, short_session, transaction, unique_key
from a13n_service.infra.errors import disabled, invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.registry import Registry
from a13n_service.resources.models.catalog import known_model, known_models, serves
from a13n_service.resources.models.schemas import (
    CatalogModel,
    CatalogPage,
    Model,
    ModelConfig,
    ModelCreate,
    ModelPage,
    ModelUpdate,
)
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.providers.scope import list_rows, usable_row, writable_scope
from a13n_service.resources.providers.service import ResolvedProvider, get_provider, resolve_provider
from a13n_service.resources.providers.tables import ModelProviderRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Scope, Verb, WorkspaceScope


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    """A model as execution uses it; its provider's credential stays encrypted until the model is opened."""

    id: str
    version: int
    config: ModelConfig
    pricing: ModelPricingEntry | None
    provider: ResolvedProvider


async def resolve_model(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    model_id: str,
    *,
    verb: Verb = "run",
    authority: ExecutionAuthority | None = None,
) -> ResolvedModel:
    """An enabled model of an enabled provider usable in the workspace, read in the caller's short session."""
    row = await usable_row(session, actor, ModelRow, scope, model_id, verb=verb, authority=authority)
    provider = await resolve_provider(
        session, actor, ModelProviderRow, scope, row.provider_id, verb=verb, authority=authority
    )
    return ResolvedModel(
        id=row.id,
        version=row.version,
        config=ModelConfig.model_validate(row.config),
        pricing=_pricing(row),
        provider=provider,
    )


async def resolve_media_model(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    kind: NativeInputMediaKind,
    model_id: str,
    *,
    verb: Verb = "run",
    authority: ExecutionAuthority | None = None,
) -> ResolvedModel:
    """`resolve_model` for a model that describes `kind` media, which it must declare it understands."""
    model = await resolve_model(session, actor, scope, model_id, verb=verb, authority=authority)
    require_understanding(model, kind)
    return model


def require_understanding(model: ResolvedModel, kind: NativeInputMediaKind) -> None:
    if ModelCapability(f"{kind}_understanding") not in model.config.characteristics.capabilities:
        raise invalid("model", f"does not declare {kind}_understanding")


async def create_model(
    storage: Storage, actor: Principal, organization_id: str, body: ModelCreate, *, registry: Registry
) -> Model:
    known = None if body.catalog_key is None else await to_thread.run_sync(known_model, body.catalog_key)
    if body.catalog_key is not None and known is None:
        raise invalid("catalog_key", "not a catalogue model")
    with unique_key(ModelRow.KIND, "uq_models_provider_id_key", body.key):
        async with transaction(storage) as session:
            scope = await writable_scope(session, actor, organization_id, body.workspace_id)
            provider = await _configured_provider(session, actor, organization_id, body.provider_id)
            if provider.workspace_id not in {None, scope.workspace_id}:
                raise invalid("workspace_id", "the provider is confined to another workspace")
            if not provider.enabled:
                raise disabled(provider.KIND, provider.id)
            config, pricing = _source(body, known, registry.get("model", provider.type))
            _check_pricing(config, pricing)
            row = ModelRow(
                id=new_object_id("mdl"),
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                provider_id=provider.id,
                key=body.key,
                name=body.name,
                description=body.description,
                config=config.model_dump(mode="json"),
                pricing=None if pricing is None else pricing.model_dump(mode="json"),
                enabled=body.enabled,
                created_by_id=actor.id,
                updated_by_id=actor.id,
            )
            session.add(row)
            await session.flush()
            audit_row(session, actor, row, "create")
            return Model.model_validate(row)


async def get_model(storage: Storage, actor: Principal, organization_id: str, model_id: str) -> Model:
    async with short_session(storage) as session:
        return Model.model_validate(await find_row(session, actor, ModelRow, Scope(organization_id), model_id, "read"))


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
        rows, next_cursor = await list_rows(
            session, actor, ModelRow, organization_id, workspace_id, limit=limit, cursor=cursor
        )
    return ModelPage(items=[Model.model_validate(row) for row in rows], next_cursor=next_cursor)


async def update_model(
    storage: Storage,
    actor: Principal,
    organization_id: str,
    model_id: str,
    body: ModelUpdate,
    *,
    if_match: str | None,
    registry: Registry,
) -> Model:
    async with transaction(storage) as session:
        row = await find_row(session, actor, ModelRow, Scope(organization_id), model_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        values = given(body, "name", "description", "enabled")
        if body.config is not None and (config := body.config.model_dump(mode="json")) != row.config:
            provider = await _configured_provider(session, actor, organization_id, row.provider_id)
            _check_api(registry.get("model", provider.type), body.config)
            values["config"] = config
        if "pricing" in body.model_fields_set:
            values["pricing"] = None if body.pricing is None else body.pricing.model_dump(mode="json")
        changed = assign(row, values)
        if {"config", "pricing"} & set(changed):
            _check_pricing(ModelConfig.model_validate(row.config), _pricing(row))
        if record_update(session, actor, row, changed):
            await session.flush()
        return Model.model_validate(row)


async def list_catalog(
    storage: Storage, actor: Principal, organization_id: str, provider_id: str, *, registry: Registry
) -> CatalogPage:
    provider = await get_provider(storage, actor, ModelProviderRow, organization_id, provider_id)
    definition = registry.get("model", provider.type)
    return CatalogPage(items=await to_thread.run_sync(known_models, definition), next_cursor=None)


def _source(
    body: ModelCreate, known: CatalogModel | None, definition: ModelProviderDefinition
) -> tuple[ModelConfig, ModelPricingEntry | None]:
    """The configuration and pricing a new model starts with: the caller's, or the catalogue's."""
    if known is not None:
        if not serves(definition, known):
            raise invalid("catalog_key", "the provider type does not serve this model")
        # The definition's first calling API is the Harness default; `PATCH` can select another.
        api = definition.supported_model_apis[0]
        config = ModelConfig(model_name=known.model_name, model_api=api, characteristics=known.characteristics)
        return config, known.pricing
    if body.config is None:
        raise invalid("config", "required unless catalog_key is given")
    _check_api(definition, body.config)
    return body.config, body.pricing


def _check_pricing(config: ModelConfig, pricing: ModelPricingEntry | None) -> None:
    # Execution looks a price up by the upstream model name, so a price naming another model never applies.
    if pricing is not None and pricing.model != config.model_name:
        raise invalid("pricing.model", f"must be the model's model_name {config.model_name}")


def _pricing(row: ModelRow) -> ModelPricingEntry | None:
    return None if row.pricing is None else ModelPricingEntry.model_validate(row.pricing)


def _check_api(definition: ModelProviderDefinition, config: ModelConfig) -> None:
    if config.model_api not in definition.supported_model_apis:
        raise invalid("config.model_api", f"{definition.type} supports {', '.join(definition.supported_model_apis)}")


async def _configured_provider(
    session: AsyncSession, actor: Principal, organization_id: str, provider_id: str
) -> ModelProviderRow:
    """The provider a model is configured under, which the caller may write: the model spends its credential."""
    return await find_row(session, actor, ModelProviderRow, Scope(organization_id), provider_id, "write")
