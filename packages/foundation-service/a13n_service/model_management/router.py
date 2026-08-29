"""Model Management public `/api/v1` routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.iam import AuthenticatedActor, authenticate_request

from .domain import (
    ModelConfigCollection,
    ModelConfigCopy,
    ModelConfigCreate,
    ModelConfigPatch,
    ModelConfigResource,
    ModelConnectionTestResult,
    ModelReferenceCollection,
)
from .providers import ProviderDefinitionCollection
from .service import ModelConfigService, ModelManagementError

router = APIRouter(prefix="/api/v1", tags=["model-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _service(request: Request) -> ModelConfigService:
    service: ModelConfigService | None = getattr(request.app.state, "model_config_service", None)
    if service is None:
        raise ModelManagementError("model_management_unavailable", "Model Management is unavailable.", status_code=503)
    return service


@router.get("/model-providers", response_model=ProviderDefinitionCollection)
async def list_model_providers(request: Request, actor: Actor) -> ProviderDefinitionCollection:
    return await _service(request).provider_definitions(actor=actor)


@router.get("/workspaces/{workspace_id}/models", response_model=ModelConfigCollection)
async def list_models(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    name: Annotated[str | None, Query(max_length=128)] = None,
    provider_type: Annotated[str | None, Query(max_length=64)] = None,
    enabled: bool | None = None,
) -> ModelConfigCollection:
    return await _service(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        name=name,
        provider_type=provider_type,
        enabled=enabled,
    )


@router.post(
    "/workspaces/{workspace_id}/models",
    response_model=ModelConfigResource,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    body: ModelConfigCreate,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=256)] = None,
) -> ModelConfigResource:
    model, replayed = await _service(request).create(
        actor=actor,
        workspace_id=workspace_id,
        request=body,
        idempotency_key=_require_idempotency_key(idempotency_key),
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    response.headers["ETag"] = model.strong_etag()
    return model


@router.get("/workspaces/{workspace_id}/models/{model_id}", response_model=ModelConfigResource)
async def get_model(
    request: Request, response: Response, actor: Actor, workspace_id: str, model_id: str
) -> ModelConfigResource:
    model = await _service(request).get(actor=actor, workspace_id=workspace_id, model_id=model_id)
    response.headers["ETag"] = model.strong_etag()
    return model


@router.patch("/workspaces/{workspace_id}/models/{model_id}", response_model=ModelConfigResource)
async def patch_model(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    model_id: str,
    body: ModelConfigPatch,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=80)] = None,
) -> ModelConfigResource:
    model = await _service(request).patch(
        actor=actor,
        workspace_id=workspace_id,
        model_id=model_id,
        request=body,
        if_match=_require_if_match(if_match),
    )
    response.headers["ETag"] = model.strong_etag()
    return model


@router.delete("/workspaces/{workspace_id}/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    request: Request,
    actor: Actor,
    workspace_id: str,
    model_id: str,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=80)] = None,
) -> Response:
    await _service(request).delete(
        actor=actor,
        workspace_id=workspace_id,
        model_id=model_id,
        if_match=_require_if_match(if_match),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workspaces/{workspace_id}/models/test", response_model=ModelConnectionTestResult)
async def test_model_candidate(
    request: Request, actor: Actor, workspace_id: str, body: ModelConfigCreate
) -> ModelConnectionTestResult:
    return await _service(request).test_candidate(actor=actor, workspace_id=workspace_id, request=body)


@router.post(
    "/workspaces/{workspace_id}/models/{model_id}/copy",
    response_model=ModelConfigResource,
    status_code=status.HTTP_201_CREATED,
)
async def copy_model(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    model_id: str,
    body: ModelConfigCopy,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=256)] = None,
) -> ModelConfigResource:
    model, replayed = await _service(request).copy(
        actor=actor,
        workspace_id=workspace_id,
        model_id=model_id,
        request=body,
        idempotency_key=_require_idempotency_key(idempotency_key),
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    response.headers["ETag"] = model.strong_etag()
    return model


@router.get(
    "/workspaces/{workspace_id}/models/{model_id}/references",
    response_model=ModelReferenceCollection,
)
async def list_model_references(
    request: Request,
    actor: Actor,
    workspace_id: str,
    model_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> ModelReferenceCollection:
    return await _service(request).references(
        actor=actor,
        workspace_id=workspace_id,
        model_id=model_id,
        limit=limit,
        cursor=cursor,
    )


def _require_idempotency_key(value: str | None) -> str:
    if value is None:
        raise ModelManagementError("idempotency_key_required", "Idempotency-Key is required.", status_code=400)
    return value


def _require_if_match(value: str | None) -> str:
    if value is None:
        raise ModelManagementError("precondition_required", "If-Match is required for this mutation.", status_code=428)
    return value
