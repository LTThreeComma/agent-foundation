"""Reusable model-facing file Toolset over the provider-neutral FileOperator."""

from __future__ import annotations

import asyncio
import fnmatch
import posixpath
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai import BinaryContent, RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from converge_agent_harness.context import AgentContext
from converge_agent_harness.environment.files import (
    FileMetadata,
    FileOperator,
    FileQueryRequest,
    FileTextSearchRequest,
)
from converge_agent_harness.environment.models import EnvironmentError
from converge_agent_harness.tools.metadata import (
    HarnessTool,
    HarnessToolMetadata,
    ToolEffect,
    ToolOutputPolicy,
    ToolResourceResolver,
)

from ._results import ToolFailure
from .file_results import (
    FileEditResult,
    FileGlobResult,
    FileGrepResult,
    FileListResult,
    FileMetadataProjection,
    FileViewResult,
    FileWriteResult,
)

_MAX_MODEL_TEXT_BYTES = 256 * 1024
_MAX_MODEL_EDIT_BYTES = 16 * 1024 * 1024
_MAX_MODEL_RESULTS = 1_000
_MAX_MODEL_MEDIA_BYTES = 16 * 1024 * 1024

type _UnlimitedOrPositiveResults = Literal[-1] | Annotated[int, Field(gt=0, le=_MAX_MODEL_RESULTS)]

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
}


class FileTextEdit(BaseModel):
    """One exact replacement in a multi-edit call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    old_string: str = Field(description="Text to replace (must match exactly, including whitespace and indentation)")
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(default=False, description="Replace every occurrence instead of one unique match")


class FileToolset:
    """Standard file-tool semantics reusable with any FileOperator implementation."""

    def __init__(
        self,
        files: FileOperator,
        *,
        resource_resolver: Callable[[str], ToolResourceResolver] | None = None,
        execution_guard: Callable[[], None] | None = None,
    ) -> None:
        self._files = files
        self._resource_resolver = resource_resolver
        self._execution_guard = execution_guard
        self._mutation_lock = asyncio.Lock()

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=(
                self._tool(
                    self.view,
                    "filesystem.view",
                    {"read"},
                    "read_only",
                    name="view",
                    description="Read bounded text segments or attach common image, video, and audio files natively.",
                    max_output_bytes=_MAX_MODEL_MEDIA_BYTES,
                ),
                self._tool(
                    self.write,
                    "filesystem.write",
                    {"write"},
                    "none",
                    name="write",
                    description="Write, overwrite, or append text to a file.",
                ),
                self._tool(
                    self.edit,
                    "filesystem.edit",
                    {"read", "write"},
                    "none",
                    name="edit",
                    description="Perform one exact string replacement; an empty old_string creates a file.",
                ),
                self._tool(
                    self.multi_edit,
                    "filesystem.multi_edit",
                    {"read", "write"},
                    "none",
                    name="multi_edit",
                    description="Apply multiple exact replacements to one file in sequence.",
                ),
                self._tool(
                    self.ls,
                    "filesystem.ls",
                    {"read"},
                    "read_only",
                    name="ls",
                    description="List one directory with bounded file metadata.",
                ),
                self._tool(
                    self.glob,
                    "filesystem.glob",
                    {"read"},
                    "read_only",
                    name="glob",
                    description="Find paths by glob pattern; bare patterns match recursively.",
                ),
                self._tool(
                    self.grep,
                    "filesystem.grep",
                    {"read"},
                    "read_only",
                    name="grep",
                    description="Search text file contents with a regular expression.",
                ),
            ),
            id="converge-file-tools",
        )

    def _tool(
        self,
        function: Callable[..., object],
        tool_id: str,
        effects: set[ToolEffect],
        idempotency: Literal["none", "read_only"],
        *,
        name: str,
        description: str,
        max_output_bytes: int = 4 * 1024 * 1024,
    ) -> HarnessTool:
        return HarnessTool(
            function,
            harness_metadata=HarnessToolMetadata(
                tool_id=tool_id,
                effects=frozenset(effects),
                credential_audiences=(),
                idempotency=idempotency,
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=256 * 1024,
                    max_output_bytes=max_output_bytes,
                    overflow="truncate",
                    redact=True,
                ),
                resource_resolver=(self._resource_resolver(tool_id) if self._resource_resolver is not None else None),
            ),
            name=name,
            description=description,
        )

    async def view(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[str, Field(description="Logical path to the file to read")],
        line_offset: Annotated[int | None, Field(default=None, ge=0)] = None,
        line_limit: Annotated[int, Field(default=300, gt=0, le=_MAX_MODEL_RESULTS)] = 300,
        max_line_length: Annotated[int, Field(default=2_000, gt=0, le=_MAX_MODEL_TEXT_BYTES)] = 2_000,
        instructions: Annotated[
            str | None,
            Field(default=None, max_length=64 * 1024, description="Focused media analysis instructions"),
        ] = None,
    ) -> FileViewResult:
        """Read bounded text or attach a common media file natively."""
        extension = posixpath.splitext(file_path)[1].casefold()
        if extension == ".pdf":
            return {
                "ok": False,
                "error": {
                    "code": "document_conversion_required",
                    "retry_hint": "request_change",
                    "details": {"tool": "pdf_convert"},
                },
            }
        media_type = _MEDIA_TYPES.get(extension)
        if media_type is not None:
            try:
                self._guard_execution()
                metadata = await self._files.stat(file_path)
                if metadata.kind != "file":
                    raise EnvironmentError(
                        "Media view source is not a file.",
                        code="environment_request_invalid",
                    )
                if metadata.size is not None and metadata.size > _MAX_MODEL_MEDIA_BYTES:
                    raise EnvironmentError(
                        "Media file exceeds the model view limit.",
                        code="environment_too_large",
                    )
                self._guard_execution()
                data = await self._files.read_bytes(file_path)
                message = f"The {media_type} file {file_path} is attached in the user message."
                if instructions is not None and instructions.strip():
                    message = f"{message}\n\nAnalysis instructions:\n{instructions.strip()}"
                return ToolReturn(
                    return_value=message,
                    content=[BinaryContent(data=data, media_type=media_type)],
                )
            except EnvironmentError as exc:
                return _environment_error_result(exc)
        return await self._execute(
            lambda: self._files.read_text(
                file_path,
                line_offset=line_offset or 0,
                line_limit=line_limit,
                max_line_length=max_line_length,
            ),
            lambda result: {
                "file_path": result.path,
                "content": result.text,
                "line_offset": result.line_offset,
                "lines_read": result.lines_read,
                "has_more": result.has_more,
                "truncated_lines": list(result.truncated_lines),
            },
        )

    async def write(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[str, Field(description="Logical path to the file to write")],
        content: Annotated[str, Field(description="Complete text to write or text to append")],
        mode: Annotated[Literal["w", "a"], Field(default="w")] = "w",
    ) -> FileWriteResult:
        """Write or append text, creating parent directories when needed."""

        async def operation():
            async with self._mutation_lock:
                self._guard_execution()
                await self._ensure_parent(file_path)
                self._guard_execution()
                return await self._files.write_text(
                    file_path,
                    content,
                    mode="append" if mode == "a" else "upsert",
                )

        return await self._execute(
            operation,
            lambda result: {"file_path": result.path, "bytes_written": result.bytes_written},
        )

    async def edit(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[str, Field(description="Logical path to the file to edit")],
        old_string: Annotated[
            str,
            Field(description="Exact text to replace; an empty value creates a new file"),
        ],
        new_string: Annotated[str, Field(description="Replacement text")],
        replace_all: Annotated[bool, Field(default=False)] = False,
    ) -> FileEditResult:
        """Apply one exact replacement without requiring a unified diff."""
        return await self._apply_edits(
            ctx,
            file_path,
            (FileTextEdit(old_string=old_string, new_string=new_string, replace_all=replace_all),),
        )

    async def multi_edit(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[str, Field(description="Logical path to the file to edit")],
        edits: Annotated[
            Sequence[FileTextEdit],
            Field(description="Exact replacements applied in order", min_length=1, max_length=256),
        ],
    ) -> FileEditResult:
        """Validate all replacements in memory, then publish one final file write."""
        return await self._apply_edits(ctx, file_path, tuple(edits))

    async def ls(
        self,
        ctx: RunContext[AgentContext],
        path: Annotated[str, Field(description="Logical directory path")],
        ignore: Annotated[Sequence[str] | None, Field(default=None)] = None,
        max_results: _UnlimitedOrPositiveResults = 500,
    ) -> FileListResult:
        """List one directory and optionally omit matching entry names."""
        limit = _MAX_MODEL_RESULTS if max_results == -1 else max_results

        def project(result) -> Mapping[str, JsonValue]:
            entries = [
                self._project_metadata(entry)
                for entry in result.entries
                if not ignore or not any(fnmatch.fnmatch(posixpath.basename(entry.path), pattern) for pattern in ignore)
            ]
            return {
                "path": path,
                "entries": cast(JsonValue, entries),
                "count": len(entries),
                "has_more": result.has_more,
            }

        return await self._execute(
            lambda: self._files.list(
                path,
                offset=0,
                max_results=limit,
                include_hidden=False,
            ),
            project,
        )

    async def glob(
        self,
        ctx: RunContext[AgentContext],
        pattern: Annotated[str, Field(description="Glob pattern; bare patterns match recursively")],
        root: Annotated[str, Field(default=".", description="Logical root to search from")] = ".",
        include_ignored: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Compatibility parameter; provider-neutral Environments do not interpret repository ignore files"
                ),
            ),
        ] = False,
        include_hidden: Annotated[bool, Field(default=False)] = False,
        max_results: _UnlimitedOrPositiveResults = 500,
    ) -> FileGlobResult:
        """Find matching paths through the provider-neutral Environment query operation."""
        del include_ignored  # Environment providers expose no repository-specific ignore authority.
        limit = _MAX_MODEL_RESULTS if max_results == -1 else max_results
        request = FileQueryRequest(
            root=root,
            pattern=pattern,
            recursive=True,
            include_hidden=include_hidden,
            offset=0,
            max_results=limit,
        )
        return await self._execute(
            lambda: self._files.query(request),
            lambda result: {
                "files": [entry.path for entry in result.entries],
                "count": len(result.entries),
                "has_more": result.has_more,
            },
        )

    async def grep(
        self,
        ctx: RunContext[AgentContext],
        pattern: Annotated[str, Field(description="Regular expression to search for")],
        root: Annotated[str, Field(default=".", description="Logical root to search from")] = ".",
        include: Annotated[str, Field(default="**/*", description="Glob selecting files to include")] = "**/*",
        include_ignored: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Compatibility parameter; provider-neutral Environments do not interpret repository ignore files"
                ),
            ),
        ] = False,
        include_hidden: Annotated[bool, Field(default=False)] = False,
        context_lines: Annotated[int, Field(default=2, ge=0, le=20)] = 2,
        max_results: _UnlimitedOrPositiveResults = 100,
        max_matches_per_file: _UnlimitedOrPositiveResults = 20,
        max_files: _UnlimitedOrPositiveResults = 50,
    ) -> FileGrepResult:
        """Search bounded UTF-8 text and return reference-compatible match records."""
        del include_ignored
        result_limit = _MAX_MODEL_RESULTS if max_results == -1 else max_results
        request = FileTextSearchRequest(
            root=root,
            pattern=pattern,
            regex=True,
            case_sensitive=True,
            include_hidden=include_hidden,
            offset=0,
            max_matches=_MAX_MODEL_RESULTS,
            max_line_length=2_000,
        )

        async def operation():
            result = await self._files.search_text(request)
            selected = []
            per_file: dict[str, int] = {}
            files: set[str] = set()
            for match in result.matches:
                if not _matches_file_glob(match.path, root=root, pattern=include):
                    continue
                if max_files != -1 and match.path not in files and len(files) >= max_files:
                    continue
                count = per_file.get(match.path, 0)
                if max_matches_per_file != -1 and count >= max_matches_per_file:
                    continue
                if len(selected) >= result_limit:
                    break
                files.add(match.path)
                per_file[match.path] = count + 1
                selected.append(match)

            widest_context = context_lines * 2 + 1
            max_context_line_length = min(
                2_000,
                max(
                    1,
                    _MAX_MODEL_TEXT_BYTES // max(1, 4 * len(selected) * widest_context),
                ),
            )
            projected = []
            for match in selected:
                context_start_line = max(1, match.line - context_lines)
                context_end_line = match.line + context_lines
                self._guard_execution()
                context = await self._files.read_text(
                    match.path,
                    line_offset=context_start_line - 1,
                    line_limit=context_end_line - context_start_line + 1,
                    max_line_length=max_context_line_length,
                )
                projected.append((match, context, context_start_line))
            return result, projected

        def project(value) -> Mapping[str, JsonValue]:
            result, projected = value
            matches: dict[str, JsonValue] = {}
            for match, context, context_start_line in projected:
                key = f"{match.path}:{match.line}"
                matches[key] = cast(
                    JsonValue,
                    {
                        "file_path": match.path,
                        "line_number": match.line,
                        "matching_line": match.text,
                        "text_truncated": match.text_truncated,
                        "context": context.text,
                        "context_start_line": context_start_line,
                    },
                )
            return {
                "matches": cast(JsonValue, matches),
                "count": len(matches),
                "has_more": result.has_more or len(matches) >= result_limit,
            }

        return await self._execute(operation, project)

    async def _apply_edits(
        self,
        ctx: RunContext[AgentContext],
        file_path: str,
        edits: tuple[FileTextEdit, ...],
    ) -> FileEditResult:
        async def operation():
            async with self._mutation_lock:
                self._guard_execution()
                create = edits[0].old_string == ""
                if create:
                    content = edits[0].new_string
                    pending = edits[1:]
                    write_mode = "create"
                else:
                    data = await self._files.read_bytes(file_path)
                    try:
                        content = data.decode("utf-8", errors="strict")
                    except UnicodeDecodeError as exc:
                        raise EnvironmentError(
                            "Edit target is not valid UTF-8 text.",
                            code="environment_unsupported",
                        ) from exc
                    pending = edits
                    write_mode = "replace"

                content = await asyncio.to_thread(
                    _apply_text_edits,
                    content,
                    pending,
                    2 if create else 1,
                )
                if create:
                    await self._ensure_parent(file_path)
                self._guard_execution()
                return await self._files.write_text(file_path, content, mode=write_mode)

        return await self._execute(
            operation,
            lambda result: {
                "file_path": result.path,
                "edits_applied": len(edits),
                "bytes_written": result.bytes_written,
                "created": edits[0].old_string == "",
            },
        )

    async def _ensure_parent(self, file_path: str) -> None:
        parent = posixpath.dirname(file_path)
        parts = tuple(part for part in parent.split("/") if part)
        is_binding_root = parent == "/workspace" or (len(parts) == 2 and parts[0] == "environment")
        if parent and parent != "." and not is_binding_root:
            self._guard_execution()
            await self._files.mkdir(parent, parents=True, exist_ok=True)

    @staticmethod
    def _project_metadata(metadata: FileMetadata) -> FileMetadataProjection:
        return {
            "path": metadata.path,
            "kind": metadata.kind,
            "size": metadata.size,
            "writable": metadata.writable,
        }

    async def _execute(
        self,
        operation: Callable[[], Awaitable[Any]],
        project: Callable[[Any], Mapping[str, object]],
    ) -> Any:
        try:
            self._guard_execution()
            result = await operation()
            return {"ok": True, **dict(project(result))}
        except EnvironmentError as exc:
            return _environment_error_result(exc)

    def _guard_execution(self) -> None:
        if self._execution_guard is not None:
            self._execution_guard()


def _environment_error_result(exc: EnvironmentError) -> ToolFailure:
    safe_details: dict[str, JsonValue] = {}
    timeout = exc.details.get("timeout_seconds")
    if isinstance(timeout, int | float) and not isinstance(timeout, bool):
        safe_details["timeout_seconds"] = timeout
    missing = exc.details.get("missing")
    if isinstance(missing, list) and all(isinstance(item, str) for item in missing):
        safe_details["missing"] = cast(JsonValue, list(missing))
    for key in ("edit_index", "occurrences"):
        value = exc.details.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            safe_details[key] = value
    return {
        "ok": False,
        "error": {
            "code": exc.code,
            "retry_hint": exc.retry_hint,
            "details": safe_details,
        },
    }


def _apply_text_edits(
    content: str,
    edits: tuple[FileTextEdit, ...],
    start_index: int = 1,
) -> str:
    if len(content.encode("utf-8")) > _MAX_MODEL_EDIT_BYTES:
        raise EnvironmentError("Edit target exceeds the model edit limit.", code="environment_too_large")
    for index, item in enumerate(edits, start=start_index):
        if item.old_string == "":
            raise EnvironmentError(
                "Only the first edit may use an empty old_string.",
                code="environment_request_invalid",
                details={"edit_index": index},
            )
        occurrences = content.count(item.old_string)
        if occurrences == 0:
            raise EnvironmentError(
                "Exact edit text was not found.",
                code="environment_edit_not_found",
                details={"edit_index": index},
            )
        if occurrences > 1 and not item.replace_all:
            raise EnvironmentError(
                "Exact edit text is not unique; add context or set replace_all.",
                code="environment_edit_ambiguous",
                details={"edit_index": index, "occurrences": occurrences},
            )
        content = content.replace(
            item.old_string,
            item.new_string,
            -1 if item.replace_all else 1,
        )
        if len(content.encode("utf-8")) > _MAX_MODEL_EDIT_BYTES:
            raise EnvironmentError("Edited file exceeds the model edit limit.", code="environment_too_large")
    return content


def _matches_file_glob(path: str, *, root: str, pattern: str) -> bool:
    normalized_path = path.removeprefix("/")
    normalized_root = root.strip("/")
    if normalized_root and normalized_path.startswith(f"{normalized_root}/"):
        normalized_path = normalized_path[len(normalized_root) + 1 :]
    if pattern in {"*", "**", "**/*"}:
        return True
    if "/" not in pattern:
        return fnmatch.fnmatch(posixpath.basename(normalized_path), pattern)
    return fnmatch.fnmatch(normalized_path, pattern)


__all__ = ["FileTextEdit", "FileToolset"]
