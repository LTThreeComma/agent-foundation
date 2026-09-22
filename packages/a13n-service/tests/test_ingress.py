"""Ingress rejects chunked excess and slow uploads before invoking the application."""

import asyncio

import pytest
from a13n_service.infra.ingress import BodyLimit

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("case", ["bounded", "oversize", "timeout", "disconnect"])
async def test_body_bounds_and_disconnect_forwarding(case):
    queue = asyncio.Queue()
    sent, received = [], []
    if case != "timeout":
        for message in (
            [{"type": "http.disconnect"}]
            if case == "disconnect"
            else [
                {"type": "http.request", "body": b"ab", "more_body": True},
                {"type": "http.request", "body": b"cd" if case == "bounded" else b"cde"},
                {"type": "http.disconnect"},
            ]
        ):
            queue.put_nowait(message)

    async def app(scope, receive, send):
        received.append(await receive())
        received.append(await receive())

    async def send(message):
        sent.append(message)

    await BodyLimit(app, max_bytes=4, timeout=0.02)({"type": "http"}, queue.get, send)
    if case == "bounded":
        assert received == [
            {"type": "http.request", "body": b"abcd", "more_body": False},
            {"type": "http.disconnect"},
        ]
        assert not sent
    else:
        assert not received
        if case == "disconnect":
            assert not sent
        else:
            assert sent[0]["status"] == (413 if case == "oversize" else 408)
