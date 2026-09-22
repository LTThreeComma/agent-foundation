"""Cross-process local CAS over atomically published, fsynced envelopes."""

import base64
import fcntl
import hashlib
import json
import os
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from uuid import uuid4

from anyio.to_thread import run_sync


class ObjectConflict(Exception):
    """Another writer owns this version or different immutable content exists."""


class ObjectCorrupt(Exception):
    """Stored identity, format, bounds or digest cannot be verified."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    content: bytes
    version: str
    writer: int | None

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class LocalObjects:
    def __init__(self, root: Path, *, max_bytes: int = 16 * 1024 * 1024, lock_timeout: float = 2):
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.lock_timeout = lock_timeout
        if max_bytes < 1 or lock_timeout <= 0:
            raise ValueError("Object bounds must be positive")

    def _path(self, key: str) -> Path:
        if not key or len(key.encode()) > 1024 or "\x00" in key:
            raise ValueError("Invalid object key")
        # Hashing keeps the logical owner-named key independent of filesystem path syntax.
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.root / digest[:2] / f"{digest}.object"

    def _read(self, key: str) -> StoredObject | None:
        path = self._path(key)
        try:
            with path.open("rb") as file:
                raw = file.read(self.max_bytes * 2 + 4097)
        except FileNotFoundError:
            return None
        if len(raw) > self.max_bytes * 2 + 4096:
            raise ObjectCorrupt("Object exceeds configured bounds")
        try:
            data = json.loads(raw)
            content = base64.b64decode(data["content"], validate=True)
            if (
                data["format"] != 1
                or data["key"] != key
                or len(content) > self.max_bytes
                or data["digest"] != hashlib.sha256(content).hexdigest()
                or not isinstance(data["version"], str)
                or not data["version"]
                or (data["writer"] is not None and (type(data["writer"]) is not int or data["writer"] < 1))
            ):
                raise ValueError("Invalid object envelope")
            return StoredObject(key, content, data["version"], data["writer"])
        except (KeyError, TypeError, ValueError) as error:
            raise ObjectCorrupt("Invalid object envelope") from error

    async def read(self, key: str) -> StoredObject | None:
        return await run_sync(self._read, key, abandon_on_cancel=True)

    async def create_payload(self, key: str, content: bytes) -> StoredObject:
        return await run_sync(partial(self._write, key, content, expected=None, writer=None), abandon_on_cancel=True)

    async def replace_snapshot(self, key: str, content: bytes, *, expected: str | None, writer: int) -> StoredObject:
        if writer < 1 or expected == "":
            raise ValueError("Invalid snapshot writer/version")
        return await run_sync(
            partial(self._write, key, content, expected=expected, writer=writer), abandon_on_cancel=True
        )

    def _write(self, key: str, content: bytes, *, expected: str | None, writer: int | None) -> StoredObject:
        if len(content) > self.max_bytes:
            raise ValueError("Object exceeds configured bounds")
        path = self._path(key)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        _sync_directory(self.root.parent)
        _sync_directory(self.root)
        lock_path = path.with_suffix(".lock")
        with lock_path.open("a+b") as lock:
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Object writer lock timed out") from None
                    time.sleep(min(0.01, max(0, deadline - time.monotonic())))
            try:
                current = self._read(key)
                if writer is None and current is not None:
                    if current.writer is None and current.content == content:
                        return current
                    raise ObjectConflict("Immutable object already has different content")
                if (current.version if current else None) != expected:
                    raise ObjectConflict("Snapshot version changed")
                if current is not None and (current.writer is None or writer is None or writer < current.writer):
                    raise ObjectConflict("Snapshot writer cannot regress or replace a payload")
                result = StoredObject(key, content, uuid4().hex, writer)
                envelope = json.dumps(
                    {
                        "format": 1,
                        "key": key,
                        "version": result.version,
                        "writer": writer,
                        "digest": result.digest,
                        "content": base64.b64encode(content).decode("ascii"),
                    },
                    separators=(",", ":"),
                ).encode()
                temporary = path.with_suffix(f".{uuid4().hex}.tmp")
                try:
                    with temporary.open("xb") as file:
                        os.chmod(temporary, 0o600)
                        file.write(envelope)
                        file.flush()
                        os.fsync(file.fileno())
                    os.replace(temporary, path)
                    _sync_directory(path.parent)
                finally:
                    temporary.unlink(missing_ok=True)
                return result
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
