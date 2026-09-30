from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.host_file_transfers import (
    TRANSFER_TTL_SECONDS,
    FileTransferRequest,
    FileTransfers,
    byte_range,
    stream_response,
)
from a13n_harness_ui.host_files import FILE_CHUNK_BYTES, FileReadRequest, HostFiles
from starlette.requests import HTTPConnection
from starlette.types import Message

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("items=0-1", None),
        ("bytes=0-1,4-5", None),
        ("bytes=0-3", (0, 3)),
        ("bytes=8-99", (8, 9)),
        ("bytes=3-", (3, 9)),
        ("bytes=-3", (7, 9)),
        ("bytes=-99", (0, 9)),
    ],
)
def test_byte_ranges(header: str | None, expected: tuple[int, int] | None) -> None:
    assert byte_range(header, 10) == expected


@pytest.mark.parametrize("header", ["bytes=-", "bytes=-0", "bytes=10-", "bytes=3-2", "bytes=bad"])
def test_unsatisfiable_ranges(header: str) -> None:
    with pytest.raises(ValueError):
        byte_range(header, 10)
    with pytest.raises(ValueError):
        byte_range("bytes=0-", 0)


def test_transfer_capabilities_are_signed_scoped_expiring_and_listener_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a13n_harness_ui.host_file_transfers.time.time", lambda: 1000)
    transfers = FileTransfers()
    selected = FileTransferRequest(path="/tmp/clip.mp4", expected_revision="reviewed", purpose="media")
    access = transfers.issue(selected, Path(selected.path))
    token = parse_qs(urlsplit(access.url).query)["token"][0]
    claims = transfers.verify(token)
    assert claims.path == selected.path and claims.expected_revision == "reviewed"
    assert claims.purpose == "media" and claims.media_type == "video/mp4"
    assert access.expires_at == 1000 + TRANSFER_TTL_SECONDS
    for invalid in ("", token + "x", "x" + token, token.replace(".", ".x")):
        with pytest.raises(HarnessUiError, match="expired or is invalid"):
            transfers.verify(invalid)
    with pytest.raises(HarnessUiError):
        FileTransfers().verify(token)
    monkeypatch.setattr("a13n_harness_ui.host_file_transfers.time.time", lambda: access.expires_at)
    with pytest.raises(HarnessUiError):
        transfers.verify(token)


def test_inline_access_only_accepts_passive_audio_and_video() -> None:
    transfers = FileTransfers()
    for name in ("page.html", "graphic.svg", "document.pdf"):
        with pytest.raises(HarnessUiError, match="cannot be played inline"):
            transfers.issue(
                FileTransferRequest(path=f"/tmp/{name}", expected_revision="one", purpose="media"), Path(name)
            )
        transfers.issue(
            FileTransferRequest(path=f"/tmp/{name}", expected_revision="one", purpose="download"), Path(name)
        )
    selected = FileTransferRequest(path="/tmp/latest", expected_revision="one", purpose="media")
    access = transfers.issue(selected, Path("/tmp/clip.MP4"))
    token = parse_qs(urlsplit(access.url).query)["token"][0]
    assert transfers.verify(token).media_type == "video/mp4"


def connection(method: str = "GET", **headers: str) -> HTTPConnection:
    return HTTPConnection(
        {
            "type": "http",
            "method": method,
            "path": "/api/host/files/content",
            "asgi": {"spec_version": "2.4"},
            "headers": [(key.replace("_", "-").encode(), value.encode()) for key, value in headers.items()],
        }
    )


async def receive() -> Message:
    return {"type": "http.disconnect"}


async def test_large_transfers_remain_chunk_bounded_and_close_on_success(tmp_path: Path) -> None:
    path = tmp_path / "large.mp4"
    size = 15_047_567
    with path.open("wb") as stream:
        stream.truncate(size)
    files = HostFiles(enabled=True)
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    request = connection()
    response = await stream_response(request, opened, files.read_stream, filename=path.name)
    total = 0

    async def send(message: Message) -> None:
        nonlocal total
        if message["type"] == "http.response.body":
            assert len(message["body"]) <= FILE_CHUNK_BYTES
            total += len(message["body"])

    await response(request.scope, receive, send)
    assert total == size
    assert opened.stream.closed
    assert response.headers["content-length"] == str(size)
    assert response.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize(
    ("method", "header", "status", "content"),
    [
        ("GET", "bytes=2-4", 206, b"234"),
        ("GET", "bytes=-2", 206, b"89"),
        ("GET", "bytes=20-", 416, b""),
        ("HEAD", "bytes=2-4", 200, b""),
    ],
)
async def test_range_delivery_and_head_close_handles(
    tmp_path: Path, method: str, header: str, status: int, content: bytes
) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"0123456789")
    files = HostFiles(enabled=True)
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    request = connection(method, range=header)
    response = await stream_response(
        request, opened, files.read_stream, filename=path.name, media_type="video/mp4", inline=True
    )
    chunks: list[bytes] = []

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            chunks.append(message["body"])

    await response(request.scope, receive, send)
    assert response.status_code == status
    assert b"".join(chunks) == content
    assert opened.stream.closed
    if status == 416:
        assert response.headers["content-range"] == "bytes */10"


async def test_stream_stops_on_a_changed_revision_and_closes_on_disconnect(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"a" * (FILE_CHUNK_BYTES + 1))
    files = HostFiles(enabled=True)
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    request = connection()
    response = await stream_response(request, opened, files.read_stream, filename=path.name)
    sent: list[bytes] = []

    async def changed(message: Message) -> None:
        if message["type"] == "http.response.body" and message["body"]:
            sent.append(message["body"])
            path.write_bytes(b"b" * (FILE_CHUNK_BYTES + 1))

    with pytest.raises(HarnessUiError, match="File content changed"):
        await response(request.scope, receive, changed)
    assert sent == [b"a" * FILE_CHUNK_BYTES]
    assert opened.stream.closed
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    response = await stream_response(request, opened, files.read_stream, filename=path.name)

    async def disconnected(_message: Message) -> None:
        raise RuntimeError("Disconnected before the body iterator started")

    with pytest.raises(RuntimeError, match="Disconnected"):
        await response(request.scope, receive, disconnected)
    assert opened.stream.closed


async def test_stream_rejects_stale_open_and_first_read(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"old")
    files = HostFiles(enabled=True)
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    revision = opened.entry.revision
    path.write_bytes(b"new")
    with pytest.raises(HarnessUiError, match="File content changed"):
        await stream_response(connection(), opened, files.read_stream, filename=path.name)
    assert opened.stream.closed
    with pytest.raises(HarnessUiError, match="File content changed"):
        await files.open_stream(FileReadRequest(path=str(path), expected_revision=revision))
    with pytest.raises(HarnessUiError, match="share-computer"):
        await HostFiles().open_stream(FileReadRequest(path=str(path)))
