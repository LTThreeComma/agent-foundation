"""Public run submission and durable SQL observations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import etag
from a13n_service.runs import collections, delivery, input_views, usage, views, wakeups
from a13n_service.runs.acceptance import submit
from a13n_service.runs.interrupt import interrupt
from a13n_service.runs.schemas import InboxPage, NewThread, RunView, Submission, Submitted
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.grants import workspace_scope
from a13n_service.tenancy.routes import current_credential, current_principal

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["runs"])


async def submit_request(
    request: Request, response: Response, actor: Principal, workspace_id: str, body: Submission, thread_id: str | None
) -> Submitted:
    config = request.app.state.settings
    result = await submit(
        request.app.state.storage,
        actor,
        workspace_id,
        body,
        thread_id=thread_id,
        request_key=request.headers.get("idempotency-key", ""),
        max_entries=config.control.inbox_count,
        max_bytes=config.control.inbox_bytes,
        max_attempts=config.worker.max_attempts,
        keys=request.app.state.key_ring,
        policy=request.app.state.admission,
    )
    if not result.replayed:
        await wakeups.notify(request.app.state.redis, timeout=config.redis.timeout)
    response.status_code = 200 if result.replayed else 201
    return result


@router.post("/threads", response_model=Submitted, status_code=201)
async def create_thread(
    request: Request,
    response: Response,
    workspace_id: str,
    body: NewThread,
    actor: Annotated[Principal, Depends(current_principal)],
) -> Submitted:
    return await submit_request(request, response, actor, workspace_id, body, None)


@router.post("/threads/{thread_id}/inbox", response_model=Submitted, status_code=201)
async def append_input(
    request: Request,
    response: Response,
    workspace_id: str,
    thread_id: str,
    body: Submission,
    actor: Annotated[Principal, Depends(current_principal)],
) -> Submitted:
    return await submit_request(request, response, actor, workspace_id, body, thread_id)


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(
    request: Request,
    response: Response,
    workspace_id: str,
    run_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> RunView:
    async with short_session(request.app.state.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        run = await session.get(RunRow, run_id)
        if run is None or run.workspace_id != workspace_id:
            raise ServiceError("not_found", "Run was not found")
        response.headers["ETag"] = etag(run.id, run.version)
        return RunView.model_validate(run)


@router.get("/threads/{thread_id}/inbox", response_model=InboxPage)
async def get_inbox(
    request: Request,
    response: Response,
    workspace_id: str,
    thread_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> InboxPage:
    async with short_session(request.app.state.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        thread = await session.get(ThreadRow, thread_id)
        if thread is None or thread.workspace_id != workspace_id:
            raise ServiceError("not_found", "Thread was not found")
        items, next_cursor = await input_views.page(
            session,
            thread_id=thread.id,
            run_id=None,
            limit=limit,
            cursor=cursor,
        )
        response.headers["ETag"] = etag(thread.id, thread.version)
        return InboxPage(items=items, next_cursor=next_cursor)


@router.get("/runs/{run_id}/items", response_model=views.RunItems)
async def get_items(
    request: Request,
    workspace_id: str,
    run_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    input_cursor: str | None = None,
) -> views.RunItems:
    return await views.items(
        request.app.state.storage,
        request.app.state.objects,
        actor,
        workspace_id,
        run_id,
        limit=limit,
        cursor=input_cursor,
        timeout=request.app.state.settings.objects.timeout,
    )


@router.get("/runs/{run_id}/events", response_class=StreamingResponse)
async def get_events(
    request: Request,
    workspace_id: str,
    run_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    cursor: str | None = None,
) -> StreamingResponse:
    config = request.app.state.settings
    cursor = cursor or request.headers.get("last-event-id")
    if cursor is not None:
        delivery.position(cursor, run_id)
    initial = await views.items(
        request.app.state.storage,
        request.app.state.objects,
        actor,
        workspace_id,
        run_id,
        limit=1,
        cursor=None,
        timeout=config.objects.timeout,
    )

    async def reauthenticate() -> Principal:
        return (await current_credential(request, Response())).principal

    return StreamingResponse(
        delivery.stream(
            request.app.state.storage,
            request.app.state.objects,
            request.app.state.redis,
            initial,
            workspace_id=initial.workspace_id,
            cursor=cursor,
            reauthenticate=reauthenticate,
            object_timeout=config.objects.timeout,
            redis_timeout=config.redis.timeout,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/interrupt", response_model=RunView)
async def interrupt_run(
    request: Request,
    response: Response,
    workspace_id: str,
    run_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> RunView:
    result = await interrupt(request.app.state.storage, actor, workspace_id, run_id)
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.get("/usage", response_model=usage.UsageView)
async def get_usage(
    request: Request,
    workspace_id: str,
    run_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> usage.UsageView:
    return await usage.view(request.app.state.storage, actor, workspace_id, run_id)


@router.get("/sessions", response_model=collections.SessionPage)
async def list_sessions(
    request: Request,
    workspace_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> collections.SessionPage:
    return await collections.list_sessions(request.app.state.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/sessions/{identity}", response_model=collections.SessionView)
async def get_session(
    request: Request,
    response: Response,
    workspace_id: str,
    identity: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> collections.SessionView:
    result = await collections.get_session(request.app.state.storage, actor, workspace_id, identity)
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.get("/threads", response_model=collections.ThreadPage)
async def list_threads(
    request: Request,
    workspace_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    session_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> collections.ThreadPage:
    return await collections.list_threads(
        request.app.state.storage, actor, workspace_id, session_id=session_id, limit=limit, cursor=cursor
    )


@router.get("/threads/{identity}", response_model=collections.ThreadView)
async def get_thread(
    request: Request,
    response: Response,
    workspace_id: str,
    identity: str,
    actor: Annotated[Principal, Depends(current_principal)],
) -> collections.ThreadView:
    result = await collections.get_thread(request.app.state.storage, actor, workspace_id, identity)
    response.headers["ETag"] = etag(result.id, result.version)
    return result


@router.get("/threads/{thread_id}/runs", response_model=collections.RunPage)
async def list_runs(
    request: Request,
    workspace_id: str,
    thread_id: str,
    actor: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> collections.RunPage:
    return await collections.list_runs(
        request.app.state.storage, actor, workspace_id, thread_id=thread_id, limit=limit, cursor=cursor
    )
