"""One organization collection for shared and workspace models, and the catalogue a model provider offers."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.models import media, service
from a13n_service.resources.models.schemas import (
    CatalogPage,
    MediaDefaults,
    MediaUnderstandingSelection,
    Model,
    ModelCreate,
    ModelPage,
    ModelUpdate,
)
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor

router = APIRouter(prefix="/api/v1/organizations/{organization_id}", tags=["models"])
# The workspace's media-understanding defaults, which only its models may serve.
workspace_router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["models"])


@router.post("/models", response_model=Model, status_code=201)
async def create_model(
    response: Response, organization_id: str, body: ModelCreate, actor: Actor, runtime: CurrentRuntime
) -> Model:
    """Needs `write` on the model's scope and on its provider, whose credential the model spends."""
    result = await service.create_model(runtime.storage, actor, organization_id, body, registry=runtime.registry)
    return tagged(response, result)


@router.get("/models", response_model=ModelPage)
async def list_models(
    organization_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    workspace_id: str | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> ModelPage:
    return await service.list_models(
        runtime.storage, actor, organization_id, workspace_id=workspace_id, limit=limit, cursor=cursor
    )


@router.get("/models/{model_id}", response_model=Model)
async def get_model(
    response: Response, organization_id: str, model_id: str, actor: Actor, runtime: CurrentRuntime
) -> Model:
    return tagged(response, await service.get_model(runtime.storage, actor, organization_id, model_id))


@router.patch("/models/{model_id}", response_model=Model)
async def update_model(
    response: Response,
    organization_id: str,
    model_id: str,
    body: ModelUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Model:
    """A configuration change also needs `write` on the model's provider."""
    result = await service.update_model(
        runtime.storage, actor, organization_id, model_id, body, if_match=if_match, registry=runtime.registry
    )
    return tagged(response, result)


@router.get("/model-providers/{provider_id}/catalog", response_model=CatalogPage)
async def list_catalog(organization_id: str, provider_id: str, actor: Actor, runtime: CurrentRuntime) -> CatalogPage:
    return await service.list_catalog(runtime.storage, actor, organization_id, provider_id, registry=runtime.registry)


@workspace_router.get("/media-understanding-defaults", response_model=MediaDefaults)
async def get_media_defaults(
    response: Response, workspace_id: str, actor: Actor, runtime: CurrentRuntime
) -> MediaDefaults:
    return tagged(response, await media.get_media_defaults(runtime.storage, actor, workspace_id))


@workspace_router.put("/media-understanding-defaults", response_model=MediaDefaults)
async def replace_media_defaults(
    response: Response,
    workspace_id: str,
    body: MediaUnderstandingSelection,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> MediaDefaults:
    """Replaces all three kinds; each model must declare it understands its kind. Requires workspace admin."""
    replaced = await media.replace_media_defaults(
        runtime.storage, runtime.access, actor, workspace_id, body, if_match=if_match
    )
    return tagged(response, replaced)
