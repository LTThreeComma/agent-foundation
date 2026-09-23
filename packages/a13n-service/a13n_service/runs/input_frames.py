"""Attempt-local expectations for complete native Service input frames."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from a13n_harness.model_context import ModelInputEvent, user_prompt_content
from a13n_stream_protocol import InputSource
from pydantic_ai.messages import (
    BinaryContent,
    EnqueuedMessagesEvent,
    ModelMessage,
    ModelRequest,
    TextContent,
    UserContent,
    UserPromptPart,
)

from a13n_service.infra.errors import ServiceError

INPUT_PROVENANCE = "a13n.service.input"
_FORMAT = "a13n.service.input.v1"
_DOMAIN = b"a13n.service.input.payload.v1\0"
MAX_NATIVE_ITEMS = 32
MAX_NATIVE_PAYLOAD_BYTES = 16 * 1024 * 1024
type PreparedContent = TextContent | BinaryContent


@dataclass(frozen=True)
class Frame:
    entry_id: str
    count: int
    digest: str


def _json(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise ServiceError("conflict", "Prepared input contains noncanonical metadata") from error


def payload_digest(items: Sequence[UserContent]) -> str:
    """Hash ordered, complete native fields; unsupported content fails closed."""
    if not 1 <= len(items) <= MAX_NATIVE_ITEMS:
        raise ServiceError("payload_too_large", "Prepared input item count exceeds its limit")
    digest = hashlib.sha256(_DOMAIN)
    size = 0
    for item in items:
        if isinstance(item, TextContent):
            fields = (b"text", item.content.encode(), _json(item.metadata))
        elif isinstance(item, BinaryContent):
            fields = (
                b"binary",
                str(item.media_type).encode(),
                item.identifier.encode(),
                _json(item.vendor_metadata),
                item.data,
            )
        else:
            raise ServiceError("conflict", "Prepared input contains an unsupported native item")
        for value in fields:
            size += len(value)
            if size > MAX_NATIVE_PAYLOAD_BYTES:
                raise ServiceError("payload_too_large", "Prepared input exceeds its native byte limit")
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.hexdigest()


def _marker(item: UserContent) -> dict | None:
    if not isinstance(item, TextContent) or not isinstance(item.metadata, dict):
        return None
    marker = item.metadata.get(INPUT_PROVENANCE)
    return marker if isinstance(marker, dict) else None


def prepared_marker(run_id: str, entry_id: str, count: int, digest: str) -> TextContent:
    return TextContent(
        "",
        metadata={
            INPUT_PROVENANCE: {
                "format": _FORMAT,
                "run_id": run_id,
                "entry_id": entry_id,
                "count": count,
                "digest": digest,
            }
        },
    )


class PreparedInputs:
    """Only this worker's prepared entries may become new durable receipts."""

    def __init__(self, run_id: str, *, receipts: tuple[str, ...] = ()):
        self.run_id = run_id
        self.receipts = frozenset(receipts)
        self.expected: dict[str, Frame] = {}

    def offer(self, entry_id: str, payload: Sequence[PreparedContent]) -> list[PreparedContent]:
        if entry_id in self.expected or entry_id in self.receipts:
            raise ServiceError("conflict", "Prepared input was offered twice")
        items = list(payload)
        frame = Frame(entry_id, len(items), payload_digest(items))
        self.expected[entry_id] = frame
        marker = prepared_marker(self.run_id, entry_id, frame.count, frame.digest)
        return [marker, *items]

    def confirm(self, receipts: Sequence[str]) -> None:
        """Advance the attempt's skip set only after durable publication succeeds."""
        current = frozenset(receipts)
        if not self.receipts.issubset(current):
            raise ServiceError("conflict", "Prepared input lost a confirmed receipt")
        self.receipts = current

    def restore(self, history: Sequence[ModelMessage]) -> None:
        """Recover only previously receipted frame expectations from saved bytes."""
        found: list[str] = []
        for message in history:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, UserPromptPart) or isinstance(part.content, str):
                    continue
                for index, item in enumerate(part.content):
                    header = _marker(item)
                    if header is None or header.get("run_id") != self.run_id:
                        continue
                    entry_id = header.get("entry_id")
                    if entry_id not in self.receipts:
                        continue
                    count = header.get("count")
                    if (
                        not isinstance(entry_id, str)
                        or type(count) is not int
                        or count < 1
                        or count > MAX_NATIVE_ITEMS
                        or index + count >= len(part.content)
                        or not isinstance(header.get("digest"), str)
                    ):
                        raise ServiceError("conflict", "Saved input frame header is invalid")
                    frame = Frame(entry_id, count, header["digest"])
                    if entry_id in self.expected and self.expected[entry_id] != frame:
                        raise ServiceError("conflict", "Saved input frame identity changed")
                    self.expected[entry_id] = frame
                _, entries = self._scan(part.content)
                found.extend(entries)
        if len(found) != len(set(found)):
            raise ServiceError("conflict", "Saved input frame was duplicated")

    def _scan(
        self, items: Sequence[UserContent], *, confirmed: frozenset[str] = frozenset()
    ) -> tuple[set[int], list[str]]:
        owned: set[int] = set()
        found: list[str] = []
        index = 0
        while index < len(items):
            current = items[index]
            header = _marker(current)
            if header is None or header.get("run_id") != self.run_id:
                index += 1
                continue
            entry_id = header.get("entry_id")
            expected = self.expected.get(entry_id) if isinstance(entry_id, str) else None
            if not isinstance(entry_id, str) or expected is None:
                # Current-run metadata cannot authorize an entry by asserting its own digest.
                if entry_id not in self.receipts:
                    raise ServiceError("conflict", "Native input frame was not offered by this worker")
                index += 1
                continue
            count = header.get("count")
            if (
                header.get("format") != _FORMAT
                or type(count) is not int
                or count != expected.count
                or header.get("digest") != expected.digest
                or set(header) != {"format", "run_id", "entry_id", "count", "digest"}
                or not isinstance(current, TextContent)
                or current.content != ""
                or not isinstance(current.metadata, dict)
                or set(current.metadata) != {INPUT_PROVENANCE}
                or index + count >= len(items)
            ):
                raise ServiceError("conflict", "Native input frame header is invalid")
            payload = items[index + 1 : index + count + 1]
            if any(_marker(item) is not None or not isinstance(item, TextContent | BinaryContent) for item in payload):
                raise ServiceError("conflict", "Native input frame payload changed")
            # Confirmed receipts need no new payload proof at every checkpoint.
            # Recovery and each native visibility source still verify full bytes.
            if entry_id not in confirmed and payload_digest(payload) != expected.digest:
                raise ServiceError("conflict", "Native input frame payload changed")
            if entry_id in found:
                raise ServiceError("conflict", "Native input frame was duplicated")
            found.append(entry_id)
            owned.update(range(index, index + count + 1))
            index += count + 1
        return owned, found

    def incorporated(self, history: Sequence[ModelMessage]) -> tuple[str, ...]:
        """Inspect one detached exported history, without trusting live message aliases."""
        found: list[str] = []
        for message in history:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                    _, entries = self._scan(part.content, confirmed=self.receipts)
                    found.extend(entries)
        if len(found) != len(set(found)):
            raise ServiceError("conflict", "Native input frame was duplicated")
        return tuple(found)

    def owned_coordinates(self, source: object) -> set[InputSource]:
        """Locate complete frames once per native source item, before observation."""
        owned: set[InputSource] = set()
        if isinstance(source, ModelInputEvent):
            indices, _ = self._scan(source.content)
            owned.update(InputSource(kind="model_input", content_index=index) for index in indices)
        elif isinstance(source, EnqueuedMessagesEvent):
            found: list[str] = []
            for message_index, message in enumerate(source.messages):
                if not isinstance(message, ModelRequest):
                    continue
                for part_index, part in enumerate(message.parts):
                    if not isinstance(part, UserPromptPart):
                        continue
                    indices, entries = self._scan(user_prompt_content(part))
                    found.extend(entries)
                    owned.update(
                        InputSource(
                            kind="enqueued_messages",
                            message_index=message_index,
                            part_index=part_index,
                            content_index=index,
                        )
                        for index in indices
                    )
            if len(found) != len(set(found)):
                raise ServiceError("conflict", "Native input frame was duplicated")
        return owned
