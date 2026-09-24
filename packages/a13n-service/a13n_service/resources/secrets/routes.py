"""Secret metadata reads and write-only value changes."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.resources.secrets import service
from a13n_service.resources.secrets.schemas import Secret, SecretCreate, SecretPage, SecretUpdate
from a13n_service.tenancy.requests import Actor

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/secrets", tags=["secrets"])


@router.post("", response_model=Secret, status_code=201)
async def create_secret(
    response: Response, workspace_id: str, body: SecretCreate, actor: Actor, runtime: CurrentRuntime
) -> Secret:
    return tagged(response, await service.create_secret(runtime.storage, runtime.keys, actor, workspace_id, body))


@router.get("", response_model=SecretPage)
async def list_secrets(
    workspace_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SecretPage:
    return await service.list_secrets(runtime.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/{secret_id}", response_model=Secret)
async def get_secret(
    response: Response, workspace_id: str, secret_id: str, actor: Actor, runtime: CurrentRuntime
) -> Secret:
    return tagged(response, await service.get_secret(runtime.storage, actor, workspace_id, secret_id))


@router.put("/{secret_id}", response_model=Secret)
async def replace_secret(
    response: Response,
    workspace_id: str,
    secret_id: str,
    body: SecretUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Secret:
    result = await service.replace_secret(
        runtime.storage, runtime.keys, actor, workspace_id, secret_id, body, if_match=if_match
    )
    return tagged(response, result)


@router.delete("/{secret_id}", status_code=204)
async def delete_secret(
    workspace_id: str, secret_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> Response:
    await service.delete_secret(runtime.storage, actor, workspace_id, secret_id, if_match=if_match)
    return Response(status_code=204)
