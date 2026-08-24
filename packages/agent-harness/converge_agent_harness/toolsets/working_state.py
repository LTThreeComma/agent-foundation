"""Pure Working State Toolset and task/note result contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol, TypedDict

from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from converge_agent_harness.context import AgentContext

from ._results import ToolFailure


class TaskProjection(TypedDict):
    id: str
    subject: str
    description: str
    active_form: str | None
    status: Literal["pending", "in_progress", "completed"]
    owner: str | None
    blocks: list[str]
    blocked_by: list[str]
    metadata: dict[str, JsonValue]


class TaskSuccess(TypedDict):
    ok: Literal[True]
    task: TaskProjection


class TaskListSuccess(TypedDict):
    ok: Literal[True]
    tasks: list[TaskProjection]


type TaskToolResult = TaskSuccess | ToolFailure
type TaskListToolResult = TaskListSuccess | ToolFailure


class NoteMutationSuccess(TypedDict):
    ok: Literal[True]
    key: str
    present: bool


class NoteKeysSuccess(TypedDict):
    ok: Literal[True]
    keys: list[str]


class NoteValueSuccess(TypedDict):
    ok: Literal[True]
    key: str
    value: str


type NoteToolResult = NoteMutationSuccess | ToolFailure
type NoteGetToolResult = NoteKeysSuccess | NoteValueSuccess | ToolFailure


class WorkingStateToolOperations(Protocol):
    """Adapter to the Capability-owned coherent task/note state, not a provider SPI."""

    async def task_create(
        self,
        ctx: RunContext[AgentContext],
        subject: str,
        description: str,
        active_form: str | None = None,
        blocked_by: Sequence[str] = (),
        blocks: Sequence[str] = (),
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> TaskToolResult: ...

    async def task_get(self, ctx: RunContext[AgentContext], task_id: str) -> TaskToolResult: ...

    async def task_list(self, ctx: RunContext[AgentContext]) -> TaskListToolResult: ...

    async def task_update(
        self,
        ctx: RunContext[AgentContext],
        task_id: str,
        status: Literal["pending", "in_progress", "completed"] | None = None,
        subject: str | None = None,
        description: str | None = None,
        active_form: str | None = None,
        add_blocks: Sequence[str] = (),
        add_blocked_by: Sequence[str] = (),
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> TaskToolResult: ...

    async def note(
        self,
        ctx: RunContext[AgentContext],
        key: str,
        value: str | None = None,
    ) -> NoteToolResult: ...

    async def note_get(
        self,
        ctx: RunContext[AgentContext],
        key: str | None = None,
    ) -> NoteGetToolResult: ...


class WorkingStateToolset:
    """Model-facing task and note intents over an injected state owner."""

    def __init__(self, operations: WorkingStateToolOperations) -> None:
        self._operations = operations

    def get_toolset(self, *, tasks: bool, notes: bool) -> FunctionToolset[AgentContext] | None:
        tools = []
        if tasks:
            tools.extend((self.task_create, self.task_get, self.task_list, self.task_update))
        if notes:
            tools.extend((self.note, self.note_get))
        return FunctionToolset(tools=tools, id="converge-working-state-tools") if tools else None

    async def task_create(
        self,
        ctx: RunContext[AgentContext],
        subject: str,
        description: str,
        active_form: str | None = None,
        blocked_by: Sequence[str] = (),
        blocks: Sequence[str] = (),
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> TaskToolResult:
        return await self._operations.task_create(
            ctx,
            subject,
            description,
            active_form,
            blocked_by,
            blocks,
            metadata,
        )

    async def task_get(self, ctx: RunContext[AgentContext], task_id: str) -> TaskToolResult:
        return await self._operations.task_get(ctx, task_id)

    async def task_list(self, ctx: RunContext[AgentContext]) -> TaskListToolResult:
        return await self._operations.task_list(ctx)

    async def task_update(
        self,
        ctx: RunContext[AgentContext],
        task_id: str,
        status: Literal["pending", "in_progress", "completed"] | None = None,
        subject: str | None = None,
        description: str | None = None,
        active_form: str | None = None,
        add_blocks: Sequence[str] = (),
        add_blocked_by: Sequence[str] = (),
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> TaskToolResult:
        return await self._operations.task_update(
            ctx,
            task_id,
            status,
            subject,
            description,
            active_form,
            add_blocks,
            add_blocked_by,
            metadata,
        )

    async def note(
        self,
        ctx: RunContext[AgentContext],
        key: str,
        value: str | None = None,
    ) -> NoteToolResult:
        return await self._operations.note(ctx, key, value)

    async def note_get(
        self,
        ctx: RunContext[AgentContext],
        key: str | None = None,
    ) -> NoteGetToolResult:
        return await self._operations.note_get(ctx, key)


__all__ = [
    "NoteGetToolResult",
    "NoteToolResult",
    "TaskListToolResult",
    "TaskProjection",
    "TaskToolResult",
    "WorkingStateToolOperations",
    "WorkingStateToolset",
]
