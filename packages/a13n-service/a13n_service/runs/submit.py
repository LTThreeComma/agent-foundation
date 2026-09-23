"""Idempotent message commands: create a thread, submit to a thread, fork a run.

Each is one short transaction that resolves the request key, appends the entry and tries to accept it.
The unique request index arbitrates concurrent duplicates: the loser rolls back everything it created,
including a tentative thread or session, then replays the winner.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import transaction, violated_constraint
from a13n_service.infra.errors import conflict, not_found
from a13n_service.resources.agents.service import select_revision
from a13n_service.resources.assets.service import require_usable
from a13n_service.resources.connections.service import validate_caller_headers
from a13n_service.runs import checkpoints
from a13n_service.runs.accept import accept
from a13n_service.runs.environments.mounts import copy_desired
from a13n_service.runs.inbox import InboxLimits, Request, append_message, check_replay, find_request
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import AssetPart, EntryView, Fork, Message, NewThread, RunView, Submitted, ThreadView
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from a13n_service.runs.threads import get_run, get_thread, new_session, new_thread, refresh_version, require_open
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, execution_authority
from a13n_service.tenancy.grants import workspace_scope

type Create = Callable[[AsyncSession, WorkspaceScope], Awaitable[tuple[ThreadRow, InboxEntryRow]]]


async def validate_message(session: AsyncSession, workspace_id: str, message: Message) -> None:
    """Malformed or unauthorized input fails before anything is appended."""
    await select_revision(session, workspace_id, message.agent_id, message.agent_revision_id)
    await require_usable(
        session, workspace_id, {part.asset_id for part in message.payload.content if isinstance(part, AssetPart)}
    )


async def receipt(session: AsyncSession, thread: ThreadRow, entry: InboxEntryRow) -> Submitted:
    """The entry's current disposition and the run that owns it, if any."""
    await refresh_version(session, thread)
    run = await session.get(RunRow, entry.assigned_run_id) if entry.assigned_run_id is not None else None
    return Submitted(
        thread=ThreadView.model_validate(thread),
        entry=EntryView.model_validate(entry),
        run=RunView.model_validate(run) if run is not None else None,
    )


async def _replay(session: AsyncSession, entry: InboxEntryRow, request: Request) -> Submitted:
    check_replay(entry, request)
    thread = await session.get(ThreadRow, entry.thread_id)
    assert thread is not None
    return await receipt(session, thread, entry)


async def _submit(
    runtime: Runtime, actor: Principal, workspace_id: str, request: Request, create: Create
) -> tuple[Submitted, bool]:
    """Returns the receipt and whether this call created it (201) rather than replayed it (200)."""
    try:
        async with transaction(runtime.storage) as session:
            scope, found = await _lookup(session, actor, workspace_id, request)
            if found is not None:
                return await _replay(session, found, request), False
            thread, entry = await create(session, scope)
            await accept(session, runtime, thread, explicit=entry)
            return await receipt(session, thread, entry), True
    except IntegrityError as error:
        if violated_constraint(error) != "uq_inbox_entries_request":
            raise
    # A concurrent request with the same key committed first; its entry is the evidence.
    async with transaction(runtime.storage) as session:
        _, found = await _lookup(session, actor, workspace_id, request)
        assert found is not None
        return await _replay(session, found, request), False


async def _lookup(
    session: AsyncSession, actor: Principal, workspace_id: str, request: Request
) -> tuple[WorkspaceScope, InboxEntryRow | None]:
    """Authentication and current permission precede replay lookup; lookup precedes state validation."""
    scope = await workspace_scope(session, actor, workspace_id, "run")
    return scope, await find_request(session, scope.workspace_id, actor.id, request.key)


def _limits(runtime: Runtime) -> InboxLimits:
    return InboxLimits(runtime.settings.control.inbox_count, runtime.settings.control.inbox_bytes)


async def submit_message(
    runtime: Runtime, actor: Principal, workspace_id: str, thread_id: str, message: Message, *, request_key: str
) -> tuple[Submitted, bool]:
    request = Request.of(request_key, "message", thread_id, message)

    async def create(session: AsyncSession, scope: WorkspaceScope) -> tuple[ThreadRow, InboxEntryRow]:
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_open(thread)
        await validate_message(session, scope.workspace_id, message)
        entry = await append_message(
            session,
            thread,
            message,
            principal_id=actor.id,
            authority=execution_authority(actor, scope),
            request=request,
            limits=_limits(runtime),
        )
        return thread, entry

    return await _submit(runtime, actor, workspace_id, request, create)


async def create_thread(
    runtime: Runtime, actor: Principal, workspace_id: str, body: NewThread, *, request_key: str
) -> tuple[Submitted, bool]:
    request = Request.of(request_key, "thread", workspace_id, body)

    async def create(session: AsyncSession, scope: WorkspaceScope) -> tuple[ThreadRow, InboxEntryRow]:
        await validate_message(session, scope.workspace_id, body)
        await validate_caller_headers(session, scope.workspace_id, body.mcp_headers)
        if body.session_id is not None:
            owner = await session.scalar(
                select(SessionRow).where(
                    SessionRow.workspace_id == scope.workspace_id, SessionRow.id == body.session_id
                )
            )
            if owner is None:
                raise not_found("session", body.session_id)
        else:
            owner = new_session(scope.organization_id, scope.workspace_id, actor.id)
            session.add(owner)
        thread = new_thread(owner, mcp_headers=body.mcp_headers)
        session.add(thread)
        await session.flush()
        entry = await append_message(
            session,
            thread,
            body,
            principal_id=actor.id,
            authority=execution_authority(actor, scope),
            request=request,
            limits=_limits(runtime),
        )
        return thread, entry

    return await _submit(runtime, actor, workspace_id, request, create)


async def fork(
    runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, body: Fork, *, request_key: str
) -> tuple[Submitted, bool]:
    """A new thread in the origin's session whose first run continues the origin's committed history."""
    request = Request.of(request_key, "fork", run_id, body)

    async def create(session: AsyncSession, scope: WorkspaceScope) -> tuple[ThreadRow, InboxEntryRow]:
        origin = await get_run(session, scope.workspace_id, run_id)
        origin_thread = await get_thread(session, scope.workspace_id, origin.thread_id, lock=True)
        # Failed and cancelled runs never became history; fork their parent and resubmit instead.
        if origin.status not in {"completed", "waiting"}:
            raise conflict("run", origin.id, f"run_{origin.status}")
        checkpoints.require_compatible(origin)
        await validate_message(session, scope.workspace_id, body)
        owner = await session.get(SessionRow, origin.session_id)
        assert owner is not None
        thread = new_thread(
            owner,
            mcp_headers=origin_thread.mcp_headers,
            origin="fork",
            origin_thread_id=origin_thread.id,
            origin_run_id=origin.id,
        )
        session.add(thread)
        await session.flush()
        if not body.fresh_environments:
            await copy_desired(session, origin_thread, thread)
        entry = await append_message(
            session,
            thread,
            body,
            principal_id=actor.id,
            authority=execution_authority(actor, scope),
            request=request,
            limits=_limits(runtime),
        )
        return thread, entry

    return await _submit(runtime, actor, workspace_id, request, create)
