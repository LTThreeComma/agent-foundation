"""Private Direct Local retained-output store with atomic finite reservations."""

from __future__ import annotations

import asyncio
import itertools
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_hex
from time import monotonic

from ..models import EnvironmentError, EnvironmentOperationReceipt
from ..retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
    EnvironmentOutputSegment,
    OpaqueOutputCursor,
    OpaqueOutputReference,
    _unwrap_opaque,
)


@dataclass(slots=True)
class _Record:
    path: Path
    size: int
    expires_monotonic: float
    expires_at: datetime


class LocalRetentionWriter:
    def __init__(self, store: LocalRetentionStore, token: str, path: Path, reserved: int) -> None:
        self._store = store
        self._token = token
        self._path = path
        self._reserved = reserved
        self._file = path.open("xb")
        self._written = 0
        self._finished = False

    async def write(self, chunk: bytes) -> None:
        if self._finished:
            raise EnvironmentError("Retention writer is closed.", code="environment_conflict")
        if self._written + len(chunk) > self._reserved:
            raise EnvironmentError("Retained output exceeds its reservation.", code="environment_too_large")
        await asyncio.to_thread(self._file.write, chunk)
        self._written += len(chunk)

    async def commit(self) -> BoundOutputReference:
        if self._finished:
            raise EnvironmentError("Retention writer is closed.", code="environment_conflict")
        await asyncio.to_thread(self._file.flush)
        await asyncio.to_thread(self._file.close)
        reference = await self._store._commit(self._token, self._path, self._reserved, self._written)
        self._finished = True
        return reference

    async def abort(self) -> None:
        if self._finished:
            return
        await asyncio.to_thread(self._file.close)
        self._finished = True
        await self._store._abort(self._path, self._reserved)


class LocalRetentionStore:
    def __init__(
        self,
        *,
        root: Path,
        binding_id: str,
        binding_revision: int,
        generation: str,
        max_object_bytes: int,
        max_total_bytes: int,
        max_objects: int,
        max_lifetime_seconds: float,
    ) -> None:
        self._root = root
        self._binding_id = binding_id
        self._binding_revision = binding_revision
        self._generation = generation
        self._max_object_bytes = max_object_bytes
        self._max_total_bytes = max_total_bytes
        self._max_objects = max_objects
        self._max_lifetime_seconds = max_lifetime_seconds
        self._records: dict[str, _Record] = {}
        self._reserved_bytes = 0
        self._reserved_objects = 0
        self._lock = asyncio.Lock()
        self._operations = itertools.count(1)
        self._closed = False

    async def reserve(self, *, max_bytes: int) -> LocalRetentionWriter:
        if max_bytes <= 0 or max_bytes > self._max_object_bytes:
            raise EnvironmentError("Invalid retained-output reservation.", code="environment_request_invalid")
        async with self._lock:
            await self._expire_locked()
            if self._closed:
                raise EnvironmentError("Retained-output store is closed.", code="environment_closed")
            if (
                len(self._records) + self._reserved_objects >= self._max_objects
                or self._reserved_bytes + max_bytes > self._max_total_bytes
            ):
                raise EnvironmentError("Retained-output quota is exhausted.", code="environment_quota_exceeded")
            token = token_hex(16)
            path = self._root / f"candidate-{token}"
            self._reserved_bytes += max_bytes
            self._reserved_objects += 1
        try:
            return LocalRetentionWriter(self, token, path, max_bytes)
        except BaseException:
            async with self._lock:
                self._reserved_bytes -= max_bytes
                self._reserved_objects -= 1
            raise

    async def _commit(self, token: str, path: Path, reserved: int, written: int) -> BoundOutputReference:
        final = self._root / f"output-{token}"
        async with self._lock:
            if self._closed:
                raise EnvironmentError("Retained-output store is closed.", code="environment_closed")
            path.replace(final)
            self._reserved_bytes -= reserved - written
            self._reserved_objects -= 1
            expires_at = datetime.now(UTC) + timedelta(seconds=self._max_lifetime_seconds)
            self._records[token] = _Record(
                path=final,
                size=written,
                expires_monotonic=monotonic() + self._max_lifetime_seconds,
                expires_at=expires_at,
            )
        return BoundOutputReference(
            binding_id=self._binding_id,
            binding_revision=self._binding_revision,
            observed_generation=self._generation,
            reference=OpaqueOutputReference._from_payload(token),
        )

    async def _abort(self, path: Path, reserved: int) -> None:
        async with self._lock:
            path.unlink(missing_ok=True)
            if not self._closed:
                self._reserved_bytes -= reserved
                self._reserved_objects -= 1

    def _validate_reference(self, reference: BoundOutputReference) -> str:
        if (
            reference.binding_id != self._binding_id
            or reference.binding_revision != self._binding_revision
            or reference.observed_generation != self._generation
        ):
            raise EnvironmentError("Retained-output reference is foreign or stale.", code="environment_stale_binding")
        return _unwrap_opaque(reference.reference, OpaqueOutputReference)

    async def read(
        self,
        reference: BoundOutputReference,
        *,
        cursor: BoundOutputCursor | None = None,
        start_offset: int | None = None,
        policy: EnvironmentOutputPolicy,
    ) -> EnvironmentOutputReadResult:
        if cursor is not None and start_offset is not None:
            raise EnvironmentError("Specify cursor or start_offset, not both.", code="environment_request_invalid")
        token = self._validate_reference(reference)
        if cursor is not None:
            if (
                cursor.binding_id != self._binding_id
                or cursor.binding_revision != self._binding_revision
                or cursor.observed_generation != self._generation
            ):
                raise EnvironmentError("Output cursor is foreign or stale.", code="environment_stale_binding")
            raw = _unwrap_opaque(cursor.cursor, OpaqueOutputCursor)
            try:
                cursor_token, raw_offset = raw.split(":", 1)
                offset = int(raw_offset)
            except ValueError:
                raise EnvironmentError("Output cursor is invalid.", code="environment_cursor_invalid") from None
            if cursor_token != token:
                raise EnvironmentError("Output cursor does not match reference.", code="environment_cursor_invalid")
        else:
            offset = start_offset or 0
        if offset < 0:
            raise EnvironmentError("Output offset is invalid.", code="environment_request_invalid")
        async with self._lock:
            await self._expire_locked()
            record = self._records.get(token)
            if record is None:
                raise EnvironmentError("Retained output is unavailable.", code="environment_not_found")
            size = record.size
            expires_at = record.expires_at
            path = record.path
        read_size = min(policy.max_inline_bytes, max(size - offset, 0))
        data = await asyncio.to_thread(_read_range, path, offset, read_size)
        end = offset + len(data)
        complete = end >= size
        next_cursor = None
        if not complete:
            next_cursor = BoundOutputCursor(
                binding_id=self._binding_id,
                binding_revision=self._binding_revision,
                observed_generation=self._generation,
                cursor=OpaqueOutputCursor._from_payload(f"{token}:{end}"),
            )
        capture = EnvironmentOutputCapture(
            kind="retained",
            producer_complete=True,
            content_complete=complete,
            produced_bytes=size,
            captured_bytes=size,
            dropped_bytes=0,
            inline=None,
            preview=(EnvironmentOutputSegment(start_offset=offset, data=data),) if data else (),
            reference=reference,
            cursor=next_cursor,
            available_start=0,
            available_end=size,
            expires_at=expires_at,
        )
        return EnvironmentOutputReadResult(
            chunks=(EnvironmentOutputSegment(start_offset=offset, data=data),) if data else (),
            next_cursor=next_cursor,
            capture=capture,
        )

    async def release(
        self,
        *,
        reference: BoundOutputReference | None = None,
        cursor: BoundOutputCursor | None = None,
    ) -> EnvironmentOperationReceipt:
        if (reference is None) == (cursor is None):
            raise EnvironmentError(
                "Release requires exactly one reference or cursor.", code="environment_request_invalid"
            )
        if reference is not None:
            token = self._validate_reference(reference)
        else:
            assert cursor is not None
            if (
                cursor.binding_id != self._binding_id
                or cursor.binding_revision != self._binding_revision
                or cursor.observed_generation != self._generation
            ):
                raise EnvironmentError("Output cursor is foreign or stale.", code="environment_stale_binding")
            raw = _unwrap_opaque(cursor.cursor, OpaqueOutputCursor)
            token, separator, raw_offset = raw.partition(":")
            if not separator or not raw_offset.isdigit():
                raise EnvironmentError("Output cursor is invalid.", code="environment_cursor_invalid")
        async with self._lock:
            record = self._records.pop(token, None)
            if record is None:
                raise EnvironmentError("Retained output is unavailable.", code="environment_not_found")
            self._reserved_bytes -= record.size
            record.path.unlink(missing_ok=True)
        return self._receipt()

    async def capture(self, data: bytes, policy: EnvironmentOutputPolicy) -> EnvironmentOutputCapture:
        produced = len(data)
        if produced <= policy.max_inline_bytes:
            return EnvironmentOutputCapture(
                kind="empty" if not data else "inline",
                producer_complete=True,
                content_complete=True,
                produced_bytes=produced,
                captured_bytes=produced,
                dropped_bytes=0,
                inline=data,
                available_end=produced,
            )
        if produced > policy.max_output_bytes and policy.overflow == "fail":
            raise EnvironmentError("Command output exceeds configured limit.", code="environment_too_large")
        if policy.overflow == "retain" and produced <= policy.max_output_bytes:
            writer = await self.reserve(max_bytes=policy.max_output_bytes)
            try:
                await writer.write(data)
                reference = await writer.commit()
            except BaseException:
                await writer.abort()
                raise
            record = self._records[_unwrap_opaque(reference.reference, OpaqueOutputReference)]
            preview_data = data[: policy.max_inline_bytes]
            return EnvironmentOutputCapture(
                kind="retained",
                producer_complete=True,
                content_complete=False,
                produced_bytes=produced,
                captured_bytes=produced,
                dropped_bytes=0,
                preview=(EnvironmentOutputSegment(start_offset=0, data=preview_data),),
                reference=reference,
                available_end=produced,
                expires_at=record.expires_at,
            )
        captured = data[: policy.max_inline_bytes]
        return EnvironmentOutputCapture(
            kind="truncated",
            producer_complete=True,
            content_complete=False,
            produced_bytes=produced,
            captured_bytes=len(captured),
            dropped_bytes=produced - len(captured),
            inline=captured,
            available_end=len(captured),
        )

    async def _expire_locked(self) -> None:
        now = monotonic()
        expired = [token for token, record in self._records.items() if record.expires_monotonic <= now]
        for token in expired:
            record = self._records.pop(token)
            self._reserved_bytes -= record.size
            record.path.unlink(missing_ok=True)

    def _receipt(self) -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            binding_id=self._binding_id,
            binding_revision=self._binding_revision,
            observed_generation=self._generation,
            operation_id=f"operation-{next(self._operations)}",
            stage="completed",
            outcome="succeeded",
        )

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            self._records.clear()
            self._reserved_bytes = 0
            self._reserved_objects = 0
        await asyncio.to_thread(shutil.rmtree, self._root, True)


def _read_range(path: Path, offset: int, size: int) -> bytes:
    with path.open("rb") as file:
        file.seek(offset)
        return file.read(size)
