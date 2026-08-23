"""Stable virtual-path file facade over current aggregate routing."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from .files import (
    AsyncFileReader,
    AsyncFileWriter,
    FileByteRange,
    FileCopyResult,
    FileListEntry,
    FileListPage,
    FileMetadata,
    FileMutationResult,
    FilePatchResult,
    FileQueryPage,
    FileQueryRequest,
    FileReadStability,
    FileRevision,
    FileTextPage,
    FileTextSearchPage,
    FileTextSearchRequest,
    FileWriteMode,
    FileWriteResult,
)
from .models import EnvironmentAction, EnvironmentError, EnvironmentPath


@dataclass(frozen=True, slots=True)
class _PreparedFile:
    selected: EnvironmentPath
    observed_generation: str
    backend: Any
    validate_result: Callable[[Any], None]


@dataclass(frozen=True, slots=True)
class _FileResultProvenance:
    binding_id: str
    binding_revision: int
    observed_generation: str
    paths: tuple[tuple[str, str], ...]

    def provider_path(self, virtual_path: str) -> str:
        for candidate, provider_path in self.paths:
            if candidate == virtual_path:
                return provider_path
        raise EnvironmentError(
            "File result path is outside its captured provider provenance.",
            code="environment_provider_failure",
        )


PrepareFile = Callable[
    [str, EnvironmentAction],
    AbstractAsyncContextManager[_PreparedFile],
]
VirtualizePath = Callable[[EnvironmentPath, str], str]


class _ValidatingFileWriter:
    def __init__(
        self,
        delegate: AsyncFileWriter,
        validate: Callable[[Any], None],
        project_commit: Callable[[FileWriteResult], FileWriteResult],
    ) -> None:
        self._delegate = delegate
        self._validate = validate
        self._project_commit = project_commit

    async def write(self, chunk: bytes) -> None:
        await self._delegate.write(chunk)

    async def commit(self) -> FileWriteResult:
        result = await self._delegate.commit()
        self._validate(result)
        return self._project_commit(result)

    async def abort(self) -> None:
        await self._delegate.abort()


class VirtualFileOperator:
    def __init__(self, prepare: PrepareFile, virtualize: VirtualizePath) -> None:
        self._prepare = prepare
        self._virtualize = virtualize
        self._captured_provenance: ContextVar[_FileResultProvenance | None] = ContextVar(
            f"environment_file_provenance_{id(self)}",
            default=None,
        )

    def begin_result_capture(self) -> Token[_FileResultProvenance | None]:
        return self._captured_provenance.set(None)

    def result_provenance(self) -> _FileResultProvenance:
        provenance = self._captured_provenance.get()
        if provenance is None:
            raise EnvironmentError(
                "File operation did not publish result provenance.",
                code="environment_provider_failure",
            )
        return provenance

    def reset_result_capture(self, token: Token[_FileResultProvenance | None]) -> None:
        self._captured_provenance.reset(token)

    def _record(self, prepared: _PreparedFile, paths: tuple[tuple[str, str], ...]) -> None:
        self._captured_provenance.set(
            _FileResultProvenance(
                binding_id=prepared.selected.binding_id,
                binding_revision=prepared.selected.binding_revision,
                observed_generation=prepared.observed_generation,
                paths=paths,
            )
        )

    async def read_text(self, path: str, **kwargs: Any) -> FileTextPage:
        async with self._prepare(path, EnvironmentAction.FILE_READ_TEXT) as prepared:
            result = await prepared.backend.read_text(prepared.selected.path, **kwargs)
            prepared.validate_result(result)
            projected = result.model_copy(update={"path": path})
            self._record(prepared, ((path, prepared.selected.path),))
            return projected

    async def write_text(
        self,
        path: str,
        text: str,
        *,
        mode: FileWriteMode,
        expected_revision: FileRevision | None = None,
    ) -> FileWriteResult:
        async with self._prepare(path, EnvironmentAction.FILE_WRITE_TEXT) as prepared:
            result = await prepared.backend.write_text(
                prepared.selected.path,
                text,
                mode=mode,
                expected_revision=expected_revision,
            )
            prepared.validate_result(result)
            projected = result.model_copy(update={"path": path})
            self._record(prepared, ((path, prepared.selected.path),))
            return projected

    async def patch_text(
        self,
        path: str,
        patch: str,
        *,
        expected_revision: FileRevision,
    ) -> FilePatchResult:
        async with self._prepare(path, EnvironmentAction.FILE_PATCH_TEXT) as prepared:
            result = await prepared.backend.patch_text(
                prepared.selected.path,
                patch,
                expected_revision=expected_revision,
            )
            prepared.validate_result(result)
            projected = result.model_copy(update={"path": path})
            self._record(prepared, ((path, prepared.selected.path),))
            return projected

    async def stat(self, path: str) -> FileMetadata:
        async with self._prepare(path, EnvironmentAction.FILE_STAT) as prepared:
            result = await prepared.backend.stat(prepared.selected.path)
            prepared.validate_result(result)
            projected = result.model_copy(update={"path": path})
            self._record(prepared, ((path, prepared.selected.path),))
            return projected

    async def list(self, path: str, **kwargs: Any) -> FileListPage:
        async with self._prepare(path, EnvironmentAction.FILE_LIST) as prepared:
            result = await prepared.backend.list(prepared.selected.path, **kwargs)
            prepared.validate_result(result)
            entries: list[FileListEntry] = []
            paths = [(path, prepared.selected.path)]
            for entry in result.entries:
                virtual_path = self._virtualize(prepared.selected, entry.metadata.path)
                paths.append((virtual_path, entry.metadata.path))
                entries.append(FileListEntry(metadata=entry.metadata.model_copy(update={"path": virtual_path})))
            projected = result.model_copy(update={"path": path, "entries": tuple(entries)})
            self._record(prepared, tuple(paths))
            return projected

    async def query(self, request: FileQueryRequest) -> FileQueryPage:
        async with self._prepare(request.root, EnvironmentAction.FILE_QUERY) as prepared:
            result = await prepared.backend.query(request.model_copy(update={"root": prepared.selected.path}))
            prepared.validate_result(result)
            entries: list[FileListEntry] = []
            paths = [(request.root, prepared.selected.path)]
            for entry in result.entries:
                virtual_path = self._virtualize(prepared.selected, entry.metadata.path)
                paths.append((virtual_path, entry.metadata.path))
                entries.append(FileListEntry(metadata=entry.metadata.model_copy(update={"path": virtual_path})))
            projected = result.model_copy(update={"entries": tuple(entries)})
            self._record(prepared, tuple(paths))
            return projected

    async def search_text(self, request: FileTextSearchRequest) -> FileTextSearchPage:
        async with self._prepare(request.root, EnvironmentAction.FILE_SEARCH_TEXT) as prepared:
            result = await prepared.backend.search_text(request.model_copy(update={"root": prepared.selected.path}))
            prepared.validate_result(result)
            matches = []
            paths = [(request.root, prepared.selected.path)]
            for match in result.matches:
                virtual_path = self._virtualize(prepared.selected, match.path)
                paths.append((virtual_path, match.path))
                matches.append(match.model_copy(update={"path": virtual_path}))
            projected = result.model_copy(update={"matches": tuple(matches)})
            self._record(prepared, tuple(paths))
            return projected

    async def mkdir(self, path: str, **kwargs: Any) -> FileMutationResult:
        async with self._prepare(path, EnvironmentAction.FILE_MKDIR) as prepared:
            result = await prepared.backend.mkdir(prepared.selected.path, **kwargs)
            prepared.validate_result(result)
            projected = result.model_copy(update={"path": path})
            self._record(prepared, ((path, prepared.selected.path),))
            return projected

    async def move(
        self,
        source: str,
        destination: str,
        *,
        expected_source_revision: FileRevision | None = None,
        replace: bool = False,
    ) -> FileMutationResult:
        async with self._prepare(source, EnvironmentAction.FILE_MOVE) as source_file:
            async with self._prepare(destination, EnvironmentAction.FILE_MOVE) as destination_file:
                if (
                    source_file.selected.binding_id != destination_file.selected.binding_id
                    or source_file.backend is not destination_file.backend
                ):
                    raise EnvironmentError(
                        "Cross-binding move must be expressed as copy and separately authorized remove.",
                        code="environment_unsupported",
                    )
                result = await source_file.backend.move(
                    source_file.selected.path,
                    destination_file.selected.path,
                    expected_source_revision=expected_source_revision,
                    replace=replace,
                )
                destination_file.validate_result(result)
                projected = result.model_copy(update={"path": destination})
                self._record(destination_file, ((destination, destination_file.selected.path),))
                return projected

    async def remove(self, path: str, **kwargs: Any) -> FileMutationResult:
        async with self._prepare(path, EnvironmentAction.FILE_REMOVE) as prepared:
            result = await prepared.backend.remove(prepared.selected.path, **kwargs)
            prepared.validate_result(result)
            projected = result.model_copy(update={"path": path})
            self._record(prepared, ((path, prepared.selected.path),))
            return projected

    @asynccontextmanager
    async def open_reader(
        self,
        path: str,
        *,
        byte_range: FileByteRange | None = None,
        expected_revision: FileRevision | None = None,
    ) -> AsyncGenerator[AsyncFileReader]:
        async with self._prepare(path, EnvironmentAction.FILE_OPEN_READER) as prepared:
            async with prepared.backend.open_reader(
                prepared.selected.path,
                byte_range=byte_range,
                expected_revision=expected_revision,
            ) as reader:
                yield reader

    @asynccontextmanager
    async def open_writer(
        self,
        path: str,
        *,
        mode: FileWriteMode,
        expected_revision: FileRevision | None = None,
    ) -> AsyncGenerator[AsyncFileWriter]:
        async with self._prepare(path, EnvironmentAction.FILE_OPEN_WRITER) as prepared:
            async with prepared.backend.open_writer(
                prepared.selected.path,
                mode=mode,
                expected_revision=expected_revision,
            ) as writer:

                def project_commit(result: FileWriteResult) -> FileWriteResult:
                    self._record(prepared, ((path, prepared.selected.path),))
                    return result.model_copy(update={"path": path})

                yield _ValidatingFileWriter(writer, prepared.validate_result, project_commit)

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
        async with self._prepare(source, EnvironmentAction.FILE_COPY_SOURCE) as source_file:
            async with self._prepare(destination, EnvironmentAction.FILE_COPY_DESTINATION) as destination_file:
                if (
                    source_file.selected.binding_id == destination_file.selected.binding_id
                    and source_file.backend is destination_file.backend
                ):
                    result = await source_file.backend.copy(
                        source_file.selected.path,
                        destination_file.selected.path,
                        expected_source_revision=expected_source_revision,
                        expected_destination_revision=expected_destination_revision,
                        replace=replace,
                        require_atomic_destination=require_atomic_destination,
                        require_stable_source=require_stable_source,
                    )
                    destination_file.validate_result(result)
                    projected = result.model_copy(update={"path": destination})
                    self._record(destination_file, ((destination, destination_file.selected.path),))
                    return projected

                copied = 0
                stability: FileReadStability = "unverified"
                async with destination_file.backend.open_writer(
                    destination_file.selected.path,
                    mode="replace" if replace else "create",
                    expected_revision=expected_destination_revision,
                ) as writer:
                    async with source_file.backend.open_reader(
                        source_file.selected.path,
                        expected_revision=expected_source_revision,
                    ) as reader:
                        async for chunk in reader:
                            copied += len(chunk)
                            await writer.write(chunk)
                        completion = reader.completion
                        if completion is None:
                            raise EnvironmentError("Copy source did not complete.", code="environment_provider_failure")
                        stability = completion.stability
                        if require_stable_source and stability != "verified":
                            raise EnvironmentError("Copy source is not stable.", code="environment_conflict")
                    result = await writer.commit()
                    destination_file.validate_result(result)
                projected = FileCopyResult(
                    path=destination,
                    revision=result.revision,
                    bytes_copied=copied,
                    atomic_destination=True,
                    source_stability=stability,
                    receipt=result.receipt,
                )
                destination_file.validate_result(projected)
                self._record(destination_file, ((destination, destination_file.selected.path),))
                return projected
