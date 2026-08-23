"""Provider-neutral bounded file values and semantic protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, RootModel

from .models import EnvironmentOperationReceipt

type FileWriteMode = Literal["create", "replace", "upsert", "append"]
type FileReadStability = Literal["verified", "unverified"]
type FileKind = Literal["file", "directory", "symlink", "other"]


class FileRevision(RootModel[str]):
    model_config = ConfigDict(frozen=True)


class FileTextCursor(RootModel[str]):
    model_config = ConfigDict(frozen=True)


class FileQueryCursor(RootModel[str]):
    model_config = ConfigDict(frozen=True)


class FileByteRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    offset: int = Field(default=0, ge=0)
    length: int | None = Field(default=None, ge=0)


class FileTextPosition(BaseModel):
    model_config = ConfigDict(frozen=True)

    line: int = Field(ge=1)
    byte_column: int = Field(ge=0)


class FileTextPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    revision: FileRevision | None
    text: str
    start: FileTextPosition
    end: FileTextPosition
    next_cursor: FileTextCursor | None
    content_complete: bool
    truncated: bool


class FileContentDigest(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: Literal["sha256"] = "sha256"
    value: str


class FileReadCompletion(BaseModel):
    model_config = ConfigDict(frozen=True)

    range_start: int = Field(ge=0)
    range_end: int = Field(ge=0)
    bytes_read: int = Field(ge=0)
    digest: FileContentDigest
    source_eof_at_end: bool
    stability: FileReadStability


class FileWriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    revision: FileRevision | None
    bytes_written: int = Field(ge=0)
    receipt: EnvironmentOperationReceipt


class FilePatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    revision: FileRevision | None
    hunks_applied: int = Field(ge=0)
    receipt: EnvironmentOperationReceipt


class FileCopyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    revision: FileRevision | None
    bytes_copied: int = Field(ge=0)
    atomic_destination: bool
    source_stability: FileReadStability
    receipt: EnvironmentOperationReceipt


class FileMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    kind: FileKind
    size: int | None = Field(default=None, ge=0)
    revision: FileRevision | None
    writable: bool


class FileListEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    metadata: FileMetadata


class FileListPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    entries: tuple[FileListEntry, ...]
    next_cursor: FileQueryCursor | None
    content_complete: bool


class FileQueryRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: str
    pattern: str
    recursive: bool = True
    include_hidden: bool = False
    kinds: frozenset[FileKind] | None = None
    max_results: int = Field(gt=0)
    cursor: FileQueryCursor | None = None


class FileQueryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    entries: tuple[FileListEntry, ...]
    next_cursor: FileQueryCursor | None
    content_complete: bool


class FileTextMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    revision: FileRevision | None
    line: int = Field(ge=1)
    byte_column: int = Field(ge=0)
    text: str


class FileTextSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: str
    pattern: str
    regex: bool = False
    case_sensitive: bool = True
    include_hidden: bool = False
    max_matches: int = Field(gt=0)
    max_bytes: int = Field(gt=0)
    cursor: FileQueryCursor | None = None


class FileTextSearchPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    matches: tuple[FileTextMatch, ...]
    next_cursor: FileQueryCursor | None
    content_complete: bool


class FileMutationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    revision: FileRevision | None
    receipt: EnvironmentOperationReceipt


class AsyncFileReader(Protocol):
    def __aiter__(self) -> AsyncIterator[bytes]: ...

    @property
    def completion(self) -> FileReadCompletion | None: ...


class AsyncFileWriter(Protocol):
    async def write(self, chunk: bytes) -> None: ...

    async def commit(self) -> FileWriteResult: ...

    async def abort(self) -> None: ...


class FileOperator(Protocol):
    async def read_text(
        self,
        path: str,
        *,
        cursor: FileTextCursor | None = None,
        start_line: int | None = None,
        max_lines: int | None = None,
        max_bytes: int | None = None,
        expected_revision: FileRevision | None = None,
    ) -> FileTextPage: ...

    async def write_text(
        self,
        path: str,
        text: str,
        *,
        mode: FileWriteMode,
        expected_revision: FileRevision | None = None,
    ) -> FileWriteResult: ...

    async def patch_text(
        self,
        path: str,
        patch: str,
        *,
        expected_revision: FileRevision,
    ) -> FilePatchResult: ...

    async def stat(self, path: str) -> FileMetadata: ...

    async def list(
        self,
        path: str,
        *,
        cursor: FileQueryCursor | None = None,
        max_results: int,
        include_hidden: bool = False,
    ) -> FileListPage: ...

    async def query(self, request: FileQueryRequest) -> FileQueryPage: ...

    async def search_text(self, request: FileTextSearchRequest) -> FileTextSearchPage: ...

    async def mkdir(
        self,
        path: str,
        *,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> FileMutationResult: ...

    async def move(
        self,
        source: str,
        destination: str,
        *,
        expected_source_revision: FileRevision | None = None,
        replace: bool = False,
    ) -> FileMutationResult: ...

    async def remove(
        self,
        path: str,
        *,
        recursive: bool = False,
        expected_revision: FileRevision | None = None,
    ) -> FileMutationResult: ...

    def open_reader(
        self,
        path: str,
        *,
        byte_range: FileByteRange | None = None,
        expected_revision: FileRevision | None = None,
    ) -> AbstractAsyncContextManager[AsyncFileReader]: ...

    def open_writer(
        self,
        path: str,
        *,
        mode: FileWriteMode,
        expected_revision: FileRevision | None = None,
    ) -> AbstractAsyncContextManager[AsyncFileWriter]: ...

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
    ) -> FileCopyResult: ...
