"""Reusable model-facing file Toolset over the provider-neutral FileOperator."""

from __future__ import annotations

import asyncio
import fnmatch
import posixpath
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai import BinaryContent, RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from converge_agent_harness._json import redact_json
from converge_agent_harness.context import AgentContext
from converge_agent_harness.environment.files import (
    FileMetadata,
    FileOperator,
    FileQueryRequest,
    FileTextSearchRequest,
)
from converge_agent_harness.environment.models import EnvironmentError
from converge_agent_harness.environment.providers import FileScopeProvider
from converge_agent_harness.tools.metadata import (
    HarnessTool,
    HarnessToolMetadata,
    ToolEffect,
    ToolOutputPolicy,
    ToolResourceResolver,
)

from ._results import ToolFailure
from ._scoped_files import ScopedFileAccess
from .file_results import (
    FileEditResult,
    FileGlobResult,
    FileGrepResult,
    FileListResult,
    FileMetadataProjection,
    FileViewResult,
    FileWriteResult,
)
from .output import (
    FINAL_TOOL_OUTPUT_HARD_CHARS,
    acknowledge_tool_output,
    continuation_disclosure,
    disclose_mapping_field,
    disclose_sequence_field,
    disclose_text_fields,
    tool_output_size,
)

_MAX_MODEL_TEXT_BYTES = 256 * 1024
_MAX_MODEL_TEXT_PAGE_BYTES = 4 * 1024 * 1024
_MAX_MODEL_EDIT_BYTES = 16 * 1024 * 1024
_MAX_MODEL_RESULTS = 1_000
_MAX_MODEL_MEDIA_BYTES = 16 * 1024 * 1024
_MAX_SKILL_MARKDOWN_PAGE_BYTES = 16 * 1024 * 1024
_SKILL_MARKDOWN_LINE_LIMIT = 800
_SKILL_MARKDOWN_MAX_LINE_LENGTH = 20_000

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
        file_scopes: FileScopeProvider | None = None,
    ) -> None:
        self._files = files
        self._file_access = ScopedFileAccess(files, file_scopes)
        self._has_file_scopes = file_scopes is not None
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
        selected_skill_markdown = _is_selected_skill_markdown(ctx, file_path)
        full_skill_markdown_read = line_offset in {None, 0} and selected_skill_markdown
        effective_line_limit = max(line_limit, _SKILL_MARKDOWN_LINE_LIMIT) if full_skill_markdown_read else line_limit
        effective_max_line_length = (
            max(max_line_length, _SKILL_MARKDOWN_MAX_LINE_LENGTH) if selected_skill_markdown else max_line_length
        )
        page_limit = _MAX_SKILL_MARKDOWN_PAGE_BYTES if selected_skill_markdown else _MAX_MODEL_TEXT_PAGE_BYTES
        if effective_line_limit * (effective_max_line_length + 1) > page_limit:
            return _environment_error_result(
                EnvironmentError(
                    "Requested text page exceeds the model view limit.",
                    code="environment_too_large",
                )
            )
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
                async with self._file_access.scope(
                    file_path,
                    prefer_authorized_selection=False,
                ) as files:
                    self._guard_execution()
                    metadata = await files.stat(file_path)
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
                    self._guard_unscoped_step()
                    data = await files.read_bytes(
                        file_path,
                        length=_MAX_MODEL_MEDIA_BYTES + 1,
                    )
                    if len(data) > _MAX_MODEL_MEDIA_BYTES:
                        raise EnvironmentError(
                            "Media file exceeds the model view limit.",
                            code="environment_too_large",
                        )
                    message = f"The {media_type} file {file_path} is attached in the user message."
                    if instructions is not None and instructions.strip():
                        message = f"{message}\n\nAnalysis instructions:\n{instructions.strip()}"
                    return ToolReturn(
                        return_value=message,
                        content=[BinaryContent(data=data, media_type=media_type)],
                    )
            except EnvironmentError as exc:
                return _environment_error_result(exc)

        async def disclose(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
            if selected_skill_markdown:
                return await _disclose_skill_markdown_page(ctx.deps, value)
            return await disclose_text_fields(
                ctx.deps,
                value,
                text_fields=("content",),
                content_complete=not bool(value.get("has_more")),
                noun="file page",
            )

        return await self._execute(
            file_path,
            lambda files: files.read_text(
                file_path,
                line_offset=line_offset or 0,
                line_limit=effective_line_limit,
                max_line_length=effective_max_line_length,
            ),
            lambda result: {
                "file_path": result.path,
                "content": result.text,
                "line_offset": result.line_offset,
                "lines_read": result.lines_read,
                "has_more": result.has_more,
                "truncated_lines": list(result.truncated_lines),
            },
            disclose=disclose,
        )

    async def write(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[str, Field(description="Logical path to the file to write")],
        content: Annotated[str, Field(description="Complete text to write or text to append")],
        mode: Annotated[Literal["w", "a"], Field(default="w")] = "w",
    ) -> FileWriteResult:
        """Write or append text, creating parent directories when needed."""

        async def operation(files: FileOperator):
            async with self._mutation_lock:
                self._guard_execution()
                await self._ensure_parent(files, file_path)
                self._guard_unscoped_step()
                return await files.write_text(
                    file_path,
                    content,
                    mode="append" if mode == "a" else "upsert",
                )

        return await self._execute(
            file_path,
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
        offset: Annotated[int, Field(default=0, ge=0)] = 0,
        max_results: _UnlimitedOrPositiveResults = 500,
    ) -> FileListResult:
        """List one directory and optionally omit matching entry names."""
        limit = _MAX_MODEL_RESULTS if max_results == -1 else max_results

        async def disclose(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
            bounded, showing = await disclose_sequence_field(
                ctx.deps,
                value,
                field="entries",
                content_complete=not bool(value.get("has_more")),
                noun="directory page",
            )
            bounded["showing"] = showing
            return bounded

        def project(result) -> Mapping[str, JsonValue]:
            entries = [
                self._project_metadata(entry)
                for entry in result.entries
                if not ignore or not any(fnmatch.fnmatch(posixpath.basename(entry.path), pattern) for pattern in ignore)
            ]
            next_offset = result.offset + len(result.entries) if result.has_more else None
            return {
                "path": path,
                "entries": cast(JsonValue, entries),
                "count": len(entries),
                "showing": len(entries),
                "has_more": result.has_more,
                "next_offset": next_offset,
            }

        return await self._execute(
            path,
            lambda files: files.list(
                path,
                offset=offset,
                max_results=limit,
                include_hidden=False,
            ),
            project,
            disclose=disclose,
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
        offset: Annotated[int, Field(default=0, ge=0)] = 0,
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
            offset=offset,
            max_results=limit,
        )

        async def disclose(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
            bounded, showing = await disclose_sequence_field(
                ctx.deps,
                value,
                field="files",
                content_complete=not bool(value.get("has_more")),
                noun="glob page",
            )
            bounded["showing"] = showing
            return bounded

        return await self._execute(
            root,
            lambda files: files.query(request),
            lambda result: {
                "files": [entry.path for entry in result.entries],
                "count": len(result.entries),
                "showing": len(result.entries),
                "has_more": result.has_more,
                "next_offset": result.offset + len(result.entries) if result.has_more else None,
            },
            disclose=disclose,
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
        offset: Annotated[int, Field(default=0, ge=0)] = 0,
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
            offset=offset,
            max_matches=_MAX_MODEL_RESULTS,
            max_line_length=2_000,
        )

        async def operation(files: FileOperator):
            result = await files.search_text(request)
            selected = []
            consumed = 0
            per_file: dict[str, int] = {}
            selected_files: set[str] = set()
            for match in result.matches:
                if len(selected) >= result_limit:
                    break
                consumed += 1
                if not _matches_file_glob(match.path, root=root, pattern=include):
                    continue
                if max_files != -1 and match.path not in selected_files and len(selected_files) >= max_files:
                    continue
                count = per_file.get(match.path, 0)
                if max_matches_per_file != -1 and count >= max_matches_per_file:
                    continue
                selected_files.add(match.path)
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
                self._guard_unscoped_step()
                context = await files.read_text(
                    match.path,
                    line_offset=context_start_line - 1,
                    line_limit=context_end_line - context_start_line + 1,
                    max_line_length=max_context_line_length,
                )
                projected.append((match, context, context_start_line))
            has_more = result.has_more or consumed < len(result.matches)
            next_offset = result.offset + consumed if has_more else None
            return projected, next_offset, has_more

        def project(value) -> Mapping[str, JsonValue]:
            projected, next_offset, has_more = value
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
                "showing": len(matches),
                "has_more": has_more,
                "next_offset": next_offset,
            }

        async def disclose(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
            bounded, showing = await disclose_mapping_field(
                ctx.deps,
                value,
                field="matches",
                content_complete=not bool(value.get("has_more")),
                noun="grep page",
            )
            bounded["showing"] = showing
            return bounded

        return await self._execute(
            root,
            operation,
            project,
            disclose=disclose,
        )

    async def _apply_edits(
        self,
        ctx: RunContext[AgentContext],
        file_path: str,
        edits: tuple[FileTextEdit, ...],
    ) -> FileEditResult:
        async def operation(files: FileOperator):
            async with self._mutation_lock:
                self._guard_execution()
                create = edits[0].old_string == ""
                if create:
                    content = edits[0].new_string
                    pending = edits[1:]
                    write_mode = "create"
                else:
                    data = await files.read_bytes(
                        file_path,
                        length=_MAX_MODEL_EDIT_BYTES + 1,
                    )
                    if len(data) > _MAX_MODEL_EDIT_BYTES:
                        raise EnvironmentError(
                            "Edit target exceeds the model edit limit.",
                            code="environment_too_large",
                        )
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
                    await self._ensure_parent(files, file_path)
                self._guard_unscoped_step()
                return await files.write_text(file_path, content, mode=write_mode)

        return await self._execute(
            file_path,
            operation,
            lambda result: {
                "file_path": result.path,
                "edits_applied": len(edits),
                "bytes_written": result.bytes_written,
                "created": edits[0].old_string == "",
            },
        )

    async def _ensure_parent(self, files: FileOperator, file_path: str) -> None:
        parent = posixpath.dirname(file_path)
        parts = tuple(part for part in parent.split("/") if part)
        is_binding_root = parent == "/workspace" or (len(parts) == 2 and parts[0] == "environment")
        if parent and parent != "." and not is_binding_root:
            await files.mkdir(parent, parents=True, exist_ok=True)

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
        path: str,
        operation: Callable[[FileOperator], Awaitable[Any]],
        project: Callable[[Any], Mapping[str, object]],
        *,
        disclose: Callable[[Mapping[str, JsonValue]], Awaitable[Mapping[str, JsonValue]]] | None = None,
    ) -> Any:
        try:
            async with self._file_access.scope(
                path,
                prefer_authorized_selection=False,
            ) as files:
                self._guard_execution()
                result = await operation(files)
            projected = cast(dict[str, JsonValue], {"ok": True, **dict(project(result))})
            if disclose is not None:
                return await disclose(projected)
            return projected
        except EnvironmentError as exc:
            return _environment_error_result(exc)

    def _guard_execution(self) -> None:
        if self._execution_guard is not None:
            self._execution_guard()

    def _guard_unscoped_step(self) -> None:
        if not self._has_file_scopes:
            self._guard_execution()


def _is_selected_skill_markdown(ctx: RunContext[AgentContext], file_path: str) -> bool:
    from converge_agent_harness.capabilities.skills import SKILLS_CAPABILITY_ID, _SkillsRunCapability

    capability = ctx.capabilities.get(SKILLS_CAPABILITY_ID)
    return isinstance(capability, _SkillsRunCapability) and capability.is_selected_markdown(file_path)


async def _disclose_skill_markdown_page(
    context: AgentContext,
    value: Mapping[str, JsonValue],
) -> Mapping[str, JsonValue]:
    safe_value = redact_json(cast(JsonValue, dict(value)))
    assert isinstance(safe_value, dict)
    result = safe_value
    result_fits = tool_output_size(result) <= FINAL_TOOL_OUTPUT_HARD_CHARS
    if result_fits and not bool(result.get("has_more")):
        return acknowledge_tool_output(result)
    content = result.get("content")
    line_offset = result.get("line_offset")
    lines_read = result.get("lines_read")
    if not isinstance(content, str) or not isinstance(line_offset, int) or not isinstance(lines_read, int):
        return await disclose_text_fields(
            context,
            result,
            text_fields=("content",),
            content_complete=not bool(result.get("has_more")),
            noun="skill Markdown page",
            limit=FINAL_TOOL_OUTPUT_HARD_CHARS,
        )

    if result_fits:
        result["next_line_offset"] = line_offset + lines_read
        result["disclosure"] = cast(
            JsonValue,
            continuation_disclosure(
                result,
                hint="Call view again with next_line_offset as line_offset to continue reading this skill Markdown file.",
            ),
        )
        if tool_output_size(result) <= FINAL_TOOL_OUTPUT_HARD_CHARS:
            return acknowledge_tool_output(result)

    lines = _lf_lines(content)
    if len(lines) != lines_read:
        return await disclose_text_fields(
            context,
            result,
            text_fields=("content",),
            content_complete=not bool(result.get("has_more")),
            noun="skill Markdown page",
            limit=FINAL_TOOL_OUTPUT_HARD_CHARS,
        )

    disclosure = continuation_disclosure(
        result,
        hint="Call view again with next_line_offset as line_offset to continue reading this skill Markdown file.",
    )
    preview: dict[str, JsonValue] = {
        **result,
        "content": "",
        "lines_read": 0,
        "has_more": True,
        "next_line_offset": line_offset,
        "truncated_lines": [],
        "disclosure": cast(JsonValue, disclosure),
    }
    shown = 0
    selected_content = ""
    for line in lines:
        candidate_content = f"{selected_content}{line}"
        preview["content"] = candidate_content
        preview["lines_read"] = shown + 1
        preview["next_line_offset"] = line_offset + shown + 1
        if tool_output_size(preview) > FINAL_TOOL_OUTPUT_HARD_CHARS:
            preview["content"] = selected_content
            preview["lines_read"] = shown
            preview["next_line_offset"] = line_offset + shown
            break
        selected_content = candidate_content
        shown += 1

    if shown == 0:
        return await disclose_text_fields(
            context,
            result,
            text_fields=("content",),
            content_complete=not bool(result.get("has_more")),
            noun="skill Markdown page",
            limit=FINAL_TOOL_OUTPUT_HARD_CHARS,
        )
    preview["truncated_lines"] = cast(
        JsonValue,
        [
            item
            for item in cast(list[JsonValue], result.get("truncated_lines", []))
            if isinstance(item, int) and line_offset < item <= line_offset + shown
        ],
    )
    return acknowledge_tool_output(preview)


def _lf_lines(content: str) -> list[str]:
    if not content:
        return []
    parts = content.split("\n")
    lines = [f"{part}\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


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
    normalized_path = path.strip("/")
    normalized_root = root.strip("/")
    if normalized_root not in {"", "."}:
        if normalized_path == normalized_root:
            normalized_path = ""
        elif normalized_path.startswith(f"{normalized_root}/"):
            normalized_path = normalized_path[len(normalized_root) + 1 :]
    if pattern in {"*", "**", "**/*"}:
        return True
    if "/" not in pattern:
        return fnmatch.fnmatchcase(posixpath.basename(normalized_path), pattern)
    return PurePosixPath(normalized_path).full_match(pattern)


__all__ = ["FileTextEdit", "FileToolset"]
