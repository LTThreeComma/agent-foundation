"""Typed model results returned by FileToolset."""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic_ai import ToolReturn

from ._results import ToolFailure


class FileMetadataProjection(TypedDict):
    path: str
    kind: Literal["file", "directory", "symlink", "other"]
    size: int | None
    writable: bool


class FileViewSuccess(TypedDict):
    ok: Literal[True]
    file_path: str
    content: str
    line_offset: int
    lines_read: int
    has_more: bool
    truncated_lines: list[int]


type FileViewResult = FileViewSuccess | ToolFailure | ToolReturn


class FileWriteSuccess(TypedDict):
    ok: Literal[True]
    file_path: str
    bytes_written: int


type FileWriteResult = FileWriteSuccess | ToolFailure


class FileEditSuccess(TypedDict):
    ok: Literal[True]
    file_path: str
    edits_applied: int
    bytes_written: int
    created: bool


type FileEditResult = FileEditSuccess | ToolFailure


class FileListSuccess(TypedDict):
    ok: Literal[True]
    path: str
    entries: list[FileMetadataProjection]
    count: int
    has_more: bool


type FileListResult = FileListSuccess | ToolFailure


class FileGlobSuccess(TypedDict):
    ok: Literal[True]
    files: list[str]
    count: int
    has_more: bool


type FileGlobResult = FileGlobSuccess | ToolFailure


class GrepMatchProjection(TypedDict):
    file_path: str
    line_number: int
    matching_line: str
    text_truncated: bool
    context: str
    context_start_line: int


class FileGrepSuccess(TypedDict):
    ok: Literal[True]
    matches: dict[str, GrepMatchProjection]
    count: int
    has_more: bool


type FileGrepResult = FileGrepSuccess | ToolFailure


__all__ = [
    "FileEditResult",
    "FileGlobResult",
    "FileGrepResult",
    "FileListResult",
    "FileMetadataProjection",
    "FileViewResult",
    "FileWriteResult",
]
