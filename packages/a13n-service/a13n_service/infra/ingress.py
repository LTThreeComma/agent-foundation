"""Bound body allocation and upload time before JSON parsing or database work."""

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi.responses import JSONResponse

type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type Application = Callable[[Message, Receive, Send], Awaitable[None]]


class BodyLimit:
    def __init__(self, app: Application, *, max_bytes: int, timeout: float):
        self.app, self.max_bytes, self.timeout = app, max_bytes, timeout

    async def __call__(self, scope: Message, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        try:
            async with asyncio.timeout(self.timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.max_bytes:
                        await JSONResponse(
                            {
                                "error": {
                                    "code": "payload_too_large",
                                    "message": "Request body exceeds its byte limit",
                                    "details": {},
                                }
                            },
                            status_code=413,
                            headers={"Connection": "close"},
                        )(scope, receive, send)
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await JSONResponse(
                {"error": {"code": "invalid_argument", "message": "Request body timed out", "details": {}}},
                status_code=408,
                headers={"Connection": "close"},
            )(scope, receive, send)
            return
        buffered: bytes | None = bytes(body)
        del body

        async def bounded_receive() -> Message:
            nonlocal buffered
            if buffered is not None:
                value, buffered = buffered, None
                return {"type": "http.request", "body": value, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
