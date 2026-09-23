"""What viewers see of a run: ordered display items folded from Harness events, never from message history.

Items keep the shape the Console renders: `{id, kind, state, parent_item_id, first_stream_id, last_stream_id,
content}`. Stream IDs are `"{attempt}-{sequence}"` positions, so a committed item and a live delta of the
same item order and deduplicate by comparing positions.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

type ItemKind = Literal["text_message", "reasoning_message", "tool_call", "run_output", "observation"]
type ItemState = Literal["in_progress", "completed", "interrupted", "failed"]


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
    parent_item_id: str | None = None
    first_stream_id: str
    last_stream_id: str
    content: dict[str, JsonValue]


class Display(BaseModel):
    """The complete display of a run at one checkpoint and the stream position it covers."""

    model_config = ConfigDict(extra="forbid")
    items: list[Item] = Field(default_factory=list)
    position: StreamPosition = StreamPosition(attempt=0, sequence=0)
