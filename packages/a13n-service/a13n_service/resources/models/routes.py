"""Workspace models, media defaults and the upstream model catalog."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.models import media, service
from a13n_service.resources.models.catalog import ModelsDevCatalog
from a13n_service.resources.models.inputs import MediaSelectionInput
from a13n_service.resources.models.schemas import (
    MediaDefaults,
    Model,
    ModelCatalog,
    ModelCreate,
    ModelPage,
    ModelUpdate,
)
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.requests import CurrentRuntime, resource_id
from a13n_service.tenancy.requests import Actor, Workspace

ModelId = Annotated[str, Depends(resource_id(ModelRow, "model_reference"))]

router = APIRouter(prefix="/api/v1", tags=["models"])
_MODELS = "/models"
_MODEL = _MODELS + "/{model_reference}"


@router.post(_MODELS, response_model=Model, status_code=201)
async def create_model(
    response: Response, workspace: Workspace, body: ModelCreate, actor: Actor, runtime: CurrentRuntime
) -> Model:
    """Needs `write` on the model's scope and on its provider, whose credential the model spends."""
    result = await service.create_model(runtime.storage, actor, workspace.workspace_id, body, registry=runtime.registry)
    return tagged(response, result)


@router.get(_MODELS, response_model=ModelPage)
async def list_models(
    workspace: Workspace,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> ModelPage:
    return await service.list_models(runtime.storage, actor, workspace.workspace_id, limit=limit, cursor=cursor)


@router.get(_MODEL, response_model=Model)
async def get_model(
    response: Response, workspace: Workspace, model_id: ModelId, actor: Actor, runtime: CurrentRuntime
) -> Model:
    return tagged(response, await service.get_model(runtime.storage, actor, workspace.workspace_id, model_id))


@router.patch(_MODEL, response_model=Model)
async def update_model(
    response: Response,
    workspace: Workspace,
    model_id: ModelId,
    body: ModelUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Model:
    """A configuration change also needs `write` on the model's provider."""
    result = await service.update_model(
        runtime.storage, actor, workspace.workspace_id, model_id, body, if_match=if_match, registry=runtime.registry
    )
    return tagged(response, result)


@router.get("/model-catalog", response_model=ModelCatalog)
async def get_model_catalog(request: Request, actor: Actor) -> ModelCatalog:
    """The models.dev models the registered model provider types serve, for any signed-in principal."""
    catalog: ModelsDevCatalog = request.app.state.model_catalog
    return await catalog.read()


@router.get("/media-understanding-defaults", response_model=MediaDefaults)
async def get_media_defaults(
    response: Response, workspace: Workspace, actor: Actor, runtime: CurrentRuntime
) -> MediaDefaults:
    return tagged(response, await media.get_media_defaults(runtime.storage, actor, workspace.workspace_id))


@router.put("/media-understanding-defaults", response_model=MediaDefaults)
async def replace_media_defaults(
    response: Response,
    workspace: Workspace,
    body: MediaSelectionInput,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> MediaDefaults:
    """Replaces all three kinds; each model must declare it understands its kind. Requires workspace admin."""
    replaced = await media.replace_media_defaults(
        runtime.storage, runtime.access, actor, workspace.workspace_id, body, if_match=if_match
    )
    return tagged(response, replaced)
