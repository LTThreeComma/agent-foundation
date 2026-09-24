"""Exact source positions survive projection, policy, replay and framing."""

from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessBuilder, HarnessEvent
from a13n_harness.model_context import ModelInputEvent
from a13n_stream_protocol import (
    AguiObservationError,
    CustomEventAssembler,
    HarnessAguiObserver,
    InputSource,
    input_source,
)
from ag_ui.core.events import CustomEvent
from pydantic import ValidationError
from pydantic_ai import RunContext, Tool
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    BinaryContent,
    CachePoint,
    EnqueuedMessagesEvent,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextContent,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel


def source(event):
    return HarnessEvent(
        thread_id="thread", run_id="run", sequence=0, occurred_at=datetime(2026, 1, 1, tzinfo=UTC), event=event
    )


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "other", "content_index": 0},
        {"kind": "model_input", "content_index": -1},
        {"kind": "model_input", "content_index": True},
        {"kind": "model_input", "content_index": 1.5},
        {"kind": "model_input", "content_index": float("inf")},
        {"kind": "model_input", "content_index": "1"},
        {"kind": "model_input", "content_index": 0, "part_index": 0},
        {"kind": "enqueued_messages", "content_index": 0, "message_index": 0},
    ],
)
def test_coordinate_rejects_invalid_positions(value):
    with pytest.raises(ValidationError):
        InputSource.model_validate(value)


def mixed_source():
    return source(
        EnqueuedMessagesEvent(
            enqueue_id="mixed",
            messages=(
                ModelResponse(parts=[TextPart("previous response")]),
                ModelRequest(
                    parts=[
                        SystemPromptPart("system"),
                        UserPromptPart(
                            [
                                CachePoint(),
                                TextContent(""),
                                TextContent("text" * 5000, metadata={"input_source": {"kind": "forged"}}),
                                BinaryContent(data=b"owned", media_type="image/png"),
                            ]
                        ),
                        UserPromptPart("plain"),
                    ]
                ),
                ModelRequest(parts=[UserPromptPart([BinaryContent(data=b"other", media_type="image/png")])]),
            ),
        )
    )


def test_original_positions_text_chunks_and_metadata_spoofing():
    events = HarnessAguiObserver().observe(mixed_source())
    assert input_source(events[0]) is None
    coordinates = [input_source(event) for event in events[1:]]
    assert (
        coordinates[:2] == [InputSource(kind="enqueued_messages", message_index=1, part_index=1, content_index=1)] * 2
    )
    assert (
        coordinates[2:7] == [InputSource(kind="enqueued_messages", message_index=1, part_index=1, content_index=2)] * 5
    )
    media = [event for event in events if isinstance(event, CustomEvent) and event.name == "a13n.input.media"]
    assert [input_source(event) for event in media] == [
        InputSource(kind="enqueued_messages", message_index=1, part_index=1, content_index=3),
        InputSource(kind="enqueued_messages", message_index=2, part_index=0, content_index=0),
    ]
    assert (
        coordinates[-4:-1]
        == [InputSource(kind="enqueued_messages", message_index=1, part_index=2, content_index=0)] * 3
    )
    assert "forged" in events[3].model_dump_json()
    model = HarnessAguiObserver().observe(source(ModelInputEvent(content=[CachePoint(), "hello"])))
    assert [input_source(event) for event in model] == [InputSource(kind="model_input", content_index=1)] * 3


@pytest.mark.parametrize("change", ["remove", "alter", "insert"])
def test_processor_cannot_change_coordinate(change):
    def processor(item, event):
        if change == "insert":
            if input_source(event) is None:
                return event.model_copy(update={"input_source": {"kind": "model_input", "content_index": 0}})
        elif input_source(event) is not None:
            if change == "remove":
                event.__pydantic_extra__.pop("input_source")
                return event
            return event.model_copy(update={"input_source": {"kind": "model_input", "content_index": 9}})
        return event

    with pytest.raises(AguiObservationError, match="input_source"):
        HarnessAguiObserver(processor=processor).observe(mixed_source())


@pytest.mark.anyio
async def test_resume_and_fragmentation_preserve_coordinate():
    item = source(
        ModelInputEvent(
            content=[BinaryContent(data=b"binary", media_type="image/png", vendor_metadata={"label": "large" * 15000})]
        )
    )
    original = HarnessAguiObserver()
    frames = original.observe(item)
    assert len(frames) > 1
    assembler = CustomEventAssembler()
    restored = None
    for frame in frames:
        restored = assembler.accept(frame.model_dump(mode="json"))
    assert restored["input_source"] == {"kind": "model_input", "content_index": 0}

    async def history():
        yield item

    resumed = HarnessAguiObserver()
    await resumed.resume(history())
    assert resumed.snapshot() == original.snapshot()


@pytest.mark.anyio
async def test_real_native_mixed_enqueue_selects_exact_equal_size_media():
    async def model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        ):
            yield "complete"
        else:
            yield {0: DeltaToolCall(name="enqueue_groups", json_args="{}", tool_call_id="enqueue")}

    async def enqueue_groups(ctx: RunContext):
        ctx.enqueue(
            ModelRequest(
                parts=[UserPromptPart([TextContent(""), BinaryContent(data=b"owned", media_type="image/png")])]
            ),
            ModelRequest(
                parts=[UserPromptPart([TextContent("unowned"), BinaryContent(data=b"other", media_type="image/png")])]
            ),
            priority="asap",
        )
        return "queued"

    selected = []
    diagnostics = []

    def processor(item, event):
        coordinate = input_source(event)
        if isinstance(item, HarnessEvent) and isinstance(item.event, EnqueuedMessagesEvent):
            if coordinate is None:
                diagnostics.append(event)
            else:
                native = (
                    item.event.messages[coordinate.message_index]
                    .parts[coordinate.part_index]
                    .content[coordinate.content_index]
                )
                selected.append(native)
                if coordinate.message_index == 0:
                    return None
        return event

    observer = HarnessAguiObserver(processor=processor)
    agent = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(Capability(tools=[Tool(enqueue_groups)]),),
    )
    async with agent.stream("start") as stream:
        async for item in stream:
            observer.observe(item)
    assert [native.data for native in selected if isinstance(native, BinaryContent)] == [b"owned", b"other"]
    media = [
        event for event in observer.snapshot() if isinstance(event, CustomEvent) and event.name == "a13n.input.media"
    ]
    assert len(media) == 1 and input_source(media[0]).message_index == 1
    assert len(diagnostics) == 1 and diagnostics[0].name == "a13n.pydantic_ai.enqueued_messages"


def test_input_coordinate_wire_fixture():
    import json
    from pathlib import Path

    item = source(ModelInputEvent(content=["hello", BinaryContent(data=b"bytes", media_type="image/png")]))
    actual = [
        event.model_dump(mode="json", by_alias=True, exclude_none=True) for event in HarnessAguiObserver().observe(item)
    ]
    expected = json.loads((Path(__file__).parent / "fixtures/input-source.json").read_text())
    assert actual == expected
