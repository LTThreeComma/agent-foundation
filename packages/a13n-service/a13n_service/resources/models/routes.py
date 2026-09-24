"""One organization collection for shared and workspace models, and the model catalog they start from."""

from fastapi import APIRouter, Request, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.models import media, service
from a13n_service.resources.models.catalog import ModelsDevCatalog
from a13n_service.resources.models.schemas import (
    MediaDefaults,
    MediaUnderstandingSelection,
    Model,
    ModelCatalog,
    ModelCreate,
    ModelPage,
    ModelUpdate,
)
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor

router = APIRouter(prefix="/api/v1", tags=["models"])
_MODELS = "/organizations/{organization_id}/models"
_MODEL = _MODELS + "/{model_id}"
# The workspace's media-understanding defaults, which only its models may serve.
workspace_router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["models"])


@router.post(_MODELS, response_model=Model, status_code=201)
async def create_model(
    response: Response, organization_id: str, body: ModelCreate, actor: Actor, runtime: CurrentRuntime
) -> Model:
    """Needs `write` on the model's scope and on its provider, whose credential the model spends."""
    result = await service.create_model(runtime.storage, actor, organization_id, body, registry=runtime.registry)
    return tagged(response, result)


@router.get(_MODELS, response_model=ModelPage)
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


@router.get(_MODEL, response_model=Model)
async def get_model(
    response: Response, organization_id: str, model_id: str, actor: Actor, runtime: CurrentRuntime
) -> Model:
    return tagged(response, await service.get_model(runtime.storage, actor, organization_id, model_id))


@router.patch(_MODEL, response_model=Model)
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


@router.get("/model-catalog", response_model=ModelCatalog)
async def get_model_catalog(request: Request, actor: Actor) -> ModelCatalog:
    """The models.dev models the registered model provider types serve, for any signed-in principal."""
    catalog: ModelsDevCatalog = request.app.state.model_catalog
    return await catalog.read()


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
