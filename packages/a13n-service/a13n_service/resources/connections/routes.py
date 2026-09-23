"""Workspace-scoped Connection management."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from a13n_service.infra.http import etag
from a13n_service.resources.connections import service
from a13n_service.resources.connections.schemas import (
    ConnectionCreate,
    ConnectionPage,
    ConnectionTest,
    ConnectionUpdate,
    ConnectionView,
)
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.routes import current_principal

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/connections", tags=["connections"])


@router.post("", response_model=ConnectionView, status_code=201)
async def create_connection(
    request: Request,
    response: Response,
    workspace_id: str,
    body: ConnectionCreate,
    actor: Annotated[Principal, Depends(current_principal)],
) -> ConnectionView:
    result = await service.create(
        request.app.state.storage,
        actor,
        workspace_id,
        body,
        catalog=request.app.state.tool_catalog,
        keys=request.app.state.key_ring,
        policy=request.app.state.endpoint_policy,
    )
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.get("", response_model=ConnectionPage)
async def list_connections(
    request: Request,
    workspace_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> ConnectionPage:
    return await service.list_connections(request.app.state.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/{connection_id}", response_model=ConnectionView)
async def get_connection(
    request: Request,
    response: Response,
    workspace_id: str,
    connection_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> ConnectionView:
    result = await service.get(request.app.state.storage, actor, workspace_id, connection_id)
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.patch("/{connection_id}", response_model=ConnectionView)
async def update_connection(
    request: Request,
    response: Response,
    workspace_id: str,
    connection_id: str,
    body: ConnectionUpdate,
    actor: Annotated[Principal, Depends(current_principal)],
) -> ConnectionView:
    result = await service.update(
        request.app.state.storage,
        actor,
        workspace_id,
        connection_id,
        body,
        if_match=request.headers.get("if-match"),
        keys=request.app.state.key_ring,
        policy=request.app.state.endpoint_policy,
        catalog=request.app.state.tool_catalog,
    )
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.post("/{connection_id}/test", response_model=ConnectionTest)
async def test_connection(
    request: Request,
    workspace_id: str,
    connection_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> ConnectionTest:
    from a13n_service.resources.connections.probe import test_connection as probe

    return await probe(
        request.app.state.storage,
        actor,
        workspace_id,
        connection_id,
        redis=request.app.state.redis,
        refresh=True,
        keys=request.app.state.key_ring,
        policy=request.app.state.endpoint_policy,
        catalog=request.app.state.tool_catalog,
        oauth_settings=request.app.state.settings.oauth,
    )


@router.get("/{connection_id}/tools", response_model=ConnectionTest)
async def connection_tools(
    request: Request,
    workspace_id: str,
    connection_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> ConnectionTest:
    from a13n_service.resources.connections.probe import test_connection as probe

    return await probe(
        request.app.state.storage,
        actor,
        workspace_id,
        connection_id,
        redis=request.app.state.redis,
        refresh=False,
        keys=request.app.state.key_ring,
        policy=request.app.state.endpoint_policy,
        catalog=request.app.state.tool_catalog,
        oauth_settings=request.app.state.settings.oauth,
    )
