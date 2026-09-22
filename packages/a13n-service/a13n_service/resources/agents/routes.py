"""Agent selection and revision creation over the current HTTP contract."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from a13n_service.infra.http import etag
from a13n_service.resources.agents import service
from a13n_service.resources.agents.schemas import (
    AgentCreate,
    AgentPage,
    AgentView,
    RevisionCreate,
    RevisionPage,
    RevisionView,
)
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.routes import current_principal

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/agents", tags=["agents"])


@router.post("", response_model=AgentView, status_code=201)
async def create_agent(
    request: Request,
    response: Response,
    workspace_id: str,
    body: AgentCreate,
    actor: Annotated[Principal, Depends(current_principal)],
) -> AgentView:
    result = await service.create_agent(request.app.state.storage, actor, workspace_id, body)
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.get("/{agent_id}", response_model=AgentView)
async def get_agent(
    request: Request,
    response: Response,
    workspace_id: str,
    agent_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> AgentView:
    result = await service.get_agent(request.app.state.storage, actor, workspace_id, agent_id)
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.post("/{agent_id}/revisions", response_model=RevisionView, status_code=201)
async def create_revision(
    request: Request,
    workspace_id: str,
    agent_id: str,
    body: RevisionCreate,
    actor: Annotated[Principal, Depends(current_principal)],
) -> RevisionView:
    return await service.create_revision(
        request.app.state.storage,
        actor,
        workspace_id,
        agent_id,
        body,
        if_match=request.headers.get("if-match"),
    )


@router.get("/{agent_id}/revisions/{revision_id}", response_model=RevisionView)
async def get_revision(
    request: Request,
    workspace_id: str,
    agent_id: str,
    revision_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> RevisionView:
    return await service.get_revision(request.app.state.storage, actor, workspace_id, agent_id, revision_id)


@router.get("", response_model=AgentPage)
async def list_agents(
    request: Request,
    workspace_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> AgentPage:
    return await service.list_agents(request.app.state.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/{agent_id}/revisions", response_model=RevisionPage)
async def list_revisions(
    request: Request,
    workspace_id: str,
    agent_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> RevisionPage:
    return await service.list_revisions(
        request.app.state.storage, actor, workspace_id, agent_id, limit=limit, cursor=cursor
    )


@router.post("/{agent_id}/revisions/{revision_id}/set-default", response_model=AgentView)
async def set_default(
    request: Request,
    response: Response,
    workspace_id: str,
    agent_id: str,
    revision_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> AgentView:
    result = await service.set_default(
        request.app.state.storage, actor, workspace_id, agent_id, revision_id, if_match=request.headers.get("if-match")
    )
    response.headers["ETag"] = etag(result.id, result.version)
    return result
