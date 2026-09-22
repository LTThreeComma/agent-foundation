"""Native MCP lifecycle releases actual sockets on success, setup failure and cancellation."""

import asyncio

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.infra.outbound import open_http
from a13n_service.providers.mcp import MCPConfig, RemoteMCP
from mcp.shared.exceptions import MCPError

from dev.fixtures.process import fixture_process

pytestmark = pytest.mark.anyio


@pytest.fixture
def peer(tmp_path):
    with fixture_process("dev.fixtures.mcp", arguments=("--database", str(tmp_path / "mcp.sqlite"))) as url:
        yield url


async def track_sockets(client, sockets):
    async def capture(response):
        stream = response.extensions.get("network_stream")
        if stream is not None:
            socket = stream.get_extra_info("socket")
            if socket is not None:
                sockets.append(socket)

    client.event_hooks["response"].append(capture)


@pytest.mark.parametrize("outcome", ["success", "before-entry", "setup-failure", "cancel-call"])
async def test_native_lifecycle_closes_network_sockets(peer, outcome):
    policy = EndpointPolicy.from_operator_allowlist(private_cidrs=["127.0.0.0/8"], http_origins=[peer])
    existing_tasks = asyncio.all_tasks()
    sockets = []
    clients = []

    async def session():
        async with open_http(policy, timeout=5, max_bytes=262144) as client:
            clients.append(client)
            await track_sockets(client, sockets)
            if outcome == "before-entry":
                raise RuntimeError("setup did not reach native entry")
            endpoint = "/bearer/mcp" if outcome == "setup-failure" else "/none/mcp"
            source = RemoteMCP(MCPConfig(url=peer + endpoint), source_id="connection", client=client)
            async with source.toolset() as tools:
                assert len(await tools.list_tools()) == 4
                await tools.direct_call_tool("read_count", {})
                await tools.direct_call_tool("increment", {"hold": outcome == "cancel-call"})

    if outcome in {"before-entry", "setup-failure"}:
        with pytest.raises(RuntimeError if outcome == "before-entry" else MCPError):
            await session()
    elif outcome == "cancel-call":
        task = asyncio.create_task(session())
        try:
            async with httpx2.AsyncClient(trust_env=False) as observer:
                async with asyncio.timeout(10):
                    while (await observer.get(peer + "/fixture/state")).json()["effects"] != 1:
                        if task.done():
                            await task
                        await asyncio.sleep(0.02)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await observer.post(peer + "/fixture/release")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    else:
        await session()
        await session()
        assert clients[0] is not clients[1]
    await asyncio.sleep(0)
    assert not (asyncio.all_tasks() - existing_tasks)
    assert all(client.is_closed for client in clients)
    if outcome != "before-entry":
        assert sockets
        assert all(socket.fileno() == -1 for socket in sockets)
