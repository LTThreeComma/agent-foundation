"""Awaited Harness boundaries; observer delivery never confirms input consumption."""

from collections.abc import Awaitable, Callable, Sequence
from functools import cache
from typing import Any

from a13n_harness import AgentContext, HarnessState
from pydantic_ai import Agent, AgentRunResult, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ValidatedToolArgs, WrapRunHandler
from pydantic_ai.messages import ModelMessage, ModelRequest, TextContent, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition

INPUT_PROVENANCE = "a13n.service.input"
type PublishCheckpoint = Callable[[HarnessState, tuple[str, ...], AgentContext], Awaitable[None]]


def entry_input(run_id: str, entry_id: str, content: str) -> list[TextContent]:
    return [
        TextContent(
            content,
            metadata={
                "display": False,
                "source_id": entry_id,
                INPUT_PROVENANCE: {"run_id": run_id, "entry_id": entry_id},
            },
        )
    ]


@cache
def native_infrastructure() -> tuple[type[AbstractCapability[AgentContext]], ...]:
    """Discover native anonymous wrappers through the public baseline Agent tree."""
    kinds: set[type[AbstractCapability[AgentContext]]] = set()

    def collect(capability: AbstractCapability[AgentContext]) -> None:
        if capability.id is None:
            kinds.add(type(capability))

    Agent(deps_type=AgentContext).root_capability.apply(collect)
    return tuple(kinds)


class CheckpointCapability(AbstractCapability[AgentContext]):
    """One attempt's cumulative receipts and mandatory pre-dispatch checkpoints."""

    id = "a13n.service.checkpoint"

    def __init__(self, run_id: str, publish: PublishCheckpoint, *, receipts: tuple[str, ...] = ()):
        self.run_id = run_id
        self.receipts = list(receipts)
        self._publish = publish
        self._native_run_id: str | None = None

    def get_ordering(self) -> CapabilityOrdering:
        # Native enqueue must drain first; feature hooks (especially compaction) run after us.
        return CapabilityOrdering(position="outermost", wrapped_by=native_infrastructure())

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: WrapRunHandler) -> AgentRunResult[Any]:
        primary = self._native_run_id is None
        if primary:
            self._native_run_id = ctx.run_id
        try:
            return await handler()
        finally:
            if primary:
                self._native_run_id = None

    def _incorporate(self, messages: Sequence[ModelMessage]) -> None:
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, UserPromptPart) or isinstance(part.content, str):
                    continue
                for content in part.content:
                    if not isinstance(content, TextContent):
                        continue
                    provenance = (content.metadata or {}).get(INPUT_PROVENANCE)
                    if not isinstance(provenance, dict) or provenance.get("run_id") != self.run_id:
                        continue
                    entry_id = provenance.get("entry_id")
                    if isinstance(entry_id, str) and entry_id not in self.receipts:
                        self.receipts.append(entry_id)

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if ctx.run_id == self._native_run_id:
            self._incorporate(request_context.messages)
            state = await ctx.deps.export_state(request_context.messages)
            await self._publish(state, tuple(self.receipts), ctx.deps)
        return request_context

    async def before_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
    ) -> ValidatedToolArgs:
        if ctx.run_id == self._native_run_id:
            # The response with pending calls is in canonical history before this awaited hook.
            state = await ctx.deps.export_state(ctx.messages)
            await self._publish(state, tuple(self.receipts), ctx.deps)
        return args
