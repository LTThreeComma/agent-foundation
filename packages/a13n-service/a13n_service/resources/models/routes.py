"""One organization collection for shared and workspace-confined models."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from a13n_service.infra.http import etag
from a13n_service.resources.models import service
from a13n_service.resources.models.schemas import ModelCreate, ModelView, ProviderCreate, ProviderView
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
