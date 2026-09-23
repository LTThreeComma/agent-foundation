"""What viewers see of a run: ordered display items folded from Harness events, never from message history.

Items keep the shape the Console renders: `{id, kind, state, first_stream_id, last_stream_id, started_at, ended_at,
content}`. Stream IDs are `"{attempt}-{sequence}"` positions, so a committed item and a live delta of the same item order and deduplicate
by comparing positions. The worker folds every AG-UI event it streams, so the display written at a checkpoint
covers exactly the stream up to that checkpoint's position.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from a13n_harness import HarnessEvent, HarnessStreamEvent
from a13n_stream_protocol import HarnessAguiObserver
from a13n_stream_protocol.fragments import CustomEventAssembler
from ag_ui.core import Event, ToolCallArgsEvent, ToolCallResultEvent
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai.messages import FunctionToolResultEvent, OutputToolResultEvent, ToolReturnPart

type ItemKind = Literal["text_message", "reasoning_message", "tool_call", "observation"]
type ItemState = Literal["in_progress", "completed", "interrupted", "failed"]

# Harness observations beyond this size keep only their name: Debug evidence, not an unbounded payload copy.
MAX_OBSERVATION_BYTES = 32768
# A message text, tool arguments or tool result keeps this many characters; the full value stays in the state.
MAX_FIELD_CHARS = 262144
# A display keeps this many items; older ones are dropped and counted, while the state keeps every message.
MAX_ITEMS = 4096

_MESSAGE_KINDS: dict[str, ItemKind] = {
    "TEXT_MESSAGE_START": "text_message",
    "TEXT_MESSAGE_CONTENT": "text_message",
    "TEXT_MESSAGE_END": "text_message",
    "REASONING_MESSAGE_START": "reasoning_message",
    "REASONING_MESSAGE_CONTENT": "reasoning_message",
    "REASONING_MESSAGE_END": "reasoning_message",
    "REASONING_ENCRYPTED_VALUE": "reasoning_message",
}
_TOOL_EVENTS = frozenset({"TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT"})
_ENDS = frozenset({"TEXT_MESSAGE_END", "REASONING_MESSAGE_END", "TOOL_CALL_RESULT"})
_FINISHED: frozenset[ItemState] = frozenset({"completed", "failed"})
_COPIED = ("messageId", "role", "toolCallId", "toolCallName", "parentMessageId", "metadata")
_ACCUMULATED = {
    "TEXT_MESSAGE_CONTENT": "text",
    "REASONING_MESSAGE_CONTENT": "text",
    "TOOL_CALL_ARGS": "arguments",
}


class StreamPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    attempt: int = Field(ge=0)
    sequence: int = Field(ge=0)

    def __str__(self) -> str:
        return f"{self.attempt}-{self.sequence}"


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: ItemKind
    state: ItemState
    first_stream_id: str
    last_stream_id: str
    # When the item's first event occurred, and the latest event that left it completed or failed.
    started_at: datetime
    ended_at: datetime | None = None
    content: dict[str, JsonValue]


class ItemRef(BaseModel):
    """The item a live event changed, and its state after the event."""

    id: str
    kind: ItemKind
    state: ItemState


class Display(BaseModel):
    """The display of a run at one checkpoint and the stream position it covers."""

    model_config = ConfigDict(extra="forbid")
    items: list[Item] = Field(default_factory=list)
    position: StreamPosition = StreamPosition(attempt=0, sequence=0)
    # Items dropped from the front over the item limit.
    dropped: int = Field(default=0, ge=0)


class Observed(BaseModel):
    """One AG-UI event at its stream sequence, with the item it changed."""

    sequence: int
    event: dict[str, Any]
    item: ItemRef | None


_OMITTED: dict[str, JsonValue] = {"omitted": True}


def _bounded(content: dict[str, JsonValue], field: str, value: str) -> None:
    content[field] = value[:MAX_FIELD_CHARS]
    if len(value) > MAX_FIELD_CHARS:
        content["truncated"] = True


def _size(item: Item) -> int:
    return len(item.model_dump_json())


def item_id(run_id: str, kind: ItemKind, source_id: str) -> str:
    """Stable across attempts: a tool call retried after recovery updates the item already committed."""
    return "itm_" + hashlib.sha256(f"{run_id}\0{kind}\0{source_id}".encode()).hexdigest()[:32]


def _occurred(payload: dict[str, Any]) -> datetime:
    """The event's own time in epoch milliseconds, or the worker's clock for an event that carries none."""
    timestamp = payload.get("timestamp")
    return datetime.fromtimestamp(timestamp / 1000, UTC) if isinstance(timestamp, int) else datetime.now(UTC)


def _failed_tool_call(source: HarnessStreamEvent[Any]) -> tuple[str, str] | None:
    """A tool result the observer does not present (retry prompts, denials): its call ID and message."""
    if not isinstance(source, HarnessEvent):
        return None
    event = source.event
    if not isinstance(event, FunctionToolResultEvent | OutputToolResultEvent):
        return None
    part = event.part
    if isinstance(part, ToolReturnPart) and part.outcome == "success":
        return None
    return part.tool_call_id, str(part.content)[:4096]


def _bound_payloads(source: HarnessStreamEvent[Any], event: Event) -> Event:
    """Cap the payloads the observer retains for the whole attempt; the state keeps them whole.

    One character over the bound survives, so the fold still sees the value was truncated.
    """
    if isinstance(event, ToolCallResultEvent) and len(event.content) > MAX_FIELD_CHARS:
        return event.model_copy(update={"content": event.content[: MAX_FIELD_CHARS + 1]})
    if isinstance(event, ToolCallArgsEvent) and len(event.delta) > MAX_FIELD_CHARS:
        return event.model_copy(update={"delta": event.delta[: MAX_FIELD_CHARS + 1]})
    return event


class DisplayFold:
    """A run's display in memory during one attempt, continuing the committed display it restored."""

    def __init__(self, run_id: str, display: Display, *, attempt: int, max_bytes: int):
        self.run_id, self.attempt, self.max_bytes = run_id, attempt, max_bytes
        self.items = {item.id: item for item in display.items}
        self.dropped = display.dropped
        # Each item's serialized size as of the last snapshot; a snapshot measures only the items changed since.
        self.sizes: dict[str, int] = {}
        self.changed: set[str] = set(self.items)
        self.sequence = 0
        self.observer = HarnessAguiObserver(processor=_bound_payloads)
        self.assembler = CustomEventAssembler(max_bytes=max_bytes)

    @property
    def position(self) -> StreamPosition:
        return StreamPosition(attempt=self.attempt, sequence=self.sequence)

    def observe(self, source: HarnessStreamEvent[Any]) -> list[Observed]:
        observed: list[Observed] = []
        for event in self.observer.observe(source):
            self.sequence += 1
            payload = event.model_dump(mode="json", by_alias=True, exclude_none=True)
            observed.append(Observed(sequence=self.sequence, event=payload, item=self._fold(payload)))
        if observed and (failed := _failed_tool_call(source)) is not None:
            ref = self._fail_tool_call(*failed, at=_occurred(observed[-1].event))
            if ref is not None:
                observed[-1] = observed[-1].model_copy(update={"item": ref})
        return observed

    def snapshot(self) -> Display:
        """The display to commit. Over its item limit the oldest items are dropped; over its byte limit the oldest
        remaining ones give up their content.

        The display is a view, so its limits never fail the run: the state still holds every message.
        """
        for key in self.changed & self.items.keys():
            self.sizes[key] = _size(self.items[key])
        self.changed.clear()
        for key in list(self.items)[: max(0, len(self.items) - MAX_ITEMS)]:
            del self.items[key], self.sizes[key]
            self.dropped += 1
        excess = sum(self.sizes.values()) - self.max_bytes
        for key, item in self.items.items():
            if excess <= 0:
                break
            if item.content != _OMITTED:
                omitted = item.model_copy(update={"content": _OMITTED})
                excess -= self.sizes[key] - (size := _size(omitted))
                self.items[key], self.sizes[key] = omitted, size
        return Display(items=list(self.items.values()), position=self.position, dropped=self.dropped)

    def interrupt(self) -> None:
        """Unfinished items of an attempt that ends without finishing them."""
        for key, item in self.items.items():
            if item.state == "in_progress":
                self.items[key] = item.model_copy(update={"state": "interrupted"})
                self.changed.add(key)

    def _fold(self, payload: dict[str, Any]) -> ItemRef | None:
        event_type = payload["type"]
        if event_type == "CUSTOM":
            return self._observation(payload)
        if event_type in _MESSAGE_KINDS:
            kind, source_id = _MESSAGE_KINDS[event_type], payload["messageId"]
        elif event_type in _TOOL_EVENTS:
            kind, source_id = "tool_call", payload["toolCallId"]
        else:
            return None
        key = item_id(self.run_id, kind, source_id)
        previous = self.items.get(key)
        content: dict[str, JsonValue] = dict(previous.content) if previous is not None else {}
        content.update({name: payload[name] for name in _COPIED if name in payload})
        if (accumulated := _ACCUMULATED.get(event_type)) is not None:
            _bounded(content, accumulated, str(content.get(accumulated, "")) + payload["delta"])
        if event_type == "REASONING_ENCRYPTED_VALUE":
            content["encrypted_value"] = payload.get("encryptedValue")
        if event_type == "TOOL_CALL_RESULT":
            _bounded(content, "result", str(payload.get("content", "")))
        state: ItemState = "completed" if event_type in _ENDS else self._continued(previous)
        return self._put(key, kind, state, content, at=_occurred(payload))

    @staticmethod
    def _continued(previous: Item | None) -> ItemState:
        """A later event keeps a finished item's state; one an earlier attempt left interrupted resumes."""
        return "in_progress" if previous is None or previous.state == "interrupted" else previous.state

    def _observation(self, payload: dict[str, Any]) -> ItemRef | None:
        assembled = self.assembler.accept(payload)
        if assembled is None:
            return None
        value: JsonValue = assembled.get("value")  # type: ignore[assignment]
        if len(json.dumps(value, separators=(",", ":"))) > MAX_OBSERVATION_BYTES:
            value = _OMITTED
        key = item_id(self.run_id, "observation", f"{self.attempt}:{self.sequence}")
        content: dict[str, JsonValue] = {"name": str(assembled.get("name")), "value": value}
        return self._put(key, "observation", "completed", content, at=_occurred(payload))

    def _fail_tool_call(self, tool_call_id: str, message: str, *, at: datetime) -> ItemRef | None:
        key = item_id(self.run_id, "tool_call", tool_call_id)
        previous = self.items.get(key)
        if previous is None:
            return None
        content = {**previous.content, "failure": {"code": "tool_failed", "message": message}}
        return self._put(key, "tool_call", "failed", content, at=at)

    def _put(
        self,
        key: str,
        kind: ItemKind,
        state: ItemState,
        content: dict[str, JsonValue],
        *,
        at: datetime,
    ) -> ItemRef:
        position = str(self.position)
        previous = self.items.get(key)
        self.items[key] = Item(
            id=key,
            kind=kind,
            state=state,
            first_stream_id=previous.first_stream_id if previous is not None else position,
            last_stream_id=position,
            started_at=previous.started_at if previous is not None else at,
            ended_at=at if state in _FINISHED else None,
            content=content,
        )
        self.changed.add(key)
        return ItemRef(id=key, kind=kind, state=state)
