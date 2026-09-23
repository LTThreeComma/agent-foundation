"""Awaited Harness boundaries; observer delivery never confirms input consumption."""

from collections.abc import Awaitable, Callable, Sequence
from functools import cache
from typing import Any

from a13n_harness import AgentContext, HarnessState
from pydantic_ai import Agent, AgentRunResult, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ValidatedToolArgs, WrapRunHandler
from pydantic_ai.messages import ModelMessage, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition

from a13n_service.infra.errors import ServiceError
from a13n_service.runs.input_frames import PreparedInputs

type PublishCheckpoint = Callable[[HarnessState, tuple[str, ...], AgentContext], Awaitable[None]]


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

    def __init__(
        self,
        run_id: str,
        publish: PublishCheckpoint,
        *,
        prepared: PreparedInputs,
        receipts: tuple[str, ...] = (),
        feedback_entry_id: str | None = None,
    ):
        self.run_id = run_id
        self.receipts = list(receipts)
        self.prepared = prepared
        self.feedback_entry_id = feedback_entry_id
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

    def _incorporate(self, state: HarnessState) -> None:
        found = self.prepared.incorporated(state.message_history)
        new = [entry_id for entry_id in found if entry_id not in self.receipts]
        remaining = [entry_id for entry_id in self.prepared.expected if entry_id not in self.receipts]
        if new != remaining[: len(new)]:
            raise ServiceError("conflict", "Native input frames are not an offered FIFO prefix")
        self.receipts.extend(new)

    def incorporate_feedback(self) -> None:
        if self.feedback_entry_id is not None and self.feedback_entry_id not in self.receipts:
            self.receipts.append(self.feedback_entry_id)

    async def _publish_boundary(self, ctx: RunContext[AgentContext], messages: Sequence[ModelMessage]) -> None:
        state = await ctx.deps.export_state(messages)
        # Native deferred activation precedes this hook. Receipts use the exact
        # detached state being published, never the mutable source messages.
        self._incorporate(state)
        self.incorporate_feedback()
        await self._publish(state, tuple(self.receipts), ctx.deps)
        self.prepared.confirm(self.receipts)

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if ctx.run_id == self._native_run_id:
            await self._publish_boundary(ctx, request_context.messages)
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
            await self._publish_boundary(ctx, ctx.messages)
        return args
