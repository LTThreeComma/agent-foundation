"""Turning a thread's eligible source into its next run.

`start_run` is the only function that creates a run; `accept` picks a queued source for it, and resume,
fork and spawn call it with their own source. Both are SQL-only and run under the caller's thread lock.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents.service import select_revision
from a13n_service.runs import inbox
from a13n_service.runs.admission import AcceptedIntent
from a13n_service.runs.environments.mounts import freeze_mounts
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Failure, Pending, Resume, Trigger
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.runs.webhooks import notify_subscribers
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope, authorize
from a13n_service.tenancy.grants import principal_for

# Entries examined per acceptance; rejected ones are failed, so a later call makes progress.
SCAN = 16


class Eligible(Enum):
    NOTHING = "nothing"
    MESSAGES = "messages"
    ANY = "any"


def eligibility(thread: ThreadRow, head: RunRow | None) -> Eligible:
    if thread.archived_at is not None or thread.current_run_id is not None:
        return Eligible.NOTHING
    if head is not None and head.status == "waiting":
        # Every pending item decides: a mixed wait needs resume even if it also asks a question.
        return Eligible.MESSAGES if Pending.model_validate(head.pending).question_only else Eligible.NOTHING
    return Eligible.ANY


def paused(last: RunRow | None) -> bool:
    """After a failed or cancelled run only an explicit action continues the thread."""
    return last is not None and last.status in {"failed", "cancelled"}


def candidates(
    kind: Eligible, is_paused: bool, pending: Sequence[InboxEntryRow], explicit: InboxEntryRow | None
) -> list[InboxEntryRow]:
    """Sources in selection order: by thread-state eligibility, then position."""
    if kind is Eligible.NOTHING:
        return []
    if is_paused:
        # An explicit submission may start that new message; unrelated pending entries never replace it.
        return [explicit] if explicit is not None else []
    return [entry for entry in pending if kind is Eligible.ANY or entry.kind == "message"]


@dataclass(frozen=True, slots=True)
class Source:
    """What a run starts from: a queued entry, or the answers resuming a waiting run."""

    trigger: Trigger
    principal_id: str
    authority: ExecutionAuthority
    agent_id: str
    agent_revision_id: str | None
    revision_selection: Literal["pinned", "default", "inherited"]
    options: dict
    entry: InboxEntryRow | None = None
    resume: Resume | None = None
    resumed_by_id: str | None = None
    request_key: str | None = None
    request_digest: str | None = None

    @classmethod
    def message(cls, entry: InboxEntryRow, trigger: Trigger) -> "Source":
        assert entry.agent_id is not None
        return cls(
            trigger=trigger,
            principal_id=entry.principal_id,
            authority=ExecutionAuthority.model_validate(entry.authority),
            agent_id=entry.agent_id,
            agent_revision_id=entry.agent_revision_id,
            revision_selection="pinned" if entry.agent_revision_id else "default",
            options=entry.options,
            entry=entry,
        )

    @classmethod
    def inherited(cls, run: RunRow, trigger: Trigger, **values: object) -> "Source":
        """Resume and child results continue with the identity, revision and options of an earlier run."""
        return cls(
            trigger=trigger,
            principal_id=run.principal_id,
            authority=ExecutionAuthority.model_validate(run.authority),
            agent_id=run.agent_id,
            agent_revision_id=run.agent_revision_id,
            revision_selection="inherited",
            options=inbox.run_options(run),
            **values,  # type: ignore[arg-type]
        )


async def _run(session: AsyncSession, run_id: str | None) -> RunRow | None:
    return await session.get(RunRow, run_id) if run_id is not None else None


async def parent_of(
    session: AsyncSession, thread: ThreadRow
) -> tuple[RunRow | None, Literal["root", "continue", "fork"]]:
    """Initial history has one rule: this thread's head, else a fork's origin, else empty.

    A failed run is never a baseline, so a fork keeps its origin until the thread has its own head.
    """
    if thread.head_run_id is not None:
        return await _run(session, thread.head_run_id), "continue"
    if thread.origin == "fork":
        return await _run(session, thread.origin_run_id), "fork"
    return None, "root"


async def root_run_id(session: AsyncSession, thread: ThreadRow, run_id: str) -> str:
    """Children share their delegation root's allowance, so a budget cannot be escaped by spawning."""
    while thread.origin == "child" and thread.origin_run_id is not None:
        origin = await session.get(RunRow, thread.origin_run_id)
        parent_thread = await session.get(ThreadRow, origin.thread_id) if origin else None
        if origin is None or parent_thread is None:
            break
        run_id, thread = origin.id, parent_thread
    return run_id


async def start_run(session: AsyncSession, runtime: Runtime, thread: ThreadRow, source: Source) -> RunRow:
    """Create the accepted run. The caller holds the thread lock and has checked eligibility."""
    scope = WorkspaceScope(thread.organization_id, thread.workspace_id)
    principal = await principal_for(session, source.principal_id, confinement=scope)
    authorize(principal, scope, "run", authority=source.authority)
    revision = await select_revision(session, thread.workspace_id, source.agent_id, source.agent_revision_id)
    parent, lineage = await parent_of(session, thread)
    mounts = await freeze_mounts(
        session, thread, principal_id=source.principal_id, template_id=revision.config.environment_template_id
    )
    current = await now(session)
    run = RunRow(
        id=new_object_id("run"),
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        session_id=thread.session_id,
        thread_id=thread.id,
        agent_id=revision.agent_id,
        agent_revision_id=revision.revision_id,
        revision_selection=source.revision_selection,
        principal_id=source.principal_id,
        authority=source.authority.model_dump(mode="json"),
        # Frozen here: the thread's headers apply to this run even if the thread is edited later.
        options={**source.options, "mcp_headers": thread.mcp_headers},
        environment_mounts=mounts,
        source_entry_id=source.entry.id if source.entry is not None else None,
        resume=source.resume.model_dump(mode="json") if source.resume is not None else None,
        resumed_by_id=source.resumed_by_id,
        request_key=source.request_key,
        request_digest=source.request_digest,
        trigger=source.trigger,
        lineage=lineage,
        parent_run_id=parent.id if parent is not None else None,
        status="accepted",
        available_at=current,
        max_attempts=runtime.settings.worker.max_attempts,
        max_usage=source.options.get("max_usage"),
        labels=source.options.get("labels", {}),
    )
    session.add(run)
    await session.flush()
    if source.entry is not None:
        inbox.assign(source.entry, run)
    thread.current_run_id = run.id
    await session.flush()
    if runtime.admission is not None:
        await runtime.admission.accept(
            session,
            AcceptedIntent(
                organization_id=run.organization_id,
                workspace_id=run.workspace_id,
                session_id=run.session_id,
                thread_id=run.thread_id,
                run_id=run.id,
                principal_id=run.principal_id,
                agent_id=run.agent_id,
                agent_revision_id=run.agent_revision_id,
                trigger=run.trigger,
                root_run_id=await root_run_id(session, thread, run.id),
            ),
        )
    await notify_subscribers(
        session, runtime.keys, run, ["run.accepted"], at=current, limit=runtime.settings.control.subscriptions
    )
    runtime.wake_workers(session)
    return run


async def _source(session: AsyncSession, entry: InboxEntryRow, explicit: InboxEntryRow | None) -> Source | Failure:
    if entry.kind == "message":
        return Source.message(entry, "input" if entry is explicit else "queued")
    origin = await _run(session, entry.origin_run_id)
    if origin is None or origin.status not in {"completed", "waiting"}:
        return Failure(code="origin_not_committed", message="The spawning run never became thread history")
    return Source.inherited(origin, "child_result", entry=entry)


async def accept(
    session: AsyncSession, runtime: Runtime, thread: ThreadRow, *, explicit: InboxEntryRow | None = None
) -> RunRow | None:
    """Start the thread's next run from its eligible queued source, if any. Rejected entries fail in place."""
    head = await _run(session, thread.head_run_id)
    kind = eligibility(thread, head)
    if kind is Eligible.NOTHING:
        return None
    pending = await inbox.pending_entries(session, thread.id, limit=SCAN)
    for entry in candidates(kind, paused(await _run(session, thread.last_run_id)), pending, explicit):
        source = await _source(session, entry, explicit)
        if isinstance(source, Source):
            try:
                # A savepoint makes one entry's rejection its own; database errors still abort the transaction.
                async with session.begin_nested():
                    return await start_run(session, runtime, thread, source)
            except ServiceError as error:
                source = Failure(code=error.code, message=error.message)
        inbox.fail(entry, source, at=await now(session))
        await session.flush()
    return None


async def advance(runtime: Runtime, thread_id: str, *, skip_locked: bool = False) -> RunRow | None:
    """Successor acceptance in its own transaction after a seal or delivery; the sweep covers a lost call."""
    async with transaction(runtime.storage) as session:
        thread = await session.scalar(
            select(ThreadRow).where(ThreadRow.id == thread_id).with_for_update(skip_locked=skip_locked)
        )
        if thread is None:
            return None
        return await accept(session, runtime, thread)


class ThreadAdvancer:
    """The advance_threads sweep: idle, unpaused threads with pending input, visited in rotating ID order.

    Rotation keeps threads whose entries stay pending (an approval wait) from starving the rest.
    """

    def __init__(self, runtime: Runtime, *, batch: int):
        self.runtime, self.batch = runtime, batch
        self.after = ""

    async def __call__(self) -> None:
        async with transaction(self.runtime.storage) as session:
            ids = (
                await session.scalars(
                    select(ThreadRow.id)
                    .where(
                        ThreadRow.current_run_id.is_(None),
                        ThreadRow.archived_at.is_(None),
                        ThreadRow.last_run_id.is_not_distinct_from(ThreadRow.head_run_id),
                        ThreadRow.id > self.after,
                        exists().where(InboxEntryRow.thread_id == ThreadRow.id, InboxEntryRow.status == "pending"),
                    )
                    .order_by(ThreadRow.id)
                    .limit(self.batch)
                )
            ).all()
        self.after = ids[-1] if len(ids) == self.batch else ""
        for thread_id in ids:
            await advance(self.runtime, thread_id, skip_locked=True)
