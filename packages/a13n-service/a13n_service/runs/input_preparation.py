"""Resolve accepted input references into frozen native content before delivery."""

import asyncio
import json
from urllib.parse import urljoin

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from pydantic import TypeAdapter
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessagesTypeAdapter,
    ModelRequest,
    TextContent,
    UserContent,
    UserPromptPart,
)

from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.infra.outbound import open_http
from a13n_service.resources.assets import service as assets
from a13n_service.runs.attempts import lock_authority
from a13n_service.runs.input_frames import (
    MAX_NATIVE_ITEMS,
    MAX_NATIVE_PAYLOAD_BYTES,
    PreparedContent,
    PreparedInputs,
    prepared_marker,
)
from a13n_service.runs.policy import authorize_execution
from a13n_service.runs.schemas import (
    AssetInput,
    AttemptClaim,
    EnvironmentPathInput,
    JsonInput,
    MessagePayload,
    TextInput,
    UrlInput,
)

_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain"})
_MAX_REDIRECTS = 3
_CONTENT_CODEC = TypeAdapter(UserContent)


def _encoded_size(item: PreparedContent) -> int:
    return len(_CONTENT_CODEC.dump_json(item))


def _frame_base(run_id: str, entry_id: str, count: int) -> int:
    # The digest is always 64 ASCII bytes; measure its marker and the native
    # request wrapper through the same codec used by HarnessState history.
    marker = prepared_marker(run_id, entry_id, count, "0" * 64)
    return len(ModelMessagesTypeAdapter.dump_json([ModelRequest(parts=[UserPromptPart([marker])])]))


def _binary_limit(remaining: int, media_type: str) -> int:
    # BinaryContent's native JSON expands data as base64. Reserve its descriptor
    # and list separator before allowing any response bytes into memory.
    available = remaining - 1 - _encoded_size(BinaryContent(b"", media_type=media_type))
    if available < 0:
        raise ServiceError("payload_too_large", "Prepared input exceeds its native byte limit")
    return 3 * (available // 4)


def render_json(value: object) -> TextContent:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return TextContent("Structured JSON:\n" + encoded)


async def _asset(
    storage: Storage, objects: LocalObjects, claim: AttemptClaim, asset_id: str, *, timeout: float, remaining: int
) -> BinaryContent:
    # Release the Run/Attempt locks before immutable object I/O. Asset.content
    # rechecks the principal's live Workspace read authority around that I/O.
    async with transaction(storage) as session:
        run, _, _ = await lock_authority(session, claim)
        actor = await authorize_execution(session, run)
    view = await assets.get(storage, actor, claim.workspace_id, asset_id)
    if view.content_type not in _MEDIA_TYPES:
        raise ServiceError("invalid_argument", "Asset media type is unsupported by native input")
    if view.size > _binary_limit(remaining, view.content_type):
        raise ServiceError("payload_too_large", "Prepared input exceeds its native byte limit")
    view, data = await assets.content(storage, objects, actor, claim.workspace_id, asset_id, timeout=timeout)
    return BinaryContent(data, media_type=view.content_type)


async def _url(url: str, policy: EndpointPolicy, *, timeout: float, max_bytes: int) -> BinaryContent:
    try:
        async with asyncio.timeout(timeout * (_MAX_REDIRECTS + 1)):
            async with open_http(policy, timeout=timeout, max_bytes=max_bytes) as client:
                current = await policy.validate(url, resolve_dns=True)
                for _ in range(_MAX_REDIRECTS + 1):
                    response = await client.get(current)
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location")
                        if not location:
                            raise ServiceError("unavailable", "Input URL redirect has no target")
                        current, _ = await policy.validate_redirect(
                            current, urljoin(current, location), resolve_dns=True
                        )
                        continue
                    if response.status_code != 200:
                        raise ServiceError("unavailable", "Input URL did not return content")
                    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if media_type not in _MEDIA_TYPES:
                        raise ServiceError("invalid_argument", "Input URL media type is unsupported")
                    return BinaryContent(response.content, media_type=media_type)
    except (httpx2.HTTPError, TimeoutError, EndpointPolicyError) as error:
        raise ServiceError("unavailable", "Input URL could not be prepared") from error
    raise ServiceError("unavailable", "Input URL exceeded its redirect limit")


async def prepare(
    storage: Storage,
    objects: LocalObjects,
    claim: AttemptClaim,
    entries: tuple[tuple[str, MessagePayload], ...],
    prepared: PreparedInputs,
    *,
    policy: EndpointPolicy,
    timeout: float,
    max_bytes: int,
) -> list[PreparedContent]:
    """Do all reference I/O before offering each complete frame to the Harness."""
    limit = min(max_bytes, MAX_NATIVE_PAYLOAD_BYTES)
    spent = 0
    batch: list[tuple[str, list[PreparedContent]]] = []
    if len({entry_id for entry_id, _ in entries}) != len(entries):
        raise ServiceError("conflict", "Prepared input batch contains a duplicate entry")
    if any(entry_id in prepared.expected or entry_id in prepared.receipts for entry_id, _ in entries):
        raise ServiceError("conflict", "Prepared input was offered twice")
    # Keep expectations untouched until the entire batch has passed preparation.
    # An earlier frame cannot be receipted from a partly resolved later entry.
    for entry_id, payload in entries:
        if not 1 <= len(payload.content) <= MAX_NATIVE_ITEMS:
            raise ServiceError("payload_too_large", "Prepared input item count exceeds its limit")
        size = _frame_base(claim.run_id, entry_id, len(payload.content))
        if spent + size > limit:
            raise ServiceError("payload_too_large", "Prepared input exceeds its native byte limit")
        items: list[PreparedContent] = []
        for part in payload.content:
            remaining = limit - spent - size
            if isinstance(part, TextInput):
                item = TextContent(part.text)
            elif isinstance(part, JsonInput):
                item = render_json(part.value)
            elif isinstance(part, AssetInput):
                item = await _asset(storage, objects, claim, part.asset_id, timeout=timeout, remaining=remaining)
            elif isinstance(part, UrlInput):
                allowance = min(_binary_limit(remaining, media_type) for media_type in _MEDIA_TYPES)
                item = await _url(part.url, policy, timeout=timeout, max_bytes=allowance)
            elif isinstance(part, EnvironmentPathInput):
                # A selected native Environment owner is required before path input can execute.
                raise ServiceError("unavailable", "Selected Environment path input is not configured")
            else:
                raise ServiceError("invalid_argument", "Input content type is unsupported")
            size += 1 + _encoded_size(item)
            if spent + size > limit:
                raise ServiceError("payload_too_large", "Prepared input exceeds its native byte limit")
            items.append(item)
        spent += size
        batch.append((entry_id, items))
    offered: list[PreparedContent] = []
    for entry_id, items in batch:
        offered.extend(prepared.offer(entry_id, items))
    return offered
