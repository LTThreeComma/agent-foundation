"""Ordered process-local Harness event envelopes and run-local extensions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator
from pydantic_ai.messages import AgentStreamEvent

from converge_agent_harness._json import dump_json_bytes, redact_json
from converge_agent_harness.errors import RunError
from converge_agent_harness.result import HarnessRunResult

_EXTENSION_PAYLOAD_ADAPTER = TypeAdapter(JsonValue)
_MAX_EXTENSION_PAYLOAD_BYTES = 64 * 1024
type HarnessExtensionKind = Literal[
    "context",
    "state",
    "recovery",
    "invocation",
    "delegation",
    "usage",
    "diagnostic",
]


class HarnessExtensionEvent(BaseModel):
    """One small Harness-owned observation absent from Pydantic AI's event vocabulary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(default="1", min_length=1, max_length=32)
    kind: HarnessExtensionKind
    payload: JsonValue

    @field_validator("payload", mode="before")
    @classmethod
    def _bound_payload(cls, value: Any) -> JsonValue:
        validated = _EXTENSION_PAYLOAD_ADAPTER.validate_python(value, strict=True)
        sanitized = redact_json(validated)
        if len(dump_json_bytes(sanitized)) > _MAX_EXTENSION_PAYLOAD_BYTES:
            raise ValueError("Harness extension payload is too large")
        return sanitized


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    """A public event correlated to one Harness run."""

    run_id: str
    sequence: int
    occurred_at: datetime
    event: AgentStreamEvent | HarnessExtensionEvent


@dataclass(frozen=True, slots=True)
class HarnessRunResultEvent[OutputT]:
    """The sole terminal stream item, emitted only after teardown succeeds."""

    run_id: str
    sequence: int
    occurred_at: datetime
    result: HarnessRunResult[OutputT]


type HarnessStreamItem[OutputT] = HarnessEvent | HarnessRunResultEvent[OutputT]


@runtime_checkable
class HarnessEventEmitter(Protocol):
    """Run-local path into the canonical single-consumer Harness stream."""

    async def emit(self, event: HarnessExtensionEvent) -> None: ...

    async def forward_child(self, event: HarnessEvent) -> None: ...


class _RunEventEmitter:
    """Bounded queue consumed concurrently with the active Pydantic event iterator."""

    def __init__(self, run_id: str, *, capacity: int = 64) -> None:
        self.run_id = run_id
        self._queue: asyncio.Queue[HarnessExtensionEvent | HarnessEvent] = asyncio.Queue(maxsize=capacity)
        self._child_run_ids: set[str] = set()
        self._consumer_started = False
        self._producer_stopped = asyncio.Event()
        self._producer_stopped.set()
        self._closed = False

    async def emit(self, event: HarnessExtensionEvent) -> None:
        if self._closed:
            raise RunError("The Harness event emitter is closed.", code="event_emitter_closed")
        if not isinstance(event, HarnessExtensionEvent):
            raise RunError("Harness extension event is invalid.", code="event_invalid")
        try:
            validated = HarnessExtensionEvent.model_validate(event.model_dump(), strict=True)
        except ValueError as exc:
            raise RunError("Harness extension event is invalid.", code="event_invalid") from exc
        await self._put(validated)

    async def forward_child(self, event: HarnessEvent) -> None:
        if self._closed:
            raise RunError("The Harness event emitter is closed.", code="event_emitter_closed")
        if not isinstance(event, HarnessEvent) or event.run_id not in self._child_run_ids or event.sequence < 0:
            raise RunError("Forwarded child event is invalid.", code="child_event_invalid")
        await self._put(event)

    def register_child_run(self, run_id: str) -> None:
        """Register one active inline-child run before its events can be forwarded."""
        if self._closed or not run_id.strip() or run_id == self.run_id:
            raise RunError("Inline child run correlation is invalid.", code="child_event_invalid")
        self._child_run_ids.add(run_id)

    def is_registered_child(self, run_id: str) -> bool:
        return run_id in self._child_run_ids

    def start_consuming(self) -> None:
        if self._closed:
            raise RunError("The Harness event emitter is closed.", code="event_emitter_closed")
        self._consumer_started = True
        self._producer_stopped.clear()

    def stop_consuming(self) -> None:
        self._consumer_started = False
        self._producer_stopped.set()

    async def _put(self, event: HarnessExtensionEvent | HarnessEvent) -> None:
        if self._consumer_started:
            put = asyncio.create_task(self._queue.put(event))
            stopped = asyncio.create_task(self._producer_stopped.wait())
            try:
                done, _ = await asyncio.wait({put, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if put in done:
                    put.result()
                    return
                raise RunError(
                    "The Harness event consumer stopped before accepting an event.",
                    code="event_emitter_closed" if self._closed else "event_consumer_stopped",
                )
            finally:
                for task in (put, stopped):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(put, stopped, return_exceptions=True)
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull as exc:
            raise RunError("The Harness event buffer is full.", code="event_buffer_full") from exc

    async def next(self) -> HarnessExtensionEvent | HarnessEvent:
        return await self._queue.get()

    def get_nowait(self) -> HarnessExtensionEvent | HarnessEvent:
        return self._queue.get_nowait()

    def empty(self) -> bool:
        return self._queue.empty()

    def close(self) -> None:
        self._closed = True
        self._consumer_started = False
        self._producer_stopped.set()

    def envelope(self, event: HarnessExtensionEvent, *, sequence: int) -> HarnessEvent:
        return HarnessEvent(
            run_id=self.run_id,
            sequence=sequence,
            occurred_at=datetime.now(UTC),
            event=event,
        )
