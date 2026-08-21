from __future__ import annotations

import asyncio

from converge_agent_envd_client.errors import EIPProtocolError, EIPTransportClosedError, EIPTransportError

_MAX_HEADER_BYTES = 8 * 1024
_CONTENT_TYPE = "application/json; charset=utf-8"
_SECURITY_SENSITIVE_HEADERS = {"authorization", "content-encoding", "eip-session", "transfer-encoding"}


class StdioTransport:
    """LSP-style EIP framing over trusted parent-supplied asyncio pipes."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        process: asyncio.subprocess.Process | None = None,
        max_request_bytes: int = 1024 * 1024,
        max_response_bytes: int = 1024 * 1024,
    ) -> None:
        _validate_limit("max_request_bytes", max_request_bytes)
        _validate_limit("max_response_bytes", max_response_bytes)
        self._reader = reader
        self._writer = writer
        self._process = process
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._write_lock = asyncio.Lock()
        self._read_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False

    @classmethod
    def from_process(
        cls,
        process: asyncio.subprocess.Process,
        *,
        max_request_bytes: int = 1024 * 1024,
        max_response_bytes: int = 1024 * 1024,
    ) -> StdioTransport:
        if process.stdin is None or process.stdout is None:
            raise ValueError("process must have asyncio stdin and stdout pipes")
        return cls(
            process.stdout,
            process.stdin,
            process=process,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
        )

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._process

    def set_limits(self, *, max_request_bytes: int, max_response_bytes: int) -> None:
        _validate_limit("max_request_bytes", max_request_bytes)
        _validate_limit("max_response_bytes", max_response_bytes)
        self._max_request_bytes = min(self._max_request_bytes, max_request_bytes)
        self._max_response_bytes = min(self._max_response_bytes, max_response_bytes)

    async def send(self, payload: bytes) -> None:
        if self._closed:
            raise EIPTransportClosedError("stdio transport is closed")
        if len(payload) > self._max_request_bytes:
            raise EIPTransportError("EIP request exceeds the negotiated request byte limit")

        task = asyncio.create_task(self._send_frame(payload))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Preserve framing if the write has already completed. If drain is still
            # blocked, make the carrier terminal rather than leave a partial frame.
            if not task.done():
                self._closed = True
                self._writer.close()
            try:
                await task
            except Exception as error:
                # A concrete transport failure takes precedence over cancellation so
                # the requester can terminate every pending correlation.
                raise error
            raise

    async def receive(self) -> bytes:
        if self._closed:
            raise EIPTransportClosedError("stdio transport is closed")
        async with self._read_lock:
            try:
                headers = await self._read_headers()
                content_length = _parse_headers(headers, self._max_response_bytes)
                return await self._reader.readexactly(content_length)
            except asyncio.IncompleteReadError as error:
                returncode = self._process.returncode if self._process is not None else None
                detail = "stdio response stream reached EOF"
                if returncode is not None:
                    detail = f"{detail} (process exit code {returncode})"
                raise EIPTransportClosedError(detail) from error
            except (ConnectionError, OSError) as error:
                raise EIPTransportError("failed to read an EIP stdio response") from error

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            # Closing before waiting for the write lock wakes a drain blocked by
            # backpressure and makes any partial frame terminal for the carrier.
            self._writer.close()
            self._close_task = asyncio.create_task(self._finish_close(), name="eip-stdio-close")
        close_task = self._close_task
        await _await_shared_close(close_task)

    async def _finish_close(self) -> None:
        async with self._write_lock:
            try:
                await self._writer.wait_closed()
            except (BrokenPipeError, ConnectionError, OSError):
                pass

    async def _send_frame(self, payload: bytes) -> None:
        async with self._write_lock:
            if self._closed:
                raise EIPTransportClosedError("stdio transport is closed")
            header = f"Content-Length: {len(payload)}\r\nContent-Type: {_CONTENT_TYPE}\r\n\r\n".encode("ascii")
            try:
                self._writer.write(header)
                self._writer.write(payload)
                await self._writer.drain()
            except (BrokenPipeError, ConnectionError, OSError) as error:
                raise EIPTransportError("failed to write an EIP stdio request") from error

    async def _read_headers(self) -> bytes:
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"):
            if len(header) == _MAX_HEADER_BYTES:
                raise EIPProtocolError("stdio response header exceeds its byte limit")
            header.extend(await self._reader.readexactly(1))
        return bytes(header)


def _parse_headers(header: bytes, max_body_bytes: int) -> int:
    try:
        text = header.decode("ascii")
    except UnicodeDecodeError as error:
        raise EIPProtocolError("stdio response header must be ASCII") from error

    values: dict[str, str] = {}
    for line in text[:-4].split("\r\n"):
        if ":" not in line:
            raise EIPProtocolError("malformed stdio response header")
        name, value = line.split(":", 1)
        if not name or not all(character.isascii() and (character.isalnum() or character == "-") for character in name):
            raise EIPProtocolError("malformed stdio response header name")
        normalized_name = name.lower()
        if normalized_name in values:
            raise EIPProtocolError("duplicate stdio response header")
        value = value.strip(" \t")
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise EIPProtocolError("malformed stdio response header value")
        values[normalized_name] = value

    if "content-length" not in values:
        raise EIPProtocolError("Content-Length response header is required")
    length_text = values["content-length"]
    if not length_text.isascii() or not length_text.isdecimal() or (length_text != "0" and length_text.startswith("0")):
        raise EIPProtocolError("Content-Length must be canonical decimal")
    content_length = int(length_text)
    if content_length > max_body_bytes:
        raise EIPProtocolError("stdio response body exceeds its byte limit")

    if content_type := values.get("content-type"):
        if not _valid_content_type(content_type):
            raise EIPProtocolError("Content-Type must identify UTF-8 JSON")
    for name in values:
        if name in _SECURITY_SENSITIVE_HEADERS or name.startswith("eip-"):
            raise EIPProtocolError("security-sensitive stdio response header is forbidden")
    return content_length


def _valid_content_type(value: str) -> bool:
    parts = [part.strip().lower() for part in value.split(";")]
    return parts in [["application/json"], ["application/json", "charset=utf-8"]]


async def _await_shared_close(close_task: asyncio.Task[None]) -> None:
    cancelled = False
    while True:
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            if close_task.done():
                raise
            cancelled = True
            continue
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        break
    if cancelled:
        raise asyncio.CancelledError


def _validate_limit(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
