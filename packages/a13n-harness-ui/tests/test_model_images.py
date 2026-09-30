from __future__ import annotations

import random
from collections.abc import AsyncIterator
from io import BytesIO

import pytest
from a13n_harness_ui.model_images import (
    MAX_PREVIEW_BYTES,
    MAX_PREVIEW_EDGE,
    MAX_REQUEST_IMAGE_BYTES,
    ImagePreviewModel,
    _preview,
    prepare_image_previews,
)
from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextContent,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def image_data() -> bytes:
    pixels = random.Random(1).randbytes(1440 * 1080 * 3)
    image = Image.frombytes("RGB", (1440, 1080), pixels)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("tool_result", [False, True])
def test_previews_preserve_original_history_and_native_content(image_data: bytes, tool_result: bool) -> None:
    image = BinaryContent(data=image_data, media_type="image/png", vendor_metadata={"display": False})
    part = ToolReturnPart("view", [image], "view-1") if tool_result else UserPromptPart(content=["inspect", image])
    history: list[ModelMessage] = [ModelRequest(parts=[part])]
    prepared = prepare_image_previews(history)
    assert prepared is not history
    assert image.data == image_data
    assert image.media_type == "image/png"
    assert image.vendor_metadata == {"display": False}
    content = prepared[0].parts[0].content
    previews = [item for item in content if isinstance(item, BinaryContent)]
    assert len(previews) == 1
    preview = previews[0]
    assert preview.media_type == "image/jpeg" and len(preview.data) <= MAX_PREVIEW_BYTES
    assert preview.identifier == image.identifier
    with Image.open(BytesIO(preview.data)) as decoded:
        assert max(decoded.size) <= MAX_PREVIEW_EDGE
        assert decoded.getextrema() != ((0, 0), (0, 0), (0, 0))
    note = next(item for item in content if isinstance(item, TextContent))
    assert note.metadata == {"display": False, "source_id": "a13n.harness-ui.image-preview"}


def test_total_budget_applies_across_multiple_images(image_data: bytes) -> None:
    images = [BinaryContent(data=image_data, media_type="image/png") for _ in range(5)]
    prepared = prepare_image_previews([ModelRequest(parts=[UserPromptPart(content=images)])])
    content = prepared[0].parts[0].content
    previews = [item for item in content if isinstance(item, BinaryContent)]
    assert len(previews) == len(images)
    assert sum(len(image.data) for image in previews) <= MAX_REQUEST_IMAGE_BYTES
    assert all(image.data == image_data for image in images)


def test_small_and_non_image_inputs_are_unchanged() -> None:
    image = BinaryContent(data=b"small-image", media_type="image/png")
    audio = BinaryContent(data=b"audio" * MAX_PREVIEW_BYTES, media_type="audio/wav")
    history = [ModelRequest(parts=[UserPromptPart(content=["inspect", image, audio])])]
    assert prepare_image_previews(history) is history


def test_corrupt_image_fails_without_modifying_the_original() -> None:
    image = BinaryContent(data=b"invalid" * MAX_PREVIEW_BYTES, media_type="image/png")
    history = [ModelRequest(parts=[UserPromptPart(content=[image])])]
    with pytest.raises(ValueError, match="Cannot prepare a preview"):
        prepare_image_previews(history)
    assert history[0].parts[0].content == [image]


def test_transparency_is_composited_without_exposing_hidden_rgb() -> None:
    image = Image.new("RGBA", (10, 10), (255, 0, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    data, width, height = _preview(buffer.getvalue(), 4096)
    with Image.open(BytesIO(data)) as preview:
        assert preview.getpixel((5, 5)) == (255, 255, 255)
    assert (width, height) == (10, 10)


@pytest.mark.parametrize("streaming", [False, True])
async def test_streaming_and_non_streaming_requests_keep_usable_image_content(
    image_data: bytes,
    streaming: bool,
) -> None:
    seen: list[BinaryContent] = []

    def observe(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        content = [
            item
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            for item in (part.content if isinstance(part.content, list) else [part.content])
        ]
        images = [item for item in content if isinstance(item, BinaryContent)]
        assert images and images[0].media_type == "image/jpeg"
        assert len(images[0].data) <= MAX_PREVIEW_BYTES
        seen.extend(images)
        return ModelResponse(parts=[TextPart("Image inspected")])

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        observe(messages, info)
        yield "Image inspected"

    agent = Agent(ImagePreviewModel(FunctionModel(observe, stream_function=stream)))
    image = BinaryContent(data=image_data, media_type="image/png")
    if streaming:
        async with agent.run_stream(["inspect", image]) as result:
            assert await result.get_output() == "Image inspected"
        history = result.all_messages()
    else:
        result = await agent.run(["inspect", image])
        assert result.output == "Image inspected"
        history = result.all_messages()
    originals = [
        item
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        for item in (part.content if isinstance(part.content, list) else [part.content])
        if isinstance(item, BinaryContent)
    ]
    assert originals[0].data == image_data
    assert len(seen) == 1
