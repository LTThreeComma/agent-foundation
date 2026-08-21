"""Bounded model-attempt recovery and interrupted-history repair."""

from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace

from pydantic_ai.exceptions import (
    ModelAPIError,
    RunCancelled,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from converge_agent_harness.errors import HarnessError
from converge_agent_harness.input import RunInputValue

RecoveryPromptFactory = Callable[
    [BaseException, int, Sequence[ModelMessage]],
    RunInputValue | Awaitable[RunInputValue],
]

DEFAULT_RECOVERY_PROMPT = (
    "The previous model stream ended before the task finished. Continue from the available "
    "history and avoid repeating completed work. A tool operation may have partially or fully "
    "completed even when no result was recorded, so check the current state before retrying "
    "side-effecting work such as shell commands."
)

INTERRUPTED_TOOL_RESULT = (
    "No tool result was recorded because execution was interrupted. The operation may have "
    "partially or fully completed. Check the current state before deciding whether to retry it."
)


@dataclass(frozen=True, slots=True)
class ModelRecoveryPolicy:
    """Optional total attempt budget for interrupted model execution."""

    enabled: bool = False
    max_attempts: int = 5
    continuation_prompt: RunInputValue = DEFAULT_RECOVERY_PROMPT
    prompt_factory: RecoveryPromptFactory | None = None
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.backoff_initial_seconds < 0 or self.backoff_max_seconds < 0:
            raise ValueError("recovery backoff values must not be negative")

    async def build_prompt(
        self,
        error: BaseException,
        attempt_index: int,
        messages: Sequence[ModelMessage],
    ) -> RunInputValue:
        if self.prompt_factory is None:
            return self.continuation_prompt
        value = self.prompt_factory(error, attempt_index, messages)
        if inspect.isawaitable(value):
            value = await value
        return value

    def delay(self, attempt_index: int) -> float:
        if self.backoff_initial_seconds == 0 or self.backoff_max_seconds == 0:
            return 0
        ceiling = min(
            self.backoff_initial_seconds * (2 ** max(0, attempt_index - 1)),
            self.backoff_max_seconds,
        )
        return random.uniform(0, ceiling)


def normalize_interrupted_history(messages: Sequence[ModelMessage]) -> tuple[tuple[ModelMessage, ...], int]:
    """Close only tool calls at an explicitly interrupted terminal boundary."""
    normalized = list(messages)
    if not normalized:
        return (), 0

    tail = normalized[-1]
    if isinstance(tail, ModelResponse) and tail.state == "interrupted":
        missing = _missing_tool_calls(tail, ())
        if missing:
            normalized.append(ModelRequest(parts=[_failed_tool_result(call) for call in missing]))
        return tuple(normalized), len(missing)

    if isinstance(tail, ModelRequest) and tail.state == "interrupted":
        response = next(
            (message for message in reversed(normalized[:-1]) if isinstance(message, ModelResponse)),
            None,
        )
        if response is None:
            return tuple(normalized), 0
        missing = _missing_tool_calls(response, tail.parts)
        if missing:
            normalized[-1] = replace(
                tail,
                parts=[*tail.parts, *(_failed_tool_result(call) for call in missing)],
            )
        return tuple(normalized), len(missing)

    return tuple(normalized), 0


def is_recoverable_model_failure(error: BaseException, messages: Sequence[ModelMessage]) -> bool:
    """Classify only failures at a model-request boundary as attempt-recoverable."""
    if isinstance(error, HarnessError | RunCancelled | UsageLimitExceeded | asyncio.CancelledError):
        return False
    if isinstance(error, ModelAPIError):
        return True
    if isinstance(error, UnexpectedModelBehavior):
        text = str(error).lower()
        return "exceeded maximum" not in text or "retries" not in text
    if not messages:
        return False
    tail = messages[-1]
    if isinstance(tail, ModelResponse):
        return tail.state == "interrupted"
    return isinstance(tail, ModelRequest) and tail.state != "interrupted"


def _missing_tool_calls(
    response: ModelResponse,
    result_parts: Sequence[object],
) -> list[ToolCallPart]:
    answered = {
        part.tool_call_id
        for part in result_parts
        if isinstance(part, ToolReturnPart | RetryPromptPart) and part.tool_call_id is not None
    }
    return [part for part in response.parts if isinstance(part, ToolCallPart) and part.tool_call_id not in answered]


def _failed_tool_result(call: ToolCallPart) -> ToolReturnPart:
    return ToolReturnPart(
        tool_name=call.tool_name,
        tool_call_id=call.tool_call_id,
        content=INTERRUPTED_TOOL_RESULT,
        outcome="failed",
    )
