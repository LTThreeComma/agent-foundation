"""Public identity routes; dependencies return values and never yield SQL sessions."""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr
from sqlalchemy import select

from a13n_service.infra import cursors
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import ObjectId
from a13n_service.infra.redis import rate_limit
from a13n_service.tenancy.audit import AuditPage, list_events
from a13n_service.tenancy.authenticate import (
    COOKIE_NAME,
    Authenticated,
    authenticate,
    issue_user_key,
    login,
    logout,
    session_csrf,
)
from a13n_service.tenancy.authorize import Principal, Scope, Verb, allowed_verbs, authorize
from a13n_service.tenancy.grants import readable_workspaces, resolve_workspace
from a13n_service.tenancy.tables import WorkspaceRow

router = APIRouter(prefix="/api/v1", tags=["auth"])


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=1024)


class Profile(BaseModel):
    id: str
    kind: str
    name: str
    email: str | None


class LoginOutput(BaseModel):
    principal_id: str
    csrf_token: str


class SessionProfile(BaseModel):
    user: Profile
    csrf_token: str


class KeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: ObjectId
    name: str = Field(min_length=1, max_length=128)


class KeyOutput(BaseModel):
    id: str
    workspace_id: str
    name: str
    secret: str


class Workspace(BaseModel):
    id: str
    organization_id: str
    key: str
    name: str
    version: int
    permissions: list[Verb]


class WorkspacePage(BaseModel):
    items: list[Workspace]
    next_cursor: str | None


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or origin != f"{request.url.scheme}://{request.url.netloc}":
        raise ServiceError("forbidden", "Request origin is not allowed")


async def current_credential(request: Request, response: Response) -> Authenticated:
    authorization = request.headers.get("authorization")
    if authorization:
        scheme, _, secret = authorization.partition(" ")
        if scheme.lower() != "bearer":
            raise ServiceError("unauthenticated", "Authentication is required")
        kind = "key"
    else:
        secret = request.cookies.get(COOKIE_NAME, "")
        kind = "session"
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            check_origin(request)
    credential = await authenticate(
        request.app.state.storage,
        secret=secret,
        kind=kind,
        csrf_token=request.headers.get("x-csrf-token"),
        mutation=request.method not in {"GET", "HEAD", "OPTIONS"},
        session_seconds=request.app.state.settings.auth.session_seconds,
    )

    response.headers["Cache-Control"] = "no-store"
    if kind == "session":
        response.set_cookie(
            COOKIE_NAME,
            secret,
            max_age=request.app.state.settings.auth.session_seconds,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
        )
    return credential


async def current_principal(credential: Annotated[Authenticated, Depends(current_credential)]) -> Principal:
    return credential.principal


@router.post("/auth/login", response_model=LoginOutput)
async def password_login(request: Request, body: LoginInput, response: Response) -> LoginOutput:
    check_origin(request)
    config = request.app.state.settings.auth
    peer = request.client.host if request.client else "unknown"
    for identity in ("login:peer:" + peer, "login:email:" + str(body.email)):
        await rate_limit(
            request.app.state.redis, identity, limit=config.login_limit, window_seconds=config.login_window_seconds
        )
    result = await login(
        request.app.state.storage,
        email=str(body.email),
        password=body.password.get_secret_value(),
        session_seconds=config.session_seconds,
    )
    response.set_cookie(
        COOKIE_NAME,
        result.secret,
        max_age=config.session_seconds,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return LoginOutput(principal_id=result.principal.id, csrf_token=result.csrf_token)


@router.post("/auth/logout")
async def session_logout(
    request: Request, response: Response, credential: Annotated[Authenticated, Depends(current_credential)]
) -> dict[str, bool]:
    await logout(request.app.state.storage, credential)
    response.delete_cookie(COOKIE_NAME, secure=True, httponly=True, samesite="strict", path="/")
    return {"logged_out": True}


@router.get("/auth/session", response_model=SessionProfile)
async def session_profile(
    request: Request, credential: Annotated[Authenticated, Depends(current_credential)]
) -> SessionProfile:
    if credential.kind != "session":
        raise ServiceError("invalid_argument", "Session bootstrap requires a login session")
    actor = credential.principal
    return SessionProfile(
        user=Profile(id=actor.id, kind=actor.kind, name=actor.name, email=actor.email),
        csrf_token=session_csrf(request.cookies[COOKIE_NAME]),
    )


@router.get("/users/me", response_model=Profile, tags=["tenancy"])
async def me(request: Request, actor: Annotated[Principal, Depends(current_principal)]) -> Profile:
    return Profile(id=actor.id, kind=actor.kind, name=actor.name, email=actor.email)


@router.post("/users/me/keys", response_model=KeyOutput, status_code=201, tags=["tenancy"])
async def create_key(
    request: Request, body: KeyInput, actor: Annotated[Principal, Depends(current_principal)]
) -> KeyOutput:
    key_id, secret = await issue_user_key(
        request.app.state.storage, actor, workspace_id=body.workspace_id, name=body.name
    )
    return KeyOutput(id=key_id, workspace_id=body.workspace_id, name=body.name, secret=secret)


@router.get("/workspaces/{workspace_id}", response_model=Workspace, tags=["tenancy"])
async def get_workspace(
    request: Request, workspace_id: str, actor: Annotated[Principal, Depends(current_principal)]
) -> Workspace:
    async with short_session(request.app.state.storage) as session:
        row = await resolve_workspace(session, workspace_id)
        authorize(actor, Scope(row.organization_id, row.id), "read")
        return workspace_view(row, actor)


@router.get("/workspaces/{workspace_id}/audit-events", response_model=AuditPage, tags=["tenancy"])
async def audit_events(
    request: Request,
    workspace_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> AuditPage:
    return await list_events(request.app.state.storage, actor, workspace_id=workspace_id, limit=limit, cursor=cursor)


def workspace_view(row: WorkspaceRow, actor: Principal) -> Workspace:
    permissions = allowed_verbs(actor, Scope(row.organization_id, row.id))
    if row.archived_at is not None:
        permissions &= {"read"}
    return Workspace(
        id=row.id,
        organization_id=row.organization_id,
        key=row.key,
        name=row.name,
        version=row.version,
        permissions=sorted(permissions),
    )


@router.get("/workspaces", response_model=WorkspacePage, tags=["tenancy"])
async def list_workspaces(
    request: Request,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> WorkspacePage:
    after = cursors.id_position(cursor, "workspaces", actor.id)
    async with short_session(request.app.state.storage) as session:
        rows = (
            await session.scalars(
                select(WorkspaceRow)
                .where(WorkspaceRow.id.in_(readable_workspaces(actor)), WorkspaceRow.id > after)
                .order_by(WorkspaceRow.id)
                .limit(limit + 1)
            )
        ).all()
        return WorkspacePage(
            items=[workspace_view(row, actor) for row in rows[:limit]],
            next_cursor=cursors.encode("workspaces", actor.id, rows[limit - 1].id) if len(rows) > limit else None,
        )
