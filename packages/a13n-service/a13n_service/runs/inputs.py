"""Fenced input assignment and confirmation of durable checkpoint receipts."""

from datetime import datetime

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.agents.tables import AgentRevisionRow
from a13n_service.runs import activity
from a13n_service.runs.attempts import lock_authority
from a13n_service.runs.feedback import FeedbackPayload
from a13n_service.runs.options import compatible
from a13n_service.runs.schemas import AttemptClaim, Checkpoint, MessagePayload, RunOptions
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow


async def ordinary_usage(session: AsyncSession, thread_id: str) -> tuple[int, int]:
    """The sole ordinary-capacity predicate; control feedback has an independent bound."""
    count, size = (
        await session.execute(
            select(
                func.count(), func.coalesce(func.sum(func.octet_length(cast(InboxEntryRow.payload, String))), 0)
            ).where(
                InboxEntryRow.thread_id == thread_id,
                InboxEntryRow.status.in_(("pending", "assigned")),
                InboxEntryRow.kind.in_(("message", "child_result")),
            )
        )
    ).one()
    return count, size


async def lock_thread(session: AsyncSession, claim: AttemptClaim) -> ThreadRow:
    thread = await session.get(ThreadRow, claim.thread_id, with_for_update=True)
    if thread is None:
        raise ServiceError("not_found", "Execution thread was not found")
    return thread


async def confirm_locked(session: AsyncSession, run: RunRow, checkpoint: Checkpoint, now: datetime) -> bool:
    if (
        checkpoint.organization_id != run.organization_id
        or checkpoint.run_id != run.id
        or checkpoint.agent_revision_id != run.agent_revision_id
        or (checkpoint.receipts and checkpoint.receipts[0] != run.source_entry_id)
        or len(set(checkpoint.receipts)) != len(checkpoint.receipts)
    ):
        raise ServiceError("conflict", "Checkpoint receipts do not match the accepted run")
    entries = (
        await session.scalars(
            select(InboxEntryRow)
            .where(InboxEntryRow.assigned_run_id == run.id)
            .order_by(InboxEntryRow.position)
            .with_for_update()
        )
    ).all()
    by_id = {entry.id: entry for entry in entries}
    expected = [run.source_entry_id, *(entry.id for entry in entries if entry.id != run.source_entry_id)]
    if (
        any(entry_id not in by_id for entry_id in checkpoint.receipts)
        or list(checkpoint.receipts) != expected[: len(checkpoint.receipts)]
    ):
        raise ServiceError("conflict", "Checkpoint receipts are not an assigned FIFO prefix")
    if any(by_id[entry_id].status not in {"assigned", "consumed"} for entry_id in checkpoint.receipts):
        raise ServiceError("conflict", "Checkpoint input has a terminal disposition")
    if any(entry.status == "consumed" and entry.id not in checkpoint.receipts for entry in entries):
        raise ServiceError("conflict", "Checkpoint dropped an earlier durable receipt")
    changed = False
    for entry_id in checkpoint.receipts:
        entry = by_id[entry_id]
        if entry.status == "assigned":
            changed = True
            entry.status = "consumed"
            entry.incorporated_checkpoint_seq = checkpoint.sequence
            entry.finished_at = now
    return changed


async def confirm(storage: Storage, claim: AttemptClaim, checkpoint: Checkpoint) -> None:
    async with transaction(storage) as session:
        thread = await lock_thread(session, claim)
        run, _, now = await lock_authority(session, claim)
        if await confirm_locked(session, run, checkpoint, now):
            thread.updated_at = now
            await activity.touch(session, run.workspace_id, [run.session_id])


async def assigned_inputs(storage: Storage, claim: AttemptClaim) -> tuple[tuple[str, MessagePayload], ...]:
    async with transaction(storage) as session:
        await lock_thread(session, claim)
        run, _, _ = await lock_authority(session, claim)
        entries = (
            await session.scalars(
                select(InboxEntryRow)
                .where(
                    InboxEntryRow.assigned_run_id == run.id,
                    InboxEntryRow.status == "assigned",
                    InboxEntryRow.kind == "message",
                )
                .order_by(InboxEntryRow.position)
            )
        ).all()
        entries = sorted(entries, key=lambda entry: (entry.id != run.source_entry_id, entry.position))
        return tuple((entry.id, MessagePayload.model_validate(entry.payload)) for entry in entries)


async def source_feedback(storage: Storage, claim: AttemptClaim) -> FeedbackPayload | None:
    async with transaction(storage) as session:
        await lock_thread(session, claim)
        run, _, _ = await lock_authority(session, claim)
        entry = await session.get(InboxEntryRow, run.source_entry_id)
        if entry is None or entry.kind != "feedback":
            return None
        return FeedbackPayload.model_validate(entry.payload)


async def assign_steers(
    storage: Storage, claim: AttemptClaim, *, max_count: int, max_bytes: int
) -> tuple[tuple[str, MessagePayload], ...]:
    async with transaction(storage) as session:
        thread = await lock_thread(session, claim)
        run, _, now = await lock_authority(session, claim)
        source = await session.get(InboxEntryRow, run.source_entry_id)
        if (
            thread.archived_at is not None
            or run.cancel_requested_at is not None
            or source is None
            or source.status != "consumed"
        ):
            return ()
        entries = (
            await session.scalars(
                select(InboxEntryRow)
                .where(
                    InboxEntryRow.thread_id == thread.id,
                    InboxEntryRow.status == "pending",
                    InboxEntryRow.kind == "message",
                    InboxEntryRow.delivery == "steer",
                    InboxEntryRow.agent_id == run.agent_id,
                )
                .order_by(InboxEntryRow.position)
                .limit(max_count)
                .with_for_update()
            )
        ).all()
        revision = await session.get(AgentRevisionRow, run.agent_revision_id)
        assert revision is not None
        config = AgentConfig.model_validate(revision.config)
        options = RunOptions.model_validate(run.options)
        selected: list[tuple[str, MessagePayload]] = []
        size = 0
        for entry in entries:
            if entry.agent_revision_id not in {None, run.agent_revision_id} or not compatible(
                config, RunOptions.model_validate(entry.options), options
            ):
                continue
            payload = MessagePayload.model_validate(entry.payload)
            size += len(payload.model_dump_json().encode())
            if size > max_bytes:
                break
            entry.status = "assigned"
            entry.assigned_run_id = run.id
            selected.append((entry.id, payload))
        if selected:
            thread.updated_at = now
            await activity.touch(session, run.workspace_id, [run.session_id])
        return tuple(selected)
