"""Bounded multipart staging and immutable Asset resources."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Header, Query, Request, Response, UploadFile

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import etag
from a13n_service.infra.redis import rate_limit
from a13n_service.resources.assets import service, uploads
from a13n_service.resources.assets.schemas import AssetCreate, AssetPage, AssetView, UploadView
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.routes import current_principal

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["assets"])


@router.post("/uploads", response_model=UploadView)
async def upload(
    request: Request,
    workspace_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> UploadView:
    state = request.app.state
    scope = await uploads.scope_for(state.storage, actor, workspace_id, "write")
    key = uploads.request_key(idempotency_key)
    config = state.settings
    await rate_limit(
        state.redis,
        f"upload:{scope.workspace_id}:{actor.id}",
        limit=config.objects.upload_limit,
        window_seconds=config.objects.upload_window_seconds,
    )
    bound = min(config.objects.upload_bytes, config.objects.max_bytes)
    content = await file.read(bound + 1)
    if len(content) > bound:
        raise ServiceError("payload_too_large", "Upload exceeds its byte limit")
    if not file.filename or not file.content_type:
        raise ServiceError("invalid_argument", "Upload requires a filename and content type")
    return await uploads.stage(
        state.storage,
        state.objects,
        actor,
        workspace_id,
        key=key,
        filename=file.filename,
        content_type=file.content_type,
        content=content,
        max_bytes=bound,
        timeout=config.objects.timeout,
    )


@router.post(
    "/assets",
    response_model=AssetView,
    status_code=201,
    responses={200: {"model": AssetView, "description": "Existing Asset with the same upload and name"}},
)
async def create_asset(
    request: Request,
    response: Response,
    workspace_id: str,
    body: AssetCreate,
    actor: Annotated[Principal, Depends(current_principal)],
) -> AssetView:
    state = request.app.state
    result, created = await service.create(
        state.storage, state.objects, actor, workspace_id, body, timeout=state.settings.objects.timeout
    )
    response.status_code = 201 if created else 200
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.get("/assets", response_model=AssetPage)
async def list_assets(
    request: Request,
    workspace_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> AssetPage:
    return await service.list_assets(request.app.state.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/assets/{asset_id}", response_model=AssetView)
async def get_asset(
    request: Request,
    response: Response,
    workspace_id: str,
    asset_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> AssetView:
    result = await service.get(request.app.state.storage, actor, workspace_id, asset_id)
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.delete("/assets/{asset_id}", response_model=AssetView)
async def retire_asset(
    request: Request,
    response: Response,
    workspace_id: str,
    asset_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> AssetView:
    result = await service.retire(
        request.app.state.storage, actor, workspace_id, asset_id, if_match=request.headers.get("if-match")
    )
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.get("/assets/{asset_id}/content", response_class=Response)
async def asset_content(
    request: Request,
    workspace_id: str,
    asset_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> Response:
    state = request.app.state
    result, data = await service.content(
        state.storage, state.objects, actor, workspace_id, asset_id, timeout=state.settings.objects.timeout
    )
    return Response(
        data,
        media_type=result.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(result.name, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
