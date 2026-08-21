from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Coroutine
from typing import Any

import pytest
from converge_agent_harness import (
    HarnessBuilder,
    HarnessEvent,
    HarnessRunResultEvent,
    HarnessState,
    RunBindings,
    RunError,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.output import TextOutput
from pydantic_ai.usage import RunUsage, UsageLimits

pytestmark = pytest.mark.anyio


def _turn_model(calls: list[tuple[ModelMessage, ...]]) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(tuple(messages))
        turn = sum(isinstance(message, ModelResponse) for message in messages) + 1
        yield f"turn-{turn}"

    return FunctionModel(stream_function=stream)


def _build(model: FunctionModel):
    return HarnessBuilder().build_code(
        AgentSpec(model="logical:test", name="test-agent"),
        output_type=str,
        model=model,
    )


async def test_stream_is_lazy_and_delivers_one_terminal_result_after_events() -> None:
    calls: list[tuple[ModelMessage, ...]] = []
    executable = _build(_turn_model(calls))

    async with executable.stream("hello", bindings=RunBindings.local()) as stream:
        assert calls == []
        assert stream.context.environment.is_noop is True
        assert len(stream.context.subagents) == 0

        items = [item async for item in stream]

        assert calls
        assert isinstance(items[-1], HarnessRunResultEvent)
        assert all(isinstance(item, HarnessEvent) for item in items[:-1])
        assert [item.sequence for item in items] == list(range(len(items)))
        assert stream.result is items[-1].result

    result = items[-1].result
    assert result.status == "completed"
    assert result.output_or_raise() == "turn-1"
    assert result.state is not None
    assert result.state.message_history == result.all_messages()
    assert len(result.new_messages()) == 2
    assert result.usage.requests == 1


async def test_enter_and_exit_without_iteration_does_not_start_the_agent() -> None:
    calls: list[tuple[ModelMessage, ...]] = []
    executable = _build(_turn_model(calls))

    async with executable.stream("hello", bindings=RunBindings.local()):
        pass

    assert calls == []


async def test_run_consumes_the_canonical_stream_and_state_resumes_a_rebuilt_agent() -> None:
    first_calls: list[tuple[ModelMessage, ...]] = []
    first_executable = _build(_turn_model(first_calls))
    first = await first_executable.run("first", bindings=RunBindings.local())
    assert first.output == "turn-1"
    assert first.state is not None

    encoded_state = first.state.model_dump_json()
    restored_state = HarnessState.model_validate_json(encoded_state)

    second_calls: list[tuple[ModelMessage, ...]] = []
    rebuilt_executable = _build(_turn_model(second_calls))
    second = await rebuilt_executable.run(
        "second",
        bindings=RunBindings.local(),
        previous_state=restored_state,
    )

    assert second.output == "turn-2"
    assert second.all_messages()[: len(first.all_messages())] == first.all_messages()
    assert len(second.new_messages()) == 2
    assert second.state is not None
    assert second.state.message_history == second.all_messages()
    assert first_executable.definition is not rebuilt_executable.definition


@pytest.mark.parametrize("annotation", ["awaitable", "coroutine"])
async def test_output_functions_may_annotate_their_awaitable_result(annotation: str) -> None:
    async def transform(value: str) -> str:
        return f"{value}|parsed"

    def awaitable_output(value: str) -> Awaitable[str]:
        return transform(value)

    def coroutine_output(value: str) -> Coroutine[Any, Any, str]:
        return transform(value)

    output_function = awaitable_output if annotation == "awaitable" else coroutine_output
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test", name="test-agent"),
        output_type=TextOutput(output_function),
        model=_turn_model([]),
    )

    result = await executable.run("hello", bindings=RunBindings.local())

    assert result.output_or_raise() == "turn-1|parsed"


async def test_every_run_gets_a_fresh_context() -> None:
    executable = _build(_turn_model([]))
    contexts = []

    async with executable.stream("one", bindings=RunBindings.local()) as first_stream:
        contexts.append(first_stream.context)
        async for _ in first_stream:
            pass

    async with executable.stream("two", bindings=RunBindings.local()) as second_stream:
        contexts.append(second_stream.context)
        async for _ in second_stream:
            pass

    assert contexts[0] is not contexts[1]
    assert contexts[0].state is not contexts[1].state
    assert contexts[0].plugins is not contexts[1].plugins
    assert contexts[0].run_id != contexts[1].run_id


async def test_input_factory_runs_once_after_noop_environment_entry() -> None:
    calls: list[str] = []
    model_calls: list[tuple[ModelMessage, ...]] = []
    executable = _build(_turn_model(model_calls))

    async def input_factory(preparation):
        assert preparation.environment.is_noop is True
        calls.append(preparation.run_id)
        return "from factory"

    async with executable.stream(input_factory=input_factory, bindings=RunBindings.local()) as stream:
        assert calls == [stream.run_id]
        assert model_calls == []
        async for _ in stream:
            pass

    assert len(calls) == 1
    assert len(model_calls) == 1


async def test_native_cancellation_becomes_a_cancelled_result() -> None:
    started = asyncio.Event()

    async def blocking_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    executable = _build(FunctionModel(stream_function=blocking_stream))

    async with executable.stream("cancel me", bindings=RunBindings.local()) as stream:
        next_item = asyncio.create_task(stream.__anext__())
        await started.wait()
        stream.cancel()
        terminal = await asyncio.wait_for(next_item, timeout=2)

        assert isinstance(terminal, HarnessRunResultEvent)
        assert terminal.result.status == "cancelled"
        assert terminal.result.output is None
        assert stream.result is terminal.result


async def test_prestart_cancellation_never_calls_the_model_and_preserves_supplied_usage() -> None:
    calls: list[tuple[ModelMessage, ...]] = []
    executable = _build(_turn_model(calls))
    supplied_usage = RunUsage(requests=7, details={"cached": 2})

    async with executable.stream(
        "cancel before start",
        bindings=RunBindings.local(),
        usage=supplied_usage,
    ) as stream:
        stream.cancel()
        terminal = await stream.__anext__()

    assert isinstance(terminal, HarnessRunResultEvent)
    assert terminal.result.status == "cancelled"
    assert terminal.result.state is None
    assert terminal.result.usage.requests == 7
    assert terminal.result.usage.details == {"cached": 2}
    assert calls == []


async def test_started_stream_closes_the_model_when_the_caller_stops_early() -> None:
    closed = asyncio.Event()

    async def open_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        try:
            yield "partial"
            await asyncio.Event().wait()
        finally:
            closed.set()

    executable = _build(FunctionModel(stream_function=open_stream))

    async with executable.stream("start", bindings=RunBindings.local()) as stream:
        item = await stream.__anext__()
        assert isinstance(item, HarnessEvent)

    await asyncio.wait_for(closed.wait(), timeout=2)


async def test_concurrent_next_is_rejected_without_closing_the_active_stream() -> None:
    started = asyncio.Event()

    async def blocking_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    executable = _build(FunctionModel(stream_function=blocking_stream))

    async with executable.stream("hello", bindings=RunBindings.local()) as stream:
        active_next = asyncio.create_task(stream.__anext__())
        await started.wait()
        with pytest.raises(RunError) as exc_info:
            await stream.__anext__()
        assert exc_info.value.code == "run_stream_concurrent_next"

        stream.cancel()
        terminal = await asyncio.wait_for(active_next, timeout=2)
        assert isinstance(terminal, HarnessRunResultEvent)
        assert terminal.result.status == "cancelled"


async def test_usage_limit_has_a_specific_safe_failure() -> None:
    executable = _build(_turn_model([]))

    result = await executable.run(
        "hello",
        bindings=RunBindings.local(),
        usage_limits=UsageLimits(request_limit=0),
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "usage_limit_exceeded"
    assert result.failure.message == "Pydantic AI usage limit exceeded."
    assert result.failure.retry_hint == "dependency_change"


async def test_recognized_pydantic_run_failure_becomes_a_failed_result() -> None:
    async def failing_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        raise UnexpectedModelBehavior("provider returned an invalid response")
        yield "unreachable"

    executable = _build(FunctionModel(stream_function=failing_stream))
    result = await executable.run("fail", bindings=RunBindings.local())

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "agent_run_failed"
    assert "invalid response" not in result.failure.message
