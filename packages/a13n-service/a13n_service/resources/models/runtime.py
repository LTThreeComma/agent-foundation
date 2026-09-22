"""Attempt-owned model clients with explicit endpoint, retry and response bounds."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model import ModelProviderDefinition
from pydantic_ai.models import Model

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.outbound import open_http
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

    async with open_http(policy, timeout=timeout, max_bytes=max_bytes) as client:
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
