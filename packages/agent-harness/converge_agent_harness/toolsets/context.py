"""Pure context-management Toolsets."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from converge_agent_harness.context import AgentContext

type SaveSummary = Callable[[RunContext[AgentContext], str, list[str] | None], Awaitable[str]]


class HandoffToolset:
    """Model-facing summarize schema over an injected state transition."""

    def __init__(self, save_summary: SaveSummary) -> None:
        self._save_summary = save_summary

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(tools=[self.summarize], id="converge-handoff-tools")

    async def summarize(
        self,
        ctx: RunContext[AgentContext],
        content: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Continuation summary preserving intent, state, decisions, past interactions, and next step."
                ),
            ),
        ],
        files_to_inspect: Annotated[
            list[str] | None,
            Field(
                default=None,
                description="Logical file paths to remind the resumed agent to inspect; no contents are loaded.",
            ),
        ] = None,
    ) -> str:
        return await self._save_summary(ctx, content, files_to_inspect)


__all__ = ["HandoffToolset"]
