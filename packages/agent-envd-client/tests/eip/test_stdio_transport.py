from __future__ import annotations

import asyncio
from typing import cast

import pytest
from converge_agent_envd_client import ControlFrame, EIPProtocolError, StdioTransport
from converge_agent_envd_client.eip.v1 import (
    DataFrame,
    DataFrameKind,
    decode_data_frame,
    encode_data_frame,
)


class CapturingWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


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


def outer_frame(content_type: str, payload: bytes) -> bytes:
    return f"Content-Length: {len(payload)}\r\nContent-Type: {content_type}\r\n\r\n".encode("ascii") + payload


def test_stdio_receive_demultiplexes_interleaved_control_and_data_frames() -> None:
    async def scenario() -> None:
        reader = asyncio.StreamReader()
        writer = CapturingWriter()
        data = DataFrame(kind=DataFrameKind.CHUNK, handle="reader-one", offset=2, payload=b"abc")
        encoded = encode_data_frame(data, max_frame_bytes=1024)
        reader.feed_data(outer_frame("application/json; charset=utf-8", b"{}"))
        reader.feed_data(outer_frame("application/vnd.converge.eip-data", encoded))

        transport = StdioTransport(
            reader,
            cast(asyncio.StreamWriter, writer),
            max_transfer_frame_bytes=1024,
        )
        assert await transport.receive() == ControlFrame(b"{}")
        assert await transport.receive() == data
        await transport.close()

    asyncio.run(scenario())


def test_stdio_send_encodes_binary_frames_under_one_outer_frame() -> None:
    async def scenario() -> None:
        writer = CapturingWriter()
        transport = StdioTransport(
            asyncio.StreamReader(),
            cast(asyncio.StreamWriter, writer),
            max_transfer_frame_bytes=1024,
        )
        frame = DataFrame(kind=DataFrameKind.CHUNK, handle="writer-one", payload=b"payload")
        await transport.send(frame)

        header, payload = bytes(writer.buffer).split(b"\r\n\r\n", 1)
        assert b"Content-Type: application/vnd.converge.eip-data" in header
        assert decode_data_frame(payload, max_frame_bytes=1024) == frame
        await transport.close()

    asyncio.run(scenario())


def test_stdio_rejects_malformed_binary_body_without_treating_it_as_json() -> None:
    async def scenario() -> None:
        reader = asyncio.StreamReader()
        writer = CapturingWriter()
        reader.feed_data(outer_frame("application/vnd.converge.eip-data", b"not-a-data-frame"))
        transport = StdioTransport(
            reader,
            cast(asyncio.StreamWriter, writer),
            max_transfer_frame_bytes=1024,
        )
        with pytest.raises(EIPProtocolError, match="invalid EIP stdio data frame"):
            await transport.receive()
        await transport.close()

    asyncio.run(scenario())
