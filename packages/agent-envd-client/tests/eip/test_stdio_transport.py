from __future__ import annotations

import asyncio
from typing import cast

import pytest
from converge_agent_envd_client import StdioTransport


class BlockingWriter:
    def __init__(self) -> None:
        self.closed = False
        self.wait_started = asyncio.Event()
        self.allow_wait = asyncio.Event()

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_started.set()
        await self.allow_wait.wait()


def test_stdio_close_finishes_before_propagating_repeated_cancellation() -> None:
    async def scenario() -> None:
        writer = BlockingWriter()
        transport = StdioTransport(
            asyncio.StreamReader(),
            cast(asyncio.StreamWriter, writer),
        )
        close = asyncio.create_task(transport.close())
        await writer.wait_started.wait()
        close.cancel()
        await asyncio.sleep(0)
        close.cancel()
        await asyncio.sleep(0)
        assert not close.done()

        writer.allow_wait.set()
        with pytest.raises(asyncio.CancelledError):
            await close
        assert writer.closed
        await transport.close()

    asyncio.run(scenario())
