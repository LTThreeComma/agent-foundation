"""Atomic request-key evidence, bounded input ownership and one run acceptance path."""

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents.service import validate_configuration
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.resources.connections.scope import connection_scope, validate_context
from a13n_service.runs import activity, events, feedback
from a13n_service.runs.feedback import FeedbackSubmission
from a13n_service.runs.input_views import project
from a13n_service.runs.inputs import ordinary_usage
from a13n_service.runs.options import compatible
from a13n_service.runs.policy import AcceptedIntent, AdmissionPolicy, authorize_execution
from a13n_service.runs.schemas import (
    AgentConfig,
    AssetInput,
    EnvironmentPathInput,
    InboxSubmission,
    MessagePayload,
    NewThread,
    RunOptions,
    RunView,
    Submission,
    Submitted,
)
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from a13n_service.runs.waiting import Waiting
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Scope, authorize, execution_authority
from a13n_service.tenancy.grants import principal_for, resolve_workspace


async def replay(session: AsyncSession, actor: Principal, workspace_id: str, key: str, digest: str) -> Submitted | None:
    entry = await session.scalar(
        select(InboxEntryRow).where(
            InboxEntryRow.workspace_id == workspace_id,
            InboxEntryRow.principal_id == actor.id,
            InboxEntryRow.request_key == key,
        )
    )
    if entry is None:
        return None
    if entry.request_digest != digest:
        raise ServiceError("conflict", "Idempotency key belongs to different input")
    return await submitted(session, entry, replayed=True)


async def submitted(session: AsyncSession, entry: InboxEntryRow, *, replayed: bool) -> Submitted:
    thread = await session.get(ThreadRow, entry.thread_id)
    assert thread is not None
    run = await session.get(RunRow, entry.assigned_run_id) if entry.assigned_run_id is not None else None
    return Submitted(
        thread_id=thread.id,
        session_id=thread.session_id,
        entry=project(entry),
        run=RunView.model_validate(run) if run is not None else None,
        replayed=replayed,
    )


async def validate_input_references(
    session: AsyncSession,
    principal: Principal,
    scope: Scope,
    payload: MessagePayload,
    *,
    authority: ExecutionAuthority | None = None,
) -> None:
    """Reject unavailable sources and recheck Asset selection at acceptance."""
    if any(isinstance(part, EnvironmentPathInput) for part in payload.content):
        raise ServiceError("unavailable", "Selected Environment path input is not configured")
    identities = {part.asset_id for part in payload.content if isinstance(part, AssetInput)}
    if not identities:
        return
    authorize(principal, scope, "read", authority=authority)
    rows = (
        await session.scalars(
            select(AssetRow)
            .where(AssetRow.workspace_id == scope.workspace_id, AssetRow.id.in_(identities))
            .with_for_update(read=True)
        )
    ).all()
    if len(rows) != len(identities):
        raise ServiceError("not_found", "Selected Asset was not found")
    if any(row.retired_at is not None for row in rows):
        raise ServiceError("disabled", "Selected Asset is retired")


def feedback_target(thread: ThreadRow, target: RunRow) -> bool:
    return thread.current_run_id is None and thread.head_run_id == target.id and target.status == "waiting"


async def accept(
    session: AsyncSession,
    thread: ThreadRow,
    entry: InboxEntryRow | None,
    *,
    max_attempts: int,
    keys: KeyRing,
    policy: AdmissionPolicy | None = None,
    actor: Principal | None = None,
) -> tuple[RunRow | None, list[events.EventRow], bool]:
    """The thread lock serializes explicit submission and bounded automatic advancement."""
    if thread.archived_at is not None or thread.current_run_id is not None:
        return None, [], False
    latest = await session.get(RunRow, thread.last_run_id) if thread.last_run_id else None
    head = await session.get(RunRow, thread.head_run_id) if thread.head_run_id else None
    waiting = Waiting.model_validate(head.pending) if head is not None and head.status == "waiting" else None
    eligible_kinds = ("feedback",) if waiting is not None and not waiting.question_only else ("message", "feedback")
    if latest is not None and latest.status in {"failed", "cancelled"}:
        identities = (
            [entry.id] if entry is not None and entry.status == "pending" and entry.kind in eligible_kinds else []
        )
    else:
        identities = (
            await session.scalars(
                select(InboxEntryRow.id)
                .where(
                    InboxEntryRow.thread_id == thread.id,
                    InboxEntryRow.status == "pending",
                    InboxEntryRow.kind.in_(eligible_kinds),
                )
                .order_by(InboxEntryRow.position)
                .limit(32)
            )
        ).all()
    observed = {actor.id: actor} if actor is not None else {}
    changed = False
    for identity in identities:
        candidate = await session.get(InboxEntryRow, identity, with_for_update=True)
        assert candidate is not None
        try:
            async with session.begin_nested():
                principal = observed.get(candidate.principal_id)
                if principal is None:
                    principal = await principal_for(
                        session, candidate.principal_id, confinement=Scope(thread.organization_id, thread.workspace_id)
                    )
                    observed[principal.id] = principal
                accepted, facts = await _accept_entry(
                    session, thread, candidate, principal, max_attempts=max_attempts, keys=keys, policy=policy
                )
                if accepted is not None:
                    return accepted, facts, True
                changed |= candidate.status == "failed"
        except ServiceError as error:
            if error.code not in {
                "forbidden",
                "unauthenticated",
                "disabled",
                "invalid_argument",
                "not_found",
                "rate_limited",
            }:
                raise
            candidate = await session.get(InboxEntryRow, identity)
            assert candidate is not None
            changed = True
            candidate.status = "failed"
            candidate.failure = {"code": error.code, "message": error.message}
            candidate.finished_at = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    return None, [], changed


async def _accept_entry(
    session: AsyncSession,
    thread: ThreadRow,
    entry: InboxEntryRow,
    principal: Principal,
    *,
    max_attempts: int,
    keys: KeyRing,
    policy: AdmissionPolicy | None = None,
) -> tuple[RunRow | None, list[events.EventRow]]:
    """Caller owns the thread lock; all creation paths use the same frozen selection."""
    if thread.archived_at is not None or thread.current_run_id is not None:
        return None, []
    if entry.status != "pending":
        return None, []
    authority = ExecutionAuthority.model_validate(entry.authority)
    authorize(principal, Scope(thread.organization_id, thread.workspace_id), "run", authority=authority)
    parent_id = thread.head_run_id or thread.origin_run_id
    parent = await session.get(RunRow, parent_id) if parent_id else None
    if entry.kind == "feedback":
        if parent is None or entry.waiting_run_id != parent.id or not feedback_target(thread, parent):
            entry.status = "failed"
            entry.failure = {"code": "stale_feedback", "message": "Waiting run is no longer the idle thread head"}
            entry.finished_at = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            return None, []
        principal = await authorize_execution(session, parent)
        authority = ExecutionAuthority.model_validate(parent.authority)
        agent_id, pinned_revision, option_values = parent.agent_id, parent.agent_revision_id, parent.options
    else:
        if (
            parent is not None
            and parent.status == "waiting"
            and not Waiting.model_validate(parent.pending).question_only
        ):
            return None, []
        if parent_id is not None and (parent is None or parent.status not in {"completed", "waiting"}):
            raise ServiceError("conflict", "Thread has no continuable parent")
        agent_id, pinned_revision, option_values = entry.agent_id, entry.agent_revision_id, entry.options
        await validate_input_references(
            session,
            principal,
            Scope(thread.organization_id, thread.workspace_id),
            MessagePayload.model_validate(entry.payload),
            authority=authority,
        )
    agent = await session.get(AgentRow, agent_id)
    if agent is None or agent.workspace_id != thread.workspace_id or agent.archived_at is not None:
        raise ServiceError("disabled", "Selected agent is unavailable")
    revision_id = pinned_revision or agent.default_revision_id
    revision = await session.get(AgentRevisionRow, revision_id) if revision_id else None
    if revision is None or revision.agent_id != agent.id:
        raise ServiceError("invalid_argument", "Agent revision was not found")
    config = AgentConfig.model_validate(revision.config)
    await validate_configuration(session, principal, Scope(thread.organization_id, thread.workspace_id), config)
    options = RunOptions.model_validate(option_values)
    await validate_context(
        session,
        principal,
        Scope(thread.organization_id, thread.workspace_id),
        config,
        options.mcp_headers,
        keys=keys,
        authority=authority,
    )
    run = RunRow(
        id=new_object_id("run"),
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        session_id=thread.session_id,
        thread_id=thread.id,
        agent_id=agent.id,
        agent_revision_id=revision.id,
        revision_selection="pinned" if pinned_revision else "default",
        principal_id=principal.id,
        authority=authority.model_dump(mode="json"),
        options=options.model_dump(mode="json"),
        environment_mounts=[],
        source_entry_id=entry.id,
        trigger="input",
        lineage="continue" if thread.head_run_id else "fork" if parent_id else "root",
        parent_run_id=parent_id,
        status="accepted",
        attempts=0,
        max_attempts=max_attempts,
        max_usage=options.max_usage.model_dump(mode="json") if options.max_usage else {"requests": config.max_requests},
        labels=options.labels,
        lifecycle_seq=0,
    )
    if policy is not None:
        await policy.accept(
            session,
            AcceptedIntent(
                organization_id=run.organization_id,
                workspace_id=run.workspace_id,
                session_id=run.session_id,
                thread_id=run.thread_id,
                run_id=run.id,
                principal_id=run.principal_id,
                agent_revision_id=run.agent_revision_id,
                model_id=config.model_id,
            ),
        )
    session.add(run)
    thread.current_run_id = run.id
    entry.assigned_run_id = run.id
    entry.status = "assigned"
    return run, [events.stage(run)]


async def submit(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    body: InboxSubmission,
    *,
    request_key: str,
    thread_id: str | None,
    max_entries: int,
    max_bytes: int,
    max_attempts: int,
    keys: KeyRing,
    policy: AdmissionPolicy | None = None,
) -> Submitted:
    if not request_key or len(request_key.encode()) > 128 or any(ord(c) < 32 for c in request_key):
        raise ServiceError("invalid_argument", "Idempotency-Key must contain 1 to 128 printable bytes")
    operation = "thread.create" if thread_id is None else "inbox.submit"
    encoded = json.dumps([operation, thread_id, body.model_dump(mode="json")], sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    payload = body.payload.model_dump(mode="json") if isinstance(body, Submission) else None
    payload_size = len(json.dumps(payload).encode())
    scope: Scope | None = None
    try:
        async with transaction(storage) as session:
            workspace = await resolve_workspace(session, workspace_id)
            workspace_id = workspace.id
            scope = Scope(workspace.organization_id, workspace.id)
            authorize(actor, scope, "run")
            if prior := await replay(session, actor, workspace_id, request_key, digest):
                return prior
            if workspace.archived_at is not None:
                raise ServiceError("disabled", "Workspace is archived")
            if isinstance(body, Submission) and payload_size > max_bytes:
                raise ServiceError("payload_too_large", "Input exceeds inbox byte capacity")
            if isinstance(body, Submission):
                await validate_input_references(session, actor, scope, body.payload)
            if thread_id is None:
                if isinstance(body, FeedbackSubmission):
                    raise ServiceError("invalid_argument", "Feedback requires an existing thread")
                session_id = body.session_id if isinstance(body, NewThread) else None
                if session_id is not None:
                    owner = await session.get(SessionRow, session_id)
                    if owner is None or owner.workspace_id != workspace_id:
                        raise ServiceError("not_found", "Session was not found")
                else:
                    session_id = new_object_id("sess")
                    session.add(
                        SessionRow(
                            id=session_id,
                            organization_id=scope.organization_id,
                            workspace_id=workspace_id,
                            labels={},
                            created_by_id=actor.id,
                        )
                    )
                    await session.flush()
                thread = ThreadRow(
                    id=new_object_id("thrd"),
                    organization_id=scope.organization_id,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    origin="new",
                    labels={},
                )
                session.add(thread)
                await session.flush()
            else:
                thread = await session.get(ThreadRow, thread_id, with_for_update=True)
                if thread is None or thread.workspace_id != workspace_id:
                    raise ServiceError("not_found", "Thread was not found")
                if thread.archived_at is not None:
                    raise ServiceError("disabled", "Thread is archived")
            if isinstance(body, Submission):
                count, byte_count = await ordinary_usage(session, thread.id)
                if count >= max_entries or byte_count + payload_size > max_bytes:
                    raise ServiceError(
                        "rate_limited",
                        "Thread inbox capacity reached",
                        {"count": count, "bytes": byte_count, "count_limit": max_entries, "byte_limit": max_bytes},
                    )
            position = (
                await session.execute(
                    select(func.coalesce(func.max(InboxEntryRow.position), 0)).where(
                        InboxEntryRow.thread_id == thread.id
                    )
                )
            ).scalar_one() + 1
            if isinstance(body, FeedbackSubmission):
                target = await session.get(RunRow, body.waiting_run_id)
                if target is None or target.thread_id != thread.id or target.workspace_id != workspace_id:
                    raise ServiceError("not_found", "Waiting run was not found in this thread")
                if target.status != "waiting" or target.pending is None:
                    raise ServiceError("invalid_argument", "Feedback target has no sealed pending batch")
                payload = feedback.normalize(Waiting.model_validate(target.pending), body).model_dump(mode="json")
                stale = not feedback_target(thread, target)
                fields = {
                    "kind": "feedback",
                    "delivery": "next_run",
                    "waiting_run_id": target.id,
                    "agent_id": None,
                    "agent_revision_id": None,
                    "options": {},
                    "status": "failed" if stale else "pending",
                    "failure": {"code": "stale_feedback", "message": "Waiting run is no longer the idle thread head"}
                    if stale
                    else None,
                    "finished_at": (await session.execute(select(func.clock_timestamp()))).scalar_one()
                    if stale
                    else None,
                }
            else:
                agent = await session.get(AgentRow, body.agent_id)
                if agent is None or agent.workspace_id != workspace_id or agent.archived_at is not None:
                    raise ServiceError("invalid_argument", "Agent is unavailable in this workspace")
                if body.agent_revision_id is not None:
                    revision = await session.get(AgentRevisionRow, body.agent_revision_id)
                    if revision is None or revision.agent_id != agent.id:
                        raise ServiceError("invalid_argument", "Agent revision was not found")
                revision_id = body.agent_revision_id or agent.default_revision_id
                if thread.current_run_id and body.delivery == "steer":
                    active = await session.get(RunRow, thread.current_run_id)
                    if (
                        active is not None
                        and active.agent_id == body.agent_id
                        and body.agent_revision_id in {None, active.agent_revision_id}
                    ):
                        active_revision = await session.get(AgentRevisionRow, active.agent_revision_id)
                        assert active_revision is not None
                        active_config = AgentConfig.model_validate(active_revision.config)
                        if body.options.mcp_headers.keys() <= connection_scope(active_config).keys() and compatible(
                            active_config, body.options, RunOptions.model_validate(active.options)
                        ):
                            revision_id = active.agent_revision_id
                revision = await session.get(AgentRevisionRow, revision_id) if revision_id else None
                if revision is None or revision.agent_id != agent.id:
                    raise ServiceError("invalid_argument", "Agent revision was not found")
                await validate_context(
                    session,
                    actor,
                    scope,
                    AgentConfig.model_validate(revision.config),
                    body.options.mcp_headers,
                    keys=keys,
                )
                fields = {
                    "kind": "message",
                    "delivery": body.delivery,
                    "agent_id": body.agent_id,
                    "agent_revision_id": body.agent_revision_id,
                    "options": body.options.model_dump(mode="json"),
                    "status": "pending",
                }
            entry = InboxEntryRow(
                id=new_object_id("inb"),
                organization_id=scope.organization_id,
                workspace_id=workspace_id,
                thread_id=thread.id,
                position=position,
                principal_id=actor.id,
                authority=execution_authority(actor, scope).model_dump(mode="json"),
                payload=payload,
                request_key=request_key,
                request_digest=digest,
                request_kind=operation,
                request_target=thread_id,
                **fields,
            )
            session.add(entry)
            await session.flush()
            facts = []
            if entry.status == "pending":
                _, facts, _ = await accept(
                    session, thread, entry, max_attempts=max_attempts, keys=keys, policy=policy, actor=actor
                )
            # Every visible append changes the thread version, even when already busy.
            thread.updated_at = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            await session.flush()
            result = await submitted(session, entry, replayed=False)
            if not facts:
                await activity.touch(session, workspace_id, [thread.session_id])
            await events.flush(session, facts)
            return result
    except IntegrityError as error:
        if scope is None or getattr(getattr(error.orig, "diag", None), "constraint_name", None) != "uq_inbox_request":
            raise
    # Unique-key losers have rolled back their entire tentative session/thread/entry.
    async with transaction(storage) as session:
        # The losing transaction rolled back; reuse this operation's observed authority.
        authorize(actor, scope, "run")
        prior = await replay(session, actor, workspace_id, request_key, digest)
        if prior is None:
            raise ServiceError("unavailable", "Request winner could not be resolved")
        return prior
