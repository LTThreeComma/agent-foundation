"""Terminal SQL facts select verified objects; failure never adopts unconfirmed state."""

import asyncio
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.runs import events, usage
from a13n_service.runs.attempts import lock_authority
from a13n_service.runs.display import Display
from a13n_service.runs.inputs import confirm_locked, lock_thread
from a13n_service.runs.schemas import AttemptClaim, Checkpoint, RunView, SnapshotRef
from a13n_service.runs.snapshots import object_key
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunRow, ThreadRow


async def verify_final(
    objects: LocalObjects, claim: AttemptClaim, checkpoint_ref: SnapshotRef, display_ref: SnapshotRef, *, timeout: float
) -> Checkpoint:
    values = []
    for kind, ref in (("state", checkpoint_ref), ("display", display_ref)):
        async with asyncio.timeout(timeout):
            value = await objects.read(object_key(claim.organization_id, claim.run_id, kind))
        if (
            value is None
            or value.writer != claim.number
            or value.digest != ref.digest
            or len(value.content) != ref.size
            or value.version != ref.version
        ):
            raise ServiceError("conflict", "Final snapshot selection does not match durable bytes")
        values.append(value)
    checkpoint = Checkpoint.model_validate_json(values[0].content)
    display = Display.model_validate_json(values[1].content)
    if (
        checkpoint.organization_id != claim.organization_id
        or checkpoint.run_id != claim.run_id
        or display.organization_id != claim.organization_id
        or display.run_id != claim.run_id
        or checkpoint.sequence != checkpoint_ref.sequence
        or display.sequence != display_ref.sequence
        or checkpoint.format != checkpoint_ref.format
        or display.format != display_ref.format
        or checkpoint.attempt_id != checkpoint_ref.attempt_id
        or display.attempt_id != display_ref.attempt_id
        or checkpoint.candidate not in {"completed", "waiting"}
        or not checkpoint.receipts
        or checkpoint.display_cut.sequence > display.sequence
        or not any(
            segment.attempt_id == checkpoint.display_cut.attempt_id
            and segment.event_sequence >= checkpoint.display_cut.event_sequence
            for segment in display.segments
        )
    ):
        raise ServiceError("conflict", "Final checkpoint/display selection is inconsistent")
    return checkpoint


async def continuation(
    storage: Storage,
    objects: LocalObjects,
    claim: AttemptClaim,
    checkpoint_ref: SnapshotRef,
    display_ref: SnapshotRef,
    *,
    timeout: float = 5,
) -> RunView:
    checkpoint = await verify_final(objects, claim, checkpoint_ref, display_ref, timeout=timeout)
    assert checkpoint.candidate is not None
    try:
        async with transaction(storage) as session:
            thread = await lock_thread(session, claim)
            run, attempt, now = await lock_authority(session, claim)
            if run.cancel_requested_at is not None:
                raise ServiceError("conflict", "Cancellation precedes completion")
            await confirm_locked(session, run, checkpoint, now)
            usage_at_seal = await usage.totals(session, run.id)
            pending = (
                await session.scalars(
                    select(InboxEntryRow)
                    .where(InboxEntryRow.assigned_run_id == run.id, InboxEntryRow.status == "assigned")
                    .with_for_update()
                )
            ).all()
            run.status = checkpoint.candidate
            run.output = {"text": checkpoint.output} if checkpoint.candidate == "completed" else None
            run.wait_reason = checkpoint.waiting.reason if checkpoint.waiting is not None else None
            run.pending = checkpoint.waiting.model_dump(mode="json") if checkpoint.waiting is not None else None
            run.sealed_checkpoint = checkpoint_ref.model_dump(mode="json")
            run.sealed_display = display_ref.model_dump(mode="json")
            run.sealed_at = now
            run.current_attempt_id = None
            run.usage_at_seal = usage_at_seal
            attempt.status = "succeeded"
            attempt.finished_at = now
            thread.current_run_id = None
            thread.head_run_id = run.id
            thread.last_run_id = run.id
            for entry in pending:
                entry.status = "pending"
                entry.assigned_run_id = None
            facts = [events.stage(run, attempt=attempt), events.stage(run)]
            await session.flush()
            await session.refresh(run)
            result = RunView.model_validate(run)
            await events.flush(session, facts)
            return result
    except SQLAlchemyError:
        async with short_session(storage) as session:
            run = await session.get(RunRow, claim.run_id)
            if (
                run is not None
                and run.status == checkpoint.candidate
                and run.sealed_checkpoint == checkpoint_ref.model_dump(mode="json")
                and run.sealed_display == display_ref.model_dump(mode="json")
            ):
                return RunView.model_validate(run)
        raise


async def fail_locked(
    session: AsyncSession,
    thread: ThreadRow,
    run: RunRow,
    attempt: AttemptRow | None,
    now: datetime,
    *,
    status: Literal["failed", "cancelled"],
    code: str,
    message: str,
) -> list[events.EventRow]:
    """Only worker fence, accepted interrupt or locked expiry may call this transition."""
    usage_at_seal = await usage.totals(session, run.id)
    entries = (
        await session.scalars(
            select(InboxEntryRow)
            .where(InboxEntryRow.assigned_run_id == run.id, InboxEntryRow.status == "assigned")
            .with_for_update()
        )
    ).all()
    run.status = status
    run.failure = {"code": code, "message": message}
    run.sealed_at = now
    run.current_attempt_id = None
    run.usage_at_seal = usage_at_seal
    thread.current_run_id = None
    thread.last_run_id = run.id
    for entry in entries:
        entry.status = "failed"
        entry.failure = {"code": code, "message": message}
        entry.finished_at = now
    facts = []
    if attempt is not None:
        attempt.status = status
        attempt.failure = run.failure
        attempt.finished_at = now
        facts.append(events.stage(run, attempt=attempt))
    facts.append(events.stage(run))
    return facts


async def failed(storage: Storage, claim: AttemptClaim, *, code: str, message: str) -> None:
    async with transaction(storage) as session:
        thread = await lock_thread(session, claim)
        run, attempt, now = await lock_authority(session, claim)
        cancelled = run.cancel_requested_at is not None
        facts = await fail_locked(
            session,
            thread,
            run,
            attempt,
            now,
            status="cancelled" if cancelled else "failed",
            code="cancelled" if cancelled else code,
            message="Run was cancelled" if cancelled else message,
        )
        await events.flush(session, facts)


async def expire(storage: Storage, run_id: str, *, backoff_seconds: float = 1) -> bool:
    from datetime import timedelta

    async with short_session(storage) as session:
        thread_id = await session.scalar(select(RunRow.thread_id).where(RunRow.id == run_id))
    if thread_id is None:
        return False
    async with transaction(storage) as session:
        thread = await session.get(ThreadRow, thread_id, with_for_update=True)
        run = await session.get(RunRow, run_id, with_for_update=True)
        if thread is None or run is None or run.status != "running" or run.current_attempt_id is None:
            return False
        attempt = await session.get(AttemptRow, run.current_attempt_id, with_for_update=True)
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        if attempt is None or attempt.status not in {"leased", "running"} or attempt.lease_expires_at > now:
            return False
        if run.cancel_requested_at is not None or run.attempts >= run.max_attempts:
            cancelled = run.cancel_requested_at is not None
            facts = await fail_locked(
                session,
                thread,
                run,
                attempt,
                now,
                status="cancelled" if cancelled else "failed",
                code="cancelled" if cancelled else "attempts_exhausted",
                message="Run was cancelled" if cancelled else "Execution attempt limit reached",
            )
        else:
            attempt.status = "failed"
            attempt.failure = {"code": "lease_expired", "message": "Worker lease expired"}
            attempt.finished_at = now
            run.status = "accepted"
            run.current_attempt_id = None
            run.available_at = now + timedelta(seconds=backoff_seconds)
            facts = [events.stage(run, attempt=attempt), events.stage(run)]
        await events.flush(session, facts)
        return True
