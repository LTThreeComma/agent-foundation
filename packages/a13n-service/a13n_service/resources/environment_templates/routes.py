"""Workspace environment templates: CRUD with ETags; `enabled: false` retires one instead of deletion."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.environment_templates import service
from a13n_service.resources.environment_templates.schemas import Template, TemplateCreate, TemplatePage, TemplateUpdate
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.requests import CurrentRuntime, resource_id
from a13n_service.tenancy.requests import Actor, Workspace

TemplateId = Annotated[str, Depends(resource_id(EnvironmentTemplateRow, "template_reference"))]

router = APIRouter(prefix="/api/v1/environment-templates", tags=["environments"])


@router.post("", response_model=Template, status_code=201)
async def create_template(
    response: Response, workspace: Workspace, body: TemplateCreate, actor: Actor, runtime: CurrentRuntime
) -> Template:
    result = await service.create_template(
        runtime.storage, actor, workspace.workspace_id, body, registry=runtime.registry
    )
    return tagged(response, result)


@router.get("", response_model=TemplatePage)
async def list_templates(
    workspace: Workspace,
    actor: Actor,
    runtime: CurrentRuntime,
    label: Annotated[list[str] | None, Query()] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> TemplatePage:
    return await service.list_templates(
        runtime.storage, actor, workspace.workspace_id, labels=label or [], limit=limit, cursor=cursor
    )


@router.get("/{template_reference}", response_model=Template)
async def get_template(
    response: Response, workspace: Workspace, template_id: TemplateId, actor: Actor, runtime: CurrentRuntime
) -> Template:
    return tagged(response, await service.get_template(runtime.storage, actor, workspace.workspace_id, template_id))


@router.patch("/{template_reference}", response_model=Template)
async def update_template(
    response: Response,
    workspace: Workspace,
    template_id: TemplateId,
    body: TemplateUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Template:
    result = await service.update_template(
        runtime.storage, actor, workspace.workspace_id, template_id, body, if_match=if_match, registry=runtime.registry
    )
    return tagged(response, result)
