"""Search and scrape backends for the Harness Web capability, one per selected web provider resource.

The backend ID is the provider resource ID, so two accounts of one type stay distinct. Fetch and download use
the host transport and need no provider resource. Which operation a provider serves was checked when the agent
revision selecting it was validated.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a13n_harness.capabilities.web import WebScrapeBackendBinding, WebSearchBackendBinding
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.web.definition import WebProvider
from a13n_harness.providers.web.options import ScrapeOptions, SearchOptions
from a13n_harness.providers.web.transport import WebProviderTransport

from a13n_service.infra.crypto import KeyRing
from a13n_service.providers.registry import Registry
from a13n_service.resources.providers.service import ResolvedProvider


@asynccontextmanager
async def open_search_backend(
    provider: ResolvedProvider, options: SearchOptions, *, registry: Registry, keys: KeyRing, policy: EndpointPolicy
) -> AsyncIterator[WebSearchBackendBinding]:
    async with _open(provider, options, registry=registry, keys=keys, policy=policy) as handle:
        yield WebSearchBackendBinding(provider.id, handle)


@asynccontextmanager
async def open_scrape_backend(
    provider: ResolvedProvider, options: ScrapeOptions, *, registry: Registry, keys: KeyRing, policy: EndpointPolicy
) -> AsyncIterator[WebScrapeBackendBinding]:
    async with _open(provider, options, registry=registry, keys=keys, policy=policy) as handle:
        yield WebScrapeBackendBinding(provider.id, handle, handle.supports_domain_restrictions)


@asynccontextmanager
async def _open(
    provider: ResolvedProvider,
    options: SearchOptions | ScrapeOptions,
    *,
    registry: Registry,
    keys: KeyRing,
    policy: EndpointPolicy,
) -> AsyncIterator[WebProvider]:
    async with registry.get("web", provider.type).open(
        provider.config,
        provider.reveal_credential(keys),
        options=options,
        transport=WebProviderTransport(endpoint_policy=policy),
    ) as handle:
        yield handle
