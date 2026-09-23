"""Host-owned async HTTP clients with endpoint and response bounds."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import anyio
import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy

from a13n_service.infra.errors import ServiceError


class BoundedBody(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream, max_bytes: int):
        self.stream, self.max_bytes = stream, max_bytes

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.max_bytes:
                raise ServiceError("payload_too_large", "Provider response exceeds its byte limit")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


@asynccontextmanager
async def open_http(
    policy: EndpointPolicy,
    *,
    timeout: float,
    max_bytes: int,
    before_request: Callable[[httpx2.Request], Awaitable[None]] | None = None,
    after_response: Callable[[httpx2.Response], Awaitable[None]] | None = None,
) -> AsyncIterator[httpx2.AsyncClient]:
    async def check_request(request: httpx2.Request) -> None:
        await policy.validate(str(request.url), resolve_dns=True)
        if before_request is not None:
            await before_request(request)

    async def bound_response(response: httpx2.Response) -> None:
        if after_response is not None:
            await after_response(response)
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise ServiceError("unavailable", "Provider response used unsupported compression")
        if not isinstance(response.stream, httpx2.AsyncByteStream):
            raise ServiceError("unavailable", "Provider response is not asynchronous")
        response.stream = BoundedBody(response.stream, max_bytes)

    # Native MCP owns entry/exit; model clients start lazily on first send.
    # The outer owner also closes clients that never reached native setup.
    client = httpx2.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={"accept-encoding": "identity"},
        event_hooks={"request": [check_request], "response": [bound_response]},
        limits=httpx2.Limits(max_connections=4, max_keepalive_connections=2),
    )
    try:
        yield client
    finally:
        if not client.is_closed:
            with anyio.fail_after(5, shield=True):
                await client.aclose()
