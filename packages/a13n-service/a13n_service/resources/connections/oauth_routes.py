"""Authenticated private management and a narrowly public bound OAuth callback."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from a13n_service.infra.redis import rate_limit
from a13n_service.resources.connections import managed, managed_completion, managed_revoke, oauth
from a13n_service.resources.connections.managed_values import ManagedCompleted, ManagedCompletion
from a13n_service.resources.connections.oauth_values import AuthorizationStart, AuthorizationView, AuthorizeRequest
from a13n_service.resources.connections.service import get
from a13n_service.tenancy.authenticate import Authenticated
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.routes import current_credential, current_principal

router = APIRouter(tags=["connections"])
BASE = "/api/v1/workspaces/{workspace_id}/connections/{connection_id}"


@router.post(BASE + "/authorize", response_model=AuthorizationStart)
async def authorize_connection(
    request: Request,
    response: Response,
    workspace_id: str,
    connection_id: str,
    body: AuthorizeRequest,
    credential: Annotated[Authenticated, Depends(current_credential)],
) -> AuthorizationStart:
    actor = credential.principal
    await rate_limit(request.app.state.redis, "oauth-init:" + actor.id, limit=20, window_seconds=60)
    selected = await get(request.app.state.storage, actor, workspace_id, connection_id)
    if selected.auth == "managed":
        settings = request.app.state.settings.managed
        result = await managed.start(
            request.app.state.storage,
            credential,
            workspace_id,
            connection_id,
            body.return_url,
            keys=request.app.state.key_ring,
            policy=request.app.state.endpoint_policy,
            settings=settings,
            catalog=request.app.state.tool_catalog,
        )
    else:
        settings = request.app.state.settings.oauth
        result = await oauth.start(
            request.app.state.storage,
            actor,
            workspace_id,
            connection_id,
            body.return_url,
            keys=request.app.state.key_ring,
            policy=request.app.state.endpoint_policy,
            settings=settings,
        )
    # Remove only obsolete bindings for this authorization, never another Connection's flow.
    authorization_id = result.response.authorization.id
    assert authorization_id is not None
    prefix = ("__Host-a13n_managed_" if selected.auth == "managed" else "__Host-a13n_oauth_") + authorization_id + "_"
    for name in request.cookies:
        if name.startswith(prefix) and name != result.cookie_name:
            response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")
    response.set_cookie(
        result.cookie_name,
        result.cookie_value,
        max_age=settings.flow_seconds,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return result.response


@router.get(BASE + "/authorization", response_model=AuthorizationView)
async def authorization_status(
    request: Request,
    response: Response,
    workspace_id: str,
    connection_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> AuthorizationView:
    response.headers["Cache-Control"] = "no-store"
    return await oauth.status(request.app.state.storage, actor, workspace_id, connection_id)


@router.post(BASE + "/revoke", response_model=AuthorizationView)
async def revoke_authorization(
    request: Request,
    response: Response,
    workspace_id: str,
    connection_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> AuthorizationView:
    selected = await get(request.app.state.storage, actor, workspace_id, connection_id)
    if selected.auth == "managed":
        result = await managed_revoke.revoke(
            request.app.state.storage,
            actor,
            workspace_id,
            connection_id,
            keys=request.app.state.key_ring,
            policy=request.app.state.endpoint_policy,
            settings=request.app.state.settings.managed,
            catalog=request.app.state.tool_catalog,
        )
    else:
        result = await oauth.revoke(request.app.state.storage, actor, workspace_id, connection_id)
    prefix = ("__Host-a13n_managed_" if selected.auth == "managed" else "__Host-a13n_oauth_") + (result.id or "") + "_"
    for name in request.cookies:
        if name.startswith(prefix):
            response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/api/v1/oauth/callback", include_in_schema=True)
async def oauth_callback(
    request: Request,
    state: Annotated[str, Query(min_length=32, max_length=256)],
    code: Annotated[str | None, Query(max_length=8192)] = None,
    error: Annotated[str | None, Query(max_length=128)] = None,
    iss: Annotated[str | None, Query(max_length=2048)] = None,
) -> Response:
    await rate_limit(
        request.app.state.redis,
        "oauth-callback:" + (request.client.host if request.client else "unknown"),
        limit=100,
        window_seconds=60,
    )
    target, cookie = await oauth.callback(
        request.app.state.storage,
        state=state,
        cookies=request.cookies,
        code=code,
        error=error,
        issuer=iss,
        keys=request.app.state.key_ring,
        policy=request.app.state.endpoint_policy,
        settings=request.app.state.settings.oauth,
    )
    response = RedirectResponse(
        target, status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    )
    response.delete_cookie(cookie, path="/", secure=True, httponly=True, samesite="lax")
    return response


@router.post(BASE + "/authorization/complete", response_model=ManagedCompleted)
async def complete_managed_authorization(
    request: Request,
    response: Response,
    workspace_id: str,
    connection_id: str,
    body: ManagedCompletion,
    credential: Annotated[Authenticated, Depends(current_credential)],
) -> ManagedCompleted:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return_url = await managed_completion.complete(
        request.app.state.storage,
        credential,
        workspace_id,
        connection_id,
        body,
        request.cookies,
        keys=request.app.state.key_ring,
        policy=request.app.state.endpoint_policy,
        settings=request.app.state.settings.managed,
        catalog=request.app.state.tool_catalog,
    )
    from a13n_service.resources.connections.managed_values import cookie_name

    response.delete_cookie(
        cookie_name(body.authorization_id, body.generation), path="/", secure=True, httponly=True, samesite="lax"
    )
    return ManagedCompleted(return_url=return_url)
