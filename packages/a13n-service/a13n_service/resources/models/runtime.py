"""Attempt-owned model clients with explicit endpoint, retry and response bounds."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx2
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model import ModelProviderDefinition
from pydantic_ai.models import Model

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.models.schemas import ModelConfig


@dataclass(frozen=True)
class ResolvedModel:
    id: str
    version: int
    provider_id: str
    provider_version: int
    provider_type: str
    config: ModelConfig
    provider_config: dict
    credential: dict | None = field(repr=False)
    pricing: dict | None


class _BoundedBody(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream, max_bytes: int):
        self.stream, self.max_bytes = stream, max_bytes

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.max_bytes:
                raise ServiceError("payload_too_large", "Model response exceeds its byte limit")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


@asynccontextmanager
async def open_model(
    selected: ResolvedModel,
    *,
    organization_id: str,
    catalog: ProviderCatalog[ModelProviderDefinition],
    keys: KeyRing,
    policy: EndpointPolicy,
    timeout: float,
    max_bytes: int,
) -> AsyncIterator[Model]:
    credential = None
    if selected.credential is not None:
        credential = json.loads(
            keys.reveal(
                Envelope.model_validate(selected.credential),
                SecretLocation(organization_id, "model_providers", "credential", selected.provider_id),
            )
        )

    async def check_request(request: httpx2.Request) -> None:
        await policy.validate(str(request.url), resolve_dns=True)

    async def bound_response(response: httpx2.Response) -> None:
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise ServiceError("unavailable", "Model response used unsupported compression")
        if not isinstance(response.stream, httpx2.AsyncByteStream):
            raise ServiceError("unavailable", "Model response is not asynchronous")
        response.stream = _BoundedBody(response.stream, max_bytes)

    async with httpx2.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={"accept-encoding": "identity"},
        event_hooks={"request": [check_request], "response": [bound_response]},
        limits=httpx2.Limits(max_connections=4, max_keepalive_connections=2),
    ) as client:
        model = await catalog.require(selected.provider_type).build(
            selected.config.model_name,
            configuration=selected.provider_config,
            credential=credential,
            model_api=selected.config.model_api,
            http_client=client,
            endpoint_policy=policy,
        )
        async with model:
            yield model
