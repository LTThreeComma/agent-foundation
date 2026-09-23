"""Public conversation view: canonical inputs, durable observations and SQL outcome."""

import asyncio
import hashlib
import json

from pydantic import BaseModel
from sqlalchemy import select

from a13n_service.infra.cursors import encode
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.local import LocalObjects, ObjectCorrupt, StoredObject
from a13n_service.runs import input_views
from a13n_service.runs.display import Display, Segment
from a13n_service.runs.schemas import Checkpoint, DisplayCut, EntryView, RunStatus, RunView, SnapshotRef
from a13n_service.runs.snapshots import object_key
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow
from a13n_service.runs.waiting import PendingItem, WaitReason
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.grants import workspace_scope


class RunItems(BaseModel):
    workspace_id: str
    run_id: str
    status: RunStatus
    wait_reason: WaitReason | None = None
    pending: tuple[PendingItem, ...] | None = None
    current_attempt_id: str | None
    display_version: str
    cursor: str | None
    execution_checkpoint_cut: DisplayCut | None
    inputs: list[EntryView]
    next_input_cursor: str | None
    segments: list[Segment]
    output: dict | None
    failure: dict | None
    complete: bool


def _selected(value: StoredObject | None, raw: dict | None) -> None:
    if raw is None:
        raise ServiceError("conflict", "Sealed run has no snapshot selection")
    ref = SnapshotRef.model_validate(raw)
    if value is None or value.digest != ref.digest or len(value.content) != ref.size or value.version != ref.version:
        raise ServiceError("unavailable", "Sealed snapshot is unavailable")


async def items(
    storage: Storage,
    objects: LocalObjects,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    *,
    limit: int,
    cursor: str | None,
    timeout: float,
) -> RunItems:
    for _ in range(3):
        async with short_session(storage) as session:
            scope = await workspace_scope(session, actor, workspace_id, "read")
            assert scope.workspace_id is not None
            workspace_id = scope.workspace_id
            row = await session.get(RunRow, run_id)
            if row is None or row.workspace_id != workspace_id:
                raise ServiceError("not_found", "Run was not found")
            run = RunView.model_validate(row)
            organization_id, attempts = row.organization_id, row.attempts
            checkpoint_ref, display_ref = row.sealed_checkpoint, row.sealed_display
            thread_version = await session.scalar(select(ThreadRow.version).where(ThreadRow.id == row.thread_id))
            attempt_numbers = {
                identity: number
                for identity, number in (
                    await session.execute(select(AttemptRow.id, AttemptRow.number).where(AttemptRow.run_id == row.id))
                ).all()
            }
            inputs, next_cursor = await input_views.page(
                session,
                thread_id=row.thread_id,
                run_id=row.id,
                limit=limit,
                cursor=cursor,
            )
        try:
            async with asyncio.timeout(timeout):
                state_object = await objects.read(object_key(organization_id, run.id, "state"))
                display_object = await objects.read(object_key(organization_id, run.id, "display"))
            checkpoint = Checkpoint.model_validate_json(state_object.content) if state_object else None
            display = Display.model_validate_json(display_object.content) if display_object else None
        except (TimeoutError, OSError, ObjectCorrupt, ValueError):
            raise ServiceError("unavailable", "Durable run view is unavailable") from None
        async with short_session(storage) as session:
            current = (
                await session.execute(
                    select(RunRow.version, ThreadRow.version)
                    .join(ThreadRow, RunRow.thread_id == ThreadRow.id)
                    .where(RunRow.id == run.id)
                )
            ).one_or_none()
        if current != (run.version, thread_version):
            continue
        if run.status in {"completed", "waiting"}:
            _selected(state_object, checkpoint_ref)
            _selected(display_object, display_ref)
        if checkpoint is not None and (
            checkpoint.run_id != run.id
            or checkpoint.organization_id != organization_id
            or checkpoint.agent_revision_id != run.agent_revision_id
            or attempt_numbers.get(checkpoint.attempt_id) != checkpoint.attempt_number
        ):
            raise ServiceError("conflict", "Checkpoint belongs to another run")
        if display is not None and (display.run_id != run.id or display.organization_id != organization_id):
            raise ServiceError("conflict", "Display belongs to another run")
        if any(
            value.writer is None or value.writer > attempts
            for value in (state_object, display_object)
            if value is not None
        ):
            raise ServiceError("conflict", "Run object has an invalid writer")
        if checkpoint is not None and (display is None or display.sequence < checkpoint.display_cut.sequence):
            continue
        segments = display.segments if display else []
        if any(attempt_numbers.get(segment.attempt_id) != segment.attempt_number for segment in segments):
            raise ServiceError("conflict", "Display segment belongs to another attempt")
        if run.status in {"failed", "cancelled", "accepted"}:
            for segment in segments:
                segment.interrupted = True
                segment.execution_cut = checkpoint.display_cut if checkpoint else None
        version = hashlib.sha256(
            json.dumps(
                [
                    run.id,
                    run.version,
                    thread_version,
                    state_object.version if state_object else None,
                    display_object.version if display_object else None,
                ]
            ).encode()
        ).hexdigest()
        number = attempt_numbers.get(run.current_attempt_id) if run.current_attempt_id else None
        sequence = next(
            (segment.event_sequence for segment in segments if segment.attempt_id == run.current_attempt_id), 0
        )
        return RunItems(
            workspace_id=workspace_id,
            run_id=run.id,
            status=run.status,
            wait_reason=run.wait_reason,
            pending=run.pending,
            current_attempt_id=run.current_attempt_id,
            display_version=version,
            cursor=encode("run-stream", run.id, number, sequence) if number is not None else None,
            execution_checkpoint_cut=checkpoint.display_cut if checkpoint else None,
            inputs=inputs,
            next_input_cursor=next_cursor,
            segments=segments,
            output=run.output,
            failure=run.failure,
            complete=run.status in {"completed", "waiting", "failed", "cancelled"},
        )
    raise ServiceError("unavailable", "Run view changed during reconstruction; retry")
