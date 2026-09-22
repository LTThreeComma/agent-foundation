"""One organization collection for shared and workspace-confined models."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from a13n_service.infra.http import etag
from a13n_service.resources.models import service
from a13n_service.resources.models.schemas import (
    ModelCreate,
    ModelPage,
    ModelView,
    ProviderCreate,
    ProviderPage,
    ProviderType,
    ProviderTypePage,
    ProviderView,
)
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.routes import current_principal

router = APIRouter(prefix="/api/v1/organizations/{organization_id}", tags=["models"])


@router.post("/model-providers", response_model=ProviderView, status_code=201)
async def create_provider(
    request: Request,
    response: Response,
    organization_id: str,
    body: ProviderCreate,
    actor: Annotated[Principal, Depends(current_principal)],
) -> ProviderView:
    result = await service.create_provider(
        request.app.state.storage,
        actor,
        organization_id,
        body,
        catalog=request.app.state.model_catalog,
        policy=request.app.state.endpoint_policy,
        keys=request.app.state.key_ring,
    )
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.get("/model-providers/{provider_id}", response_model=ProviderView)
async def get_provider(
    request: Request,
    response: Response,
    organization_id: str,
    provider_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> ProviderView:
    result = await service.get_provider(request.app.state.storage, actor, organization_id, provider_id)
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.post("/models", response_model=ModelView, status_code=201)
async def create_model(
    request: Request,
    response: Response,
    organization_id: str,
    body: ModelCreate,
    actor: Annotated[Principal, Depends(current_principal)],
) -> ModelView:
    result = await service.create_model(
        request.app.state.storage,
        actor,
        organization_id,
        body,
        catalog=request.app.state.model_catalog,
    )
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.get("/models/{model_id}", response_model=ModelView)
async def get_model(
    request: Request,
    response: Response,
    organization_id: str,
    model_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> ModelView:
    result = await service.get_model(request.app.state.storage, actor, organization_id, model_id)
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.get("/models", response_model=ModelPage)
async def list_models(
    request: Request,
    organization_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    workspace_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> ModelPage:
    return await service.list_models(
        request.app.state.storage, actor, organization_id, workspace_id=workspace_id, limit=limit, cursor=cursor
    )


@router.get("/model-providers", response_model=ProviderPage)
async def list_providers(
    request: Request,
    organization_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    workspace_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> ProviderPage:
    return await service.list_providers(
        request.app.state.storage, actor, organization_id, workspace_id=workspace_id, limit=limit, cursor=cursor
    )


catalog_router = APIRouter(prefix="/api/v1/provider-types", tags=["models"])


@catalog_router.get("/model", response_model=ProviderTypePage)
async def provider_types(
    request: Request,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> ProviderTypePage:
    from a13n_service.infra import cursors

    after = cursors.id_position(cursor, "model_provider_types", "deployment")
    definitions = sorted(
        (item for item in request.app.state.model_catalog.values() if item.type > after), key=lambda item: item.type
    )[: limit + 1]
    return ProviderTypePage(
        items=[
            ProviderType(
                type=item.type,
                display_name=item.display_name,
                configuration_schema=item.configuration_model.model_json_schema(),
                credential_schema=item.credential_model.model_json_schema() if item.credential_model else None,
                authentication=item.authentication,
                supported_model_apis=list(item.supported_model_apis),
                setup_url=item.setup_url,
            )
            for item in definitions[:limit]
        ],
        next_cursor=cursors.encode("model_provider_types", "deployment", definitions[limit - 1].type)
        if len(definitions) > limit
        else None,
    )
