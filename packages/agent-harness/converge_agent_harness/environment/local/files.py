"""Direct-local bounded filesystem implementation."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import itertools
import os
import re
import shutil
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ..files import (
    AsyncFileReader,
    AsyncFileWriter,
    FileByteRange,
    FileContentDigest,
    FileCopyResult,
    FileKind,
    FileListEntry,
    FileListPage,
    FileMetadata,
    FileMutationResult,
    FilePatchResult,
    FileQueryCursor,
    FileQueryPage,
    FileQueryRequest,
    FileReadCompletion,
    FileRevision,
    FileTextCursor,
    FileTextMatch,
    FileTextPage,
    FileTextPosition,
    FileTextSearchPage,
    FileTextSearchRequest,
    FileWriteMode,
    FileWriteResult,
)
from ..models import EnvironmentError, EnvironmentOperationReceipt

if TYPE_CHECKING:
    from .binding import DirectLocalFilePolicy

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class _LocalReader(AsyncFileReader):
    def __init__(
        self,
        operator: LocalFileOperator,
        logical_path: str,
        native_path: Path,
        byte_range: FileByteRange,
        revision: FileRevision,
    ) -> None:
        self._operator = operator
        self._logical_path = logical_path
        self._native_path = native_path
        self._range = byte_range
        self._initial_revision = revision
        self._file: Any | None = None
        self._read = 0
        self._digest = hashlib.sha256()
        self._completion: FileReadCompletion | None = None

    @property
    def completion(self) -> FileReadCompletion | None:
        return self._completion

    async def open(self) -> None:
        self._file = await asyncio.to_thread(self._native_path.open, "rb")
        await asyncio.to_thread(self._file.seek, self._range.offset)

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        if self._file is None:
            raise RuntimeError("Reader is not entered")
        remaining = self._range.length
        while remaining is None or remaining > 0:
            size = 65_536 if remaining is None else min(65_536, remaining)
            chunk = await asyncio.to_thread(self._file.read, size)
            if not chunk:
                break
            self._read += len(chunk)
            self._digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
            yield chunk
        current = await asyncio.to_thread(self._operator._revision, self._native_path)
        if current != self._initial_revision:
            raise EnvironmentError("File changed during raw read.", code="environment_conflict")
        position = self._range.offset + self._read
        size = await asyncio.to_thread(lambda: self._native_path.stat().st_size)
        self._completion = FileReadCompletion(
            range_start=self._range.offset,
            range_end=position,
            bytes_read=self._read,
            digest=FileContentDigest(value=self._digest.hexdigest()),
            source_eof_at_end=position >= size,
            stability="verified",
        )

    async def close(self) -> None:
        if self._file is not None:
            await asyncio.to_thread(self._file.close)
            self._file = None


class _LocalWriter(AsyncFileWriter):
    def __init__(
        self,
        operator: LocalFileOperator,
        logical_path: str,
        native_path: Path,
        mode: FileWriteMode,
        expected_revision: FileRevision | None,
    ) -> None:
        self._operator = operator
        self._logical_path = logical_path
        self._native_path = native_path
        self._mode: FileWriteMode = mode
        self._expected_revision = expected_revision
        self._temp_path: Path | None = None
        self._file: Any | None = None
        self._written = 0
        self._committed = False

    async def open(self) -> None:
        fd, name = await asyncio.to_thread(
            tempfile.mkstemp,
            prefix=".converge-write-",
            dir=self._native_path.parent,
        )
        self._temp_path = Path(name)
        self._file = os.fdopen(fd, "wb")
        if self._mode == "append" and self._native_path.exists():
            existing_size = await asyncio.to_thread(lambda: self._native_path.stat().st_size)
            if existing_size > self._operator.max_transfer_bytes:
                raise EnvironmentError("Append source exceeds transfer limit.", code="environment_too_large")
            existing = await asyncio.to_thread(self._native_path.read_bytes)
            await self.write(existing)

    async def write(self, chunk: bytes) -> None:
        if self._file is None or self._committed:
            raise EnvironmentError("File writer is not writable.", code="environment_conflict")
        if not isinstance(chunk, bytes):
            raise TypeError("raw writer chunks must be bytes")
        if self._written + len(chunk) > self._operator.max_transfer_bytes:
            raise EnvironmentError("Raw write exceeds transfer limit.", code="environment_too_large")
        await asyncio.to_thread(self._file.write, chunk)
        self._written += len(chunk)

    async def commit(self) -> FileWriteResult:
        if self._file is None or self._temp_path is None or self._committed:
            raise EnvironmentError("File writer cannot commit.", code="environment_conflict")
        await asyncio.to_thread(self._file.flush)
        await asyncio.to_thread(os.fsync, self._file.fileno())
        await asyncio.to_thread(self._file.close)
        self._file = None
        await asyncio.to_thread(
            self._operator._publish_staged,
            self._temp_path,
            self._native_path,
            self._mode,
            self._expected_revision,
        )
        self._temp_path = None
        self._committed = True
        revision = await asyncio.to_thread(self._operator._revision, self._native_path)
        return FileWriteResult(
            path=self._logical_path,
            revision=revision,
            bytes_written=self._written,
            receipt=self._operator._receipt(),
        )

    async def abort(self) -> None:
        if self._file is not None:
            await asyncio.to_thread(self._file.close)
            self._file = None
        if self._temp_path is not None:
            await asyncio.to_thread(self._temp_path.unlink, missing_ok=True)
            self._temp_path = None


class LocalFileOperator:
    """Root-confined Direct Local file operations with staged publication."""

    def __init__(
        self,
        *,
        root: Path,
        read_only: bool,
        policy: DirectLocalFilePolicy,
        binding_id: str,
        binding_revision: int,
        generation: str,
    ) -> None:
        self._root = root.resolve(strict=True)
        self._read_only = read_only
        self._policy = policy
        self._binding_id = binding_id
        self._binding_revision = binding_revision
        self._generation = generation
        self._operations = itertools.count(1)

    @property
    def max_transfer_bytes(self) -> int:
        return self._policy.max_transfer_bytes

    def _receipt(self) -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            binding_id=self._binding_id,
            binding_revision=self._binding_revision,
            observed_generation=self._generation,
            operation_id=f"operation-{next(self._operations)}",
            stage="completed",
            outcome="succeeded",
        )

    def _logical(self, native: Path) -> str:
        relative = native.relative_to(self._root)
        return "/" if not relative.parts else f"/{relative.as_posix()}"

    def _lexical(self, path: str) -> tuple[str, ...]:
        if not path.startswith("/") or "\x00" in path:
            raise EnvironmentError("Provider-local file paths must be absolute.", code="environment_request_invalid")
        pure = PurePosixPath(path)
        if any(part in {".", ".."} for part in pure.parts):
            raise EnvironmentError("File path traversal is invalid.", code="environment_request_invalid")
        return tuple(part for part in pure.parts if part != "/")

    def _resolve(self, path: str, *, follow_final: bool = True, require_exists: bool = True) -> Path:
        parts = self._lexical(path)
        candidate = self._root.joinpath(*parts)
        if follow_final:
            try:
                resolved = candidate.resolve(strict=require_exists)
            except FileNotFoundError:
                if require_exists:
                    raise EnvironmentError("File path does not exist.", code="environment_not_found") from None
                parent = candidate.parent.resolve(strict=True)
                resolved = parent / candidate.name
        else:
            parent = candidate.parent.resolve(strict=True) if candidate != self._root else self._root
            resolved = parent / candidate.name if candidate != self._root else self._root
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise EnvironmentError("File path escapes the configured root.", code="environment_denied") from None
        if not follow_final and resolved != self._root and resolved.is_symlink():
            return resolved
        return resolved

    async def resolve_native_directory(self, path: str) -> Path:
        """Resolve a provider-local cwd without exposing native-path fallback publicly."""
        native = await asyncio.to_thread(self._resolve, path)
        if not native.is_dir():
            raise EnvironmentError("Command cwd is not a directory.", code="environment_request_invalid")
        return native

    def _require_writable(self, path: Path) -> None:
        if self._read_only:
            raise EnvironmentError("Direct Local root is read-only.", code="environment_denied")
        if path == self._root:
            raise EnvironmentError("The provider root cannot be mutated.", code="environment_denied")

    def _revision(self, path: Path) -> FileRevision:
        if path.is_symlink():
            digest = hashlib.sha256(os.readlink(path).encode()).hexdigest()
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            stat = path.stat(follow_symlinks=False)
            digest = hashlib.sha256(f"{stat.st_mode}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
        return FileRevision(digest)

    def _check_revision(self, path: Path, expected: FileRevision | None) -> None:
        if expected is None:
            return
        if not path.exists() or self._revision(path) != expected:
            raise EnvironmentError("File revision does not match.", code="environment_conflict")

    def _publish_staged(
        self,
        staged: Path,
        destination: Path,
        mode: FileWriteMode,
        expected: FileRevision | None,
    ) -> None:
        self._require_writable(destination)
        exists = destination.exists() or destination.is_symlink()
        if destination.is_symlink():
            raise EnvironmentError("Writing through a symlink is denied.", code="environment_denied")
        if mode == "create" and exists:
            raise EnvironmentError("Destination already exists.", code="environment_conflict")
        if mode == "replace" and not exists:
            raise EnvironmentError("Destination does not exist.", code="environment_not_found")
        self._check_revision(destination, expected)
        parent = destination.parent.resolve(strict=True)
        try:
            parent.relative_to(self._root)
        except ValueError:
            raise EnvironmentError(
                "Destination parent escaped the configured root.", code="environment_denied"
            ) from None
        os.replace(staged, destination)

    async def read_text(
        self,
        path: str,
        *,
        cursor: FileTextCursor | None = None,
        start_line: int | None = None,
        max_lines: int | None = None,
        max_bytes: int | None = None,
        expected_revision: FileRevision | None = None,
    ) -> FileTextPage:
        native = await asyncio.to_thread(self._resolve, path)
        if not native.is_file():
            raise EnvironmentError("Path is not a regular file.", code="environment_request_invalid")
        revision = await asyncio.to_thread(self._revision, native)
        if expected_revision is not None and expected_revision != revision:
            raise EnvironmentError("File revision does not match.", code="environment_conflict")
        size = await asyncio.to_thread(lambda: native.stat().st_size)
        if size > self._policy.max_text_bytes:
            raise EnvironmentError("Text file exceeds configured limit.", code="environment_too_large")
        data = await asyncio.to_thread(native.read_bytes)
        if len(data) > self._policy.max_text_bytes:
            raise EnvironmentError("Text file exceeds configured limit.", code="environment_too_large")
        limit = min(max_bytes or self._policy.max_text_bytes, self._policy.max_text_bytes)
        text = data.decode("utf-8", errors="strict")
        lines = text.splitlines(keepends=True)
        if cursor is not None:
            try:
                cursor_revision, raw_index = cursor.root.split(":", 1)
                index = int(raw_index)
            except (ValueError, AttributeError):
                raise EnvironmentError("Text cursor is invalid.", code="environment_cursor_invalid") from None
            if cursor_revision != revision.root:
                raise EnvironmentError("Text cursor is stale.", code="environment_conflict")
        else:
            index = max((start_line or 1) - 1, 0)
        selected: list[str] = []
        used = 0
        line_limit = max_lines or len(lines)
        while index + len(selected) < len(lines) and len(selected) < line_limit:
            line = lines[index + len(selected)]
            encoded = line.encode()
            if selected and used + len(encoded) > limit:
                break
            if not selected and len(encoded) > limit:
                selected.append(encoded[:limit].decode("utf-8", errors="ignore"))
                used = limit
                break
            selected.append(line)
            used += len(encoded)
        next_index = index + len(selected)
        complete = next_index >= len(lines)
        output = "".join(selected)
        return FileTextPage(
            path=path,
            revision=revision,
            text=output,
            start=FileTextPosition(line=index + 1, byte_column=0),
            end=FileTextPosition(line=max(next_index, index + 1), byte_column=0),
            next_cursor=None if complete else FileTextCursor(f"{revision.root}:{next_index}"),
            content_complete=complete,
            truncated=not complete,
        )

    async def write_text(
        self,
        path: str,
        text: str,
        *,
        mode: FileWriteMode,
        expected_revision: FileRevision | None = None,
    ) -> FileWriteResult:
        data = text.encode("utf-8")
        if len(data) > self._policy.max_text_bytes:
            raise EnvironmentError("Text write exceeds configured limit.", code="environment_too_large")
        async with self.open_writer(path, mode=mode, expected_revision=expected_revision) as writer:
            await writer.write(data)
            return await writer.commit()

    async def patch_text(
        self,
        path: str,
        patch: str,
        *,
        expected_revision: FileRevision,
    ) -> FilePatchResult:
        page = await self.read_text(path, expected_revision=expected_revision)
        if not page.content_complete:
            raise EnvironmentError("Patch source was not read completely.", code="environment_too_large")
        updated, count = _apply_unified_diff(page.text, patch)
        result = await self.write_text(path, updated, mode="replace", expected_revision=expected_revision)
        return FilePatchResult(
            path=path,
            revision=result.revision,
            hunks_applied=count,
            receipt=result.receipt,
        )

    async def stat(self, path: str) -> FileMetadata:
        native = await asyncio.to_thread(self._resolve, path, follow_final=False)
        if not native.exists() and not native.is_symlink():
            raise EnvironmentError("File path does not exist.", code="environment_not_found")
        stat = await asyncio.to_thread(native.lstat)
        if native.is_symlink():
            kind = "symlink"
        elif native.is_file():
            kind = "file"
        elif native.is_dir():
            kind = "directory"
        else:
            kind = "other"
        return FileMetadata(
            path=path,
            kind=kind,
            size=stat.st_size if kind == "file" else None,
            revision=await asyncio.to_thread(self._revision, native),
            writable=not self._read_only and native != self._root,
        )

    async def list(
        self,
        path: str,
        *,
        cursor: FileQueryCursor | None = None,
        max_results: int,
        include_hidden: bool = False,
    ) -> FileListPage:
        if max_results <= 0 or max_results > self._policy.max_query_results:
            raise EnvironmentError("Invalid list result limit.", code="environment_request_invalid")
        native = await asyncio.to_thread(self._resolve, path)
        if not native.is_dir():
            raise EnvironmentError("List path is not a directory.", code="environment_request_invalid")
        children = await asyncio.to_thread(lambda: sorted(native.iterdir(), key=lambda item: item.name))
        if not include_hidden:
            children = [item for item in children if not item.name.startswith(".")]
        offset = _cursor_offset(cursor)
        selected = children[offset : offset + max_results]
        entries_list: list[FileListEntry] = []
        for item in selected:
            entries_list.append(FileListEntry(metadata=await self.stat(self._logical(item))))
        entries = tuple(entries_list)
        next_offset = offset + len(selected)
        complete = next_offset >= len(children)
        return FileListPage(
            path=path,
            entries=entries,
            next_cursor=None if complete else FileQueryCursor(str(next_offset)),
            content_complete=complete,
        )

    async def query(self, request: FileQueryRequest) -> FileQueryPage:
        root = await asyncio.to_thread(self._resolve, request.root)
        if not root.is_dir():
            raise EnvironmentError("Query root is not a directory.", code="environment_request_invalid")
        offset = _cursor_offset(request.cursor)
        limit = min(request.max_results, self._policy.max_query_results)
        selected, has_more = await asyncio.to_thread(
            _collect_query_page,
            root,
            request.pattern,
            request.recursive,
            request.include_hidden,
            request.kinds,
            offset,
            limit,
        )
        entries_list: list[FileListEntry] = []
        for item in selected:
            entries_list.append(FileListEntry(metadata=await self.stat(self._logical(item))))
        entries = tuple(entries_list)
        next_offset = offset + len(selected)
        return FileQueryPage(
            entries=entries,
            next_cursor=FileQueryCursor(str(next_offset)) if has_more else None,
            content_complete=not has_more,
        )

    async def search_text(self, request: FileTextSearchRequest) -> FileTextSearchPage:
        query = FileQueryRequest(
            root=request.root,
            pattern="*",
            recursive=True,
            include_hidden=request.include_hidden,
            kinds=frozenset({"file"}),
            max_results=self._policy.max_query_results,
        )
        files = (await self.query(query)).entries
        offset = _cursor_offset(request.cursor)
        matches: list[FileTextMatch] = []
        consumed = 0
        needle = request.pattern if request.case_sensitive else request.pattern.casefold()
        regex = re.compile(request.pattern, 0 if request.case_sensitive else re.IGNORECASE) if request.regex else None
        for entry in files:
            try:
                native = await asyncio.to_thread(self._resolve, entry.metadata.path)
                data = await asyncio.to_thread(native.read_bytes)
                if len(data) > min(request.max_bytes, self._policy.max_query_bytes):
                    continue
                text = data.decode("utf-8", errors="strict")
            except (UnicodeDecodeError, EnvironmentError):
                continue
            revision = entry.metadata.revision
            for line_number, line in enumerate(text.splitlines(), start=1):
                candidate = line if request.case_sensitive else line.casefold()
                positions = (
                    [match.start() for match in regex.finditer(line)]
                    if regex is not None
                    else _literal_positions(candidate, needle)
                )
                for position in positions:
                    if consumed < offset:
                        consumed += 1
                        continue
                    matches.append(
                        FileTextMatch(
                            path=entry.metadata.path,
                            revision=revision,
                            line=line_number,
                            byte_column=len(line[:position].encode()),
                            text=line,
                        )
                    )
                    consumed += 1
                    if len(matches) >= request.max_matches:
                        return FileTextSearchPage(
                            matches=tuple(matches),
                            next_cursor=FileQueryCursor(str(consumed)),
                            content_complete=False,
                        )
        return FileTextSearchPage(matches=tuple(matches), next_cursor=None, content_complete=True)

    async def mkdir(self, path: str, *, parents: bool = False, exist_ok: bool = False) -> FileMutationResult:
        native = await asyncio.to_thread(self._resolve, path, follow_final=False, require_exists=False)
        self._require_writable(native)
        await asyncio.to_thread(native.mkdir, parents=parents, exist_ok=exist_ok)
        return FileMutationResult(
            path=path, revision=await asyncio.to_thread(self._revision, native), receipt=self._receipt()
        )

    async def move(
        self,
        source: str,
        destination: str,
        *,
        expected_source_revision: FileRevision | None = None,
        replace: bool = False,
    ) -> FileMutationResult:
        source_native = await asyncio.to_thread(self._resolve, source, follow_final=False)
        destination_native = await asyncio.to_thread(
            self._resolve,
            destination,
            follow_final=False,
            require_exists=False,
        )
        self._require_writable(source_native)
        self._require_writable(destination_native)
        self._check_revision(source_native, expected_source_revision)
        if destination_native.exists() and not replace:
            raise EnvironmentError("Move destination exists.", code="environment_conflict")
        if destination_native.is_symlink():
            raise EnvironmentError("Move destination symlink is denied.", code="environment_denied")
        await asyncio.to_thread(os.replace if replace else os.rename, source_native, destination_native)
        return FileMutationResult(
            path=destination,
            revision=await asyncio.to_thread(self._revision, destination_native),
            receipt=self._receipt(),
        )

    async def remove(
        self,
        path: str,
        *,
        recursive: bool = False,
        expected_revision: FileRevision | None = None,
    ) -> FileMutationResult:
        native = await asyncio.to_thread(self._resolve, path, follow_final=False)
        self._require_writable(native)
        self._check_revision(native, expected_revision)
        if native.is_symlink() or native.is_file():
            await asyncio.to_thread(native.unlink)
        elif native.is_dir():
            if recursive:
                await asyncio.to_thread(shutil.rmtree, native)
            else:
                await asyncio.to_thread(native.rmdir)
        else:
            raise EnvironmentError("Unsupported file kind.", code="environment_unsupported")
        return FileMutationResult(path=path, revision=None, receipt=self._receipt())

    @asynccontextmanager
    async def open_reader(
        self,
        path: str,
        *,
        byte_range: FileByteRange | None = None,
        expected_revision: FileRevision | None = None,
    ) -> AsyncGenerator[AsyncFileReader]:
        native = await asyncio.to_thread(self._resolve, path)
        if not native.is_file():
            raise EnvironmentError("Raw reader source is not a file.", code="environment_request_invalid")
        revision = await asyncio.to_thread(self._revision, native)
        if expected_revision is not None and revision != expected_revision:
            raise EnvironmentError("File revision does not match.", code="environment_conflict")
        selected_range = byte_range or FileByteRange()
        size = await asyncio.to_thread(lambda: native.stat().st_size)
        available = max(size - selected_range.offset, 0)
        requested = available if selected_range.length is None else min(selected_range.length, available)
        if requested > self._policy.max_transfer_bytes:
            raise EnvironmentError("Raw read range exceeds transfer limit.", code="environment_too_large")
        reader = _LocalReader(self, path, native, selected_range, revision)
        try:
            await reader.open()
            yield reader
        finally:
            await reader.close()

    @asynccontextmanager
    async def open_writer(
        self,
        path: str,
        *,
        mode: FileWriteMode,
        expected_revision: FileRevision | None = None,
    ) -> AsyncGenerator[AsyncFileWriter]:
        native = await asyncio.to_thread(self._resolve, path, follow_final=False, require_exists=False)
        self._require_writable(native)
        writer = _LocalWriter(self, path, native, mode, expected_revision)
        try:
            await writer.open()
            yield writer
        finally:
            if not writer._committed:
                await writer.abort()

    async def copy(
        self,
        source: str,
        destination: str,
        *,
        expected_source_revision: FileRevision | None = None,
        expected_destination_revision: FileRevision | None = None,
        replace: bool = False,
        require_atomic_destination: bool = True,
        require_stable_source: bool = False,
    ) -> FileCopyResult:
        del require_atomic_destination
        mode: FileWriteMode = "replace" if replace else "create"
        copied = 0
        async with self.open_reader(source, expected_revision=expected_source_revision) as reader:
            async with self.open_writer(
                destination,
                mode=mode,
                expected_revision=expected_destination_revision,
            ) as writer:
                async for chunk in reader:
                    copied += len(chunk)
                    if copied > self._policy.max_transfer_bytes:
                        raise EnvironmentError("Copy exceeds transfer limit.", code="environment_too_large")
                    await writer.write(chunk)
                completion = reader.completion
                if completion is None:
                    raise EnvironmentError(
                        "Raw source did not complete verification.", code="environment_provider_failure"
                    )
                if require_stable_source and completion.stability != "verified":
                    raise EnvironmentError("Copy source stability is unverified.", code="environment_conflict")
                result = await writer.commit()
        return FileCopyResult(
            path=destination,
            revision=result.revision,
            bytes_copied=copied,
            atomic_destination=True,
            source_stability=completion.stability,
            receipt=result.receipt,
        )


def _collect_query_page(
    root: Path,
    pattern: str,
    recursive: bool,
    include_hidden: bool,
    kinds: frozenset[FileKind] | None,
    offset: int,
    limit: int,
) -> tuple[list[Path], bool]:
    """Walk in deterministic path order while retaining at most one page plus lookahead."""

    def iterate(directory: Path):
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise EnvironmentError(
                "File query could not enumerate a directory.", code="environment_provider_failure"
            ) from exc
        for child in children:
            relative = child.relative_to(root)
            if not include_hidden and any(part.startswith(".") for part in relative.parts):
                continue
            yield child
            if recursive and child.is_dir() and not child.is_symlink():
                yield from iterate(child)

    selected: list[Path] = []
    matched = 0
    for item in iterate(root):
        relative = item.relative_to(root).as_posix()
        if not fnmatch.fnmatch(relative, pattern):
            continue
        kind = _native_kind(item)
        if kinds is not None and kind not in kinds:
            continue
        if matched < offset:
            matched += 1
            continue
        if len(selected) == limit:
            return selected, True
        selected.append(item)
        matched += 1
    return selected, False


def _native_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "file"
    if path.is_dir():
        return "directory"
    return "other"


def _cursor_offset(cursor: FileQueryCursor | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(cursor.root)
    except ValueError:
        raise EnvironmentError("Query cursor is invalid.", code="environment_cursor_invalid") from None
    if value < 0:
        raise EnvironmentError("Query cursor is invalid.", code="environment_cursor_invalid")
    return value


def _literal_positions(value: str, needle: str) -> list[int]:
    if not needle:
        raise EnvironmentError("Search pattern must not be empty.", code="environment_request_invalid")
    positions = []
    start = 0
    while (position := value.find(needle, start)) >= 0:
        positions.append(position)
        start = position + max(len(needle), 1)
    return positions


def _apply_unified_diff(original: str, patch: str) -> tuple[str, int]:
    source = original.splitlines(keepends=True)
    patch_lines = patch.splitlines(keepends=True)
    output: list[str] = []
    source_index = 0
    index = 0
    hunks = 0
    while index < len(patch_lines):
        line = patch_lines[index]
        if line.startswith(("---", "+++")):
            index += 1
            continue
        match = _HUNK.match(line)
        if match is None:
            raise EnvironmentError("Unified diff contains an invalid hunk header.", code="environment_request_invalid")
        old_start = int(match.group(1)) - 1
        if old_start < source_index or old_start > len(source):
            raise EnvironmentError("Unified diff hunk is out of range.", code="environment_conflict")
        output.extend(source[source_index:old_start])
        source_index = old_start
        index += 1
        hunks += 1
        while index < len(patch_lines) and not patch_lines[index].startswith("@@"):
            item = patch_lines[index]
            if item.startswith("\\ No newline"):
                index += 1
                continue
            marker, content = item[:1], item[1:]
            if marker == " ":
                if source_index >= len(source) or source[source_index] != content:
                    raise EnvironmentError("Unified diff context does not match.", code="environment_conflict")
                output.append(content)
                source_index += 1
            elif marker == "-":
                if source_index >= len(source) or source[source_index] != content:
                    raise EnvironmentError("Unified diff deletion does not match.", code="environment_conflict")
                source_index += 1
            elif marker == "+":
                output.append(content)
            else:
                raise EnvironmentError("Unified diff line is invalid.", code="environment_request_invalid")
            index += 1
    if hunks == 0:
        raise EnvironmentError("Unified diff must contain at least one hunk.", code="environment_request_invalid")
    output.extend(source[source_index:])
    return "".join(output), hunks
