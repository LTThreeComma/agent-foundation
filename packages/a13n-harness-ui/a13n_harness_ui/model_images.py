"""Bounded model-only previews, preserving original image history and files."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from io import BytesIO
from typing import Any

from anyio import to_thread
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextContent,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext

MAX_REQUEST_IMAGE_BYTES = 384 * 1024
MAX_PREVIEW_BYTES = 256 * 1024
MAX_PREVIEW_EDGE = 2048
MAX_IMAGE_PIXELS = 32_000_000


class ImagePreviewModel(WrapperModel):
    """Prepare previews on a detached request before provider serialization."""

    def __copy__(self) -> ImagePreviewModel:
        return ImagePreviewModel(self.wrapped)

    def __deepcopy__(self, memo: dict[int, Any]) -> ImagePreviewModel:
        return ImagePreviewModel(deepcopy(self.wrapped, memo))

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        prepared = await to_thread.run_sync(prepare_image_previews, messages)
        return await self.wrapped.request(prepared, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        prepared = await to_thread.run_sync(prepare_image_previews, messages)
        async with self.wrapped.request_stream(
            prepared, model_settings, model_request_parameters, run_context
        ) as stream:
            yield stream


def _images(messages: list[ModelMessage]) -> list[BinaryContent]:
    images: list[BinaryContent] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, UserPromptPart) and type(part) is not ToolReturnPart:
                continue
            content = part.content
            items = content if isinstance(content, Sequence) and not isinstance(content, str | bytes) else [content]
            images.extend(item for item in items if isinstance(item, BinaryContent) and item.is_image)
    return images


def prepare_image_previews(messages: list[ModelMessage]) -> list[ModelMessage]:
    images = _images(messages)
    if not images:
        return messages
    budget = min(MAX_PREVIEW_BYTES, MAX_REQUEST_IMAGE_BYTES // len(images))
    if all(len(image.data) <= budget for image in images):
        return messages
    prepared = deepcopy(messages)
    for message in prepared:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, UserPromptPart) and type(part) is not ToolReturnPart:
                continue
            content = part.content
            if isinstance(content, str):
                continue
            items = list(content) if isinstance(content, Sequence) and not isinstance(content, bytes) else [content]
            changed = False
            result: list[Any] = []
            for item in items:
                if not isinstance(item, BinaryContent) or not item.is_image or len(item.data) <= budget:
                    result.append(item)
                    continue
                data, width, height = _preview(item.data, budget)
                result.extend(
                    [
                        TextContent(
                            content=f"Model-only JPEG preview ({width} x {height}); the original image is unchanged. "
                            "Use a crop of the original for fine detail if needed.",
                            metadata={"display": False, "source_id": "a13n.harness-ui.image-preview"},
                        ),
                        BinaryContent(
                            data=data,
                            media_type="image/jpeg",
                            identifier=item.identifier,
                            vendor_metadata=deepcopy(item.vendor_metadata),
                        ),
                    ]
                )
                changed = True
            if changed:
                part.content = result
    return prepared


def _preview(data: bytes, budget: int) -> tuple[bytes, int, int]:
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("Model image preview exceeds the 32 megapixel decode limit")
            # GIF previews use the first frame; animated originals remain intact.
            oriented = ImageOps.exif_transpose(source)
            if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = oriented.convert("RGB")
            image.thumbnail((MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE), Image.Resampling.LANCZOS)
            while True:
                for quality in (85, 70, 55):
                    output = BytesIO()
                    image.save(output, format="JPEG", quality=quality, optimize=True)
                    if output.tell() <= budget:
                        return output.getvalue(), image.width, image.height
                if max(image.size) <= 256:
                    raise ValueError("Too many images to prepare usable previews within the request budget")
                edge = max(256, max(image.size) * 3 // 4)
                image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Cannot prepare a preview of this image") from exc
