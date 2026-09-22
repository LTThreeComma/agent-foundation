"""One bounded publisher: display first, checkpoint CAS second, fenced receipts last."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from a13n_harness import AgentContext, HarnessEvent, HarnessExtensionEvent, HarnessState, HarnessStreamEvent
from a13n_stream_protocol import HarnessAguiObserver
from pydantic import JsonValue

from a13n_service.infra.db import Storage
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.local import LocalObjects, StoredObject
from a13n_service.runs import attempts, inputs, snapshots
from a13n_service.runs.display import Display, Fold, Segment
from a13n_service.runs.schemas import AgentSelection, AttemptClaim, Checkpoint, DisplayCut, RunOptions, SnapshotRef


@dataclass
class _Publication:
    display: Display
    state: HarnessState | None
    receipts: tuple[str, ...]
    candidate: Literal["completed", "waiting"] | None
    output: str | None
    result: asyncio.Future[None]


class Publisher:
    def __init__(
        self,
        storage: Storage,
        objects: LocalObjects,
        claim: AttemptClaim,
        selection: AgentSelection,
        options: RunOptions,
        *,
        max_bytes: int,
        max_events: int,
        timeout: float,
    ):
        self.storage, self.objects, self.claim, self.selection = storage, objects, claim, selection
        self.max_bytes, self.max_events, self.timeout = max_bytes, max_events, timeout
        self.options_digest = hashlib.sha256(
            json.dumps(options.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.state_key = snapshots.object_key(claim.organization_id, claim.run_id, "state")
        self.display_key = snapshots.object_key(claim.organization_id, claim.run_id, "display")
        self.state_object: StoredObject | None = None
        self.display_object: StoredObject | None = None
        self.checkpoint: Checkpoint | None = None
        self.fold = Fold(
            Display(organization_id=claim.organization_id, run_id=claim.run_id, attempt_id=claim.attempt_id),
            max_bytes=max_bytes,
            max_events=max_events,
        )
        self._queue: asyncio.Queue[_Publication] = asyncio.Queue(maxsize=4)
        self._barriers: dict[str, tuple[str, asyncio.Future[None]]] = {}
        self._barrier_slots = asyncio.Semaphore(4)
        self._source_events = 0
        self._task: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None
        self._quiescent = False
        self._observer = HarnessAguiObserver()

    def _checkpoint(self, stored: StoredObject) -> Checkpoint:
        value = Checkpoint.model_validate_json(stored.content)
        if (
            value.organization_id != self.claim.organization_id
            or value.run_id != self.claim.run_id
            or value.agent_revision_id != self.selection.revision_id
            or value.agent_digest != self.selection.digest
            or value.options_digest != self.options_digest
            or stored.writer is None
            or value.attempt_number > stored.writer
            or stored.writer > self.claim.number
        ):
            raise ServiceError("conflict", "Checkpoint identity or writer is invalid")
        return value

    def _display(self, stored: StoredObject) -> Display:
        value = Display.model_validate_json(stored.content)
        if (
            value.organization_id != self.claim.organization_id
            or value.run_id != self.claim.run_id
            or stored.writer is None
            or stored.writer > self.claim.number
        ):
            raise ServiceError("conflict", "Display identity or writer is invalid")
        return value

    async def initialize(self, previous_state: HarnessState) -> None:
        await attempts.check(self.storage, self.claim)
        async with asyncio.timeout(self.timeout):
            state = await self.objects.read(self.state_key)
            display = await self.objects.read(self.display_key)
        checkpoint = self._checkpoint(state) if state is not None else None
        view = self._display(display) if display is not None else self.fold.display
        if checkpoint is not None and (display is None or view.sequence < checkpoint.display_cut.sequence):
            raise ServiceError("conflict", "Checkpoint refers to an unavailable display cut")
        if checkpoint is None and any(segment.event_sequence for segment in view.segments):
            raise ServiceError("unavailable", "Execution checkpoint is missing for an observed run")
        # Validate both objects before either writer claim. CAS refuses any intervening publication.
        for kind, stored in (("display", display), ("state", state)):
            if stored is None:
                continue
            claimed = await snapshots.replace(
                self.objects,
                stored.key,
                stored.content,
                expected=stored.version,
                writer=self.claim.number,
                timeout=self.timeout,
            )
            if kind == "display":
                self.display_object = claimed
            else:
                self.state_object = claimed
        await attempts.check(self.storage, self.claim)
        self.checkpoint = checkpoint
        self.fold = Fold(view, max_bytes=self.max_bytes, max_events=self.max_events)
        if checkpoint is not None:
            await inputs.confirm(self.storage, self.claim, checkpoint)
        if checkpoint is None or checkpoint.candidate is None:
            if view.segments and view.segments[-1].attempt_id != self.claim.attempt_id:
                view.segments[-1].interrupted = True
                view.segments[-1].execution_cut = checkpoint.display_cut if checkpoint else None
            if not view.segments or view.segments[-1].attempt_id != self.claim.attempt_id:
                view.segments.append(Segment(attempt_id=self.claim.attempt_id, attempt_number=self.claim.number))
            view.attempt_id = self.claim.attempt_id
        self._task = asyncio.create_task(self._run(), name=f"publish-{self.claim.attempt_id}")
        if checkpoint is None:
            await self._enqueue(previous_state, (), None, None)

    def observe(self, item: HarnessStreamEvent) -> tuple[dict[str, JsonValue], ...]:
        self._source_events += 1
        if self._source_events > self.max_events:
            raise ServiceError("payload_too_large", "Run event count exceeds its limit")
        if isinstance(item, HarnessEvent) and self.observe_barrier(item):
            return ()
        values = tuple(
            event.model_dump(mode="json", by_alias=True, exclude_none=True) for event in self._observer.observe(item)
        )
        for value in values:
            self.fold.add(value)
        return values

    def observe_barrier(self, item: HarnessEvent) -> bool:
        event = item.event
        if (
            not isinstance(event, HarnessExtensionEvent)
            or event.kind != "diagnostic"
            or not isinstance(event.payload, dict)
        ):
            return False
        if event.payload.get("type") != "a13n.service.display_barrier":
            return False
        identity = event.payload.get("id")
        pending = self._barriers.get(identity) if isinstance(identity, str) else None
        if pending is not None and item.run_id == pending[0] and not pending[1].done():
            pending[1].set_result(None)
        return True

    async def publish(self, state: HarnessState, receipts: tuple[str, ...], context: AgentContext) -> None:
        """The stream consumer acknowledges all earlier observations before this cut."""
        async with asyncio.timeout(self.timeout * 6), self._barrier_slots:
            identity = uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self._barriers[identity] = (context.run_id, future)
            try:
                async with asyncio.timeout(self.timeout):
                    await context.events.emit(
                        HarnessExtensionEvent(
                            kind="diagnostic",
                            payload={
                                "type": "a13n.service.display_barrier",
                                "id": identity,
                            },
                        )
                    )
                    await future
                await self._enqueue(state, receipts, None, None)
            finally:
                self._barriers.pop(identity, None)

    async def flush_display(self) -> None:
        await self._enqueue(None, (), None, None)

    async def finalize(self, state: HarnessState, receipts: tuple[str, ...], *, output: str) -> None:
        await self._enqueue(state, receipts, "completed", output)
        self._quiescent = True
        await self._queue.join()

    async def _enqueue(
        self,
        state: HarnessState | None,
        receipts: tuple[str, ...],
        candidate: Literal["completed", "waiting"] | None,
        output: str | None,
    ) -> None:
        if self._quiescent or self._failure is not None or self._task is None or self._task.done():
            raise ServiceError("unavailable", "Run publisher is stopped") from self._failure
        future = asyncio.get_running_loop().create_future()
        command = _Publication(
            self.fold.display.model_copy(deep=True),
            state.model_copy(deep=True) if state is not None else None,
            receipts,
            candidate,
            output,
            future,
        )
        async with asyncio.timeout(self.timeout * 4):
            await self._queue.put(command)
            await future

    async def _run(self) -> None:
        while True:
            command = await self._queue.get()
            try:
                try:
                    await self._store(command)
                except BaseException as error:
                    self._failure = error
                    if not command.result.done():
                        command.result.set_exception(error)
                    while not self._queue.empty():
                        waiting = self._queue.get_nowait()
                        if not waiting.result.done():
                            waiting.result.set_exception(ServiceError("unavailable", "Run publisher failed"))
                        self._queue.task_done()
                    return
                if not command.result.done():
                    command.result.set_result(None)
            finally:
                self._queue.task_done()

    async def _store(self, command: _Publication) -> None:
        await attempts.check(self.storage, self.claim)
        previous_display = self._display(self.display_object) if self.display_object else None
        command.display.sequence = previous_display.sequence + 1 if previous_display else 1
        display_bytes = command.display.model_dump_json().encode()
        if len(display_bytes) > self.max_bytes:
            raise ServiceError("payload_too_large", "Durable display exceeds its byte limit")
        self.display_object = await snapshots.replace(
            self.objects,
            self.display_key,
            display_bytes,
            expected=self.display_object.version if self.display_object else None,
            writer=self.claim.number,
            timeout=self.timeout,
        )
        self.fold.display.sequence = command.display.sequence
        await attempts.check(self.storage, self.claim)
        if command.state is None:
            return
        segment = command.display.segments[-1]
        checkpoint = Checkpoint(
            organization_id=self.claim.organization_id,
            run_id=self.claim.run_id,
            attempt_id=self.claim.attempt_id,
            attempt_number=self.claim.number,
            sequence=self.checkpoint.sequence + 1 if self.checkpoint else 1,
            agent_revision_id=self.selection.revision_id,
            agent_digest=self.selection.digest,
            options_digest=self.options_digest,
            state=command.state,
            receipts=command.receipts,
            display_cut=DisplayCut(
                sequence=command.display.sequence, attempt_id=segment.attempt_id, event_sequence=segment.event_sequence
            ),
            candidate=command.candidate,
            output=command.output,
        )
        if self.checkpoint is not None and not set(self.checkpoint.receipts).issubset(checkpoint.receipts):
            raise ServiceError("conflict", "Checkpoint lost prior input receipts")
        state_bytes = checkpoint.model_dump_json().encode()
        if len(state_bytes) > self.max_bytes:
            raise ServiceError("payload_too_large", "Execution checkpoint exceeds its byte limit")
        self.state_object = await snapshots.replace(
            self.objects,
            self.state_key,
            state_bytes,
            expected=self.state_object.version if self.state_object else None,
            writer=self.claim.number,
            timeout=self.timeout,
        )
        self.checkpoint = checkpoint
        self.fold.display.execution_checkpoint = checkpoint.sequence
        await inputs.confirm(self.storage, self.claim, checkpoint)

    def selected(self) -> tuple[Checkpoint, SnapshotRef, SnapshotRef]:
        if self.checkpoint is None or self.state_object is None or self.display_object is None:
            raise ServiceError("conflict", "Run has no durable snapshots")
        display = self._display(self.display_object)
        return (
            self.checkpoint,
            snapshots.reference(
                self.state_object,
                format=self.checkpoint.format,
                sequence=self.checkpoint.sequence,
                attempt_id=self.checkpoint.attempt_id,
            ),
            snapshots.reference(
                self.display_object, format=display.format, sequence=display.sequence, attempt_id=display.attempt_id
            ),
        )

    async def close(self) -> None:
        self._quiescent = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        for _, future in self._barriers.values():
            if not future.done():
                future.cancel()
