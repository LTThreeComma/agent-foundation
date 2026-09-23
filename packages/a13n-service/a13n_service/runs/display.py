"""Bounded event-folded observations, retained independently of execution history."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.infra.errors import ServiceError
from a13n_service.runs.schemas import DisplayCut


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: str
    attempt_number: int
    interrupted: bool = False
    execution_cut: DisplayCut | None = None
    items: list[dict[str, JsonValue]] = Field(default_factory=list)
    event_sequence: int = 0


class Display(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["a13n.service.display.v1"] = "a13n.service.display.v1"
    organization_id: str
    run_id: str
    attempt_id: str
    sequence: int = 0
    execution_checkpoint: int = 0
    segments: list[Segment] = Field(default_factory=list, max_length=20)


class Fold:
    def __init__(self, display: Display, *, max_bytes: int, max_events: int):
        self.display = display
        self.max_bytes = max_bytes
        self.max_events = max_events
        self._indices: dict[tuple[str, str], int] = {}
        self._observed_bytes = len(display.model_dump_json().encode())
        self._observed_events = 0

    def add(self, event: dict[str, JsonValue]) -> None:
        import json

        self._observed_bytes += len(json.dumps(event, ensure_ascii=False).encode()) + 64
        self._observed_events += 1
        if self._observed_bytes > self.max_bytes or self._observed_events > self.max_events:
            raise ServiceError("payload_too_large", "Run observations exceed their configured limit")
        segment = self.display.segments[-1]
        segment.event_sequence += 1
        metadata = event.get("metadata")
        # Presentation hints do not establish Service input ownership or receipts.
        if isinstance(metadata, dict) and metadata.get("display") is False:
            return
        kind = event.get("type")
        message_id = event.get("messageId")
        if kind in {"TEXT_MESSAGE_START", "REASONING_MESSAGE_START"} and isinstance(message_id, str):
            category = "reasoning" if kind == "REASONING_MESSAGE_START" else "text"
            self._indices[(category, message_id)] = len(segment.items)
            segment.items.append(
                {"type": category, "id": message_id, "role": event.get("role"), "text": "", "complete": False}
            )
        elif kind in {
            "TEXT_MESSAGE_CONTENT",
            "REASONING_MESSAGE_CONTENT",
            "TEXT_MESSAGE_END",
            "REASONING_MESSAGE_END",
        } and isinstance(message_id, str):
            category = "reasoning" if str(kind).startswith("REASONING") else "text"
            index = self._indices.get((category, message_id))
            if index is None:
                raise ServiceError("conflict", "Display content has no message start")
            item = segment.items[index]
            if str(kind).endswith("CONTENT"):
                delta = event.get("delta")
                if not isinstance(delta, str):
                    raise ServiceError("conflict", "Display text delta is invalid")
                item["text"] = str(item["text"]) + delta
            else:
                item["complete"] = True
        else:
            segment.items.append({"type": "event", "event": event})
