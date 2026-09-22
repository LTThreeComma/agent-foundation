"""Service receipts and unsafe dispatch exercised through the actual Harness."""

import asyncio
from copy import deepcopy

import pytest
from a13n_harness import AgentDefinition, HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy
from a13n_service.runs.harness import CheckpointCapability, entry_input
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


async def test_source_receipt_precedes_real_compaction_and_survives_history_replacement():
    checkpoints = []
    calls = []

    async def publish(state, receipts):
        checkpoints.append((state.model_copy(deep=True), receipts))
        await asyncio.sleep(0)

    capability = CheckpointCapability("run_test", publish)

    async def model(messages, info):
        calls.append(deepcopy(messages))
        assert capability.receipts == ["inb_source"]
        yield "compacted summary" if len(calls) == 1 else "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("old input")]),
            ModelResponse(parts=[TextPart("old answer")], usage=RequestUsage(input_tokens=2100, output_tokens=100)),
        )
    )
    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(system_prompt="Follow instructions"),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(capability, CompactionCapability(CompactionPolicy(trigger_tokens=2000))),
        )
    )
    result = await executable.run(entry_input("run_test", "inb_source", "new input"), previous_state=previous)
    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    assert len(checkpoints) == 1  # Nested compaction must not publish its private history.
    assert checkpoints[0][1] == ("inb_source",)
    assert capability.receipts == ["inb_source"]
    assert result.state is not None
    assert previous.message_history[1] not in result.state.message_history
    assert any(
        isinstance(message, ModelRequest) and (message.metadata or {}).get("a13n.context") == "compaction"
        for message in result.state.message_history
    )


async def test_pending_unsafe_call_is_checkpointed_before_effect_and_not_replayed():
    checkpoints = []
    effects = []
    crashed = asyncio.Event()

    async def publish(state, receipts):
        checkpoints.append((state.model_copy(deep=True), receipts))

    async def unsafe_write():
        state, receipts = checkpoints[-1]
        assert receipts == ("inb_source",)
        assert any(
            isinstance(part, ToolCallPart) and part.tool_call_id == "unsafe-1"
            for message in state.message_history
            if isinstance(message, ModelResponse)
            for part in message.parts
        )
        effects.append("effect")
        crashed.set()
        await asyncio.Event().wait()

    async def model(messages, info):
        if any(isinstance(message, ModelResponse) for message in messages):
            yield "recovered unknown outcome"
        else:
            yield {0: DeltaToolCall(name="unsafe_write", json_args="{}", tool_call_id="unsafe-1")}

    def executable(capability):
        return HarnessBuilder().build(
            AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(capability, Capability(id="tools", tools=[unsafe_write])),
            )
        )

    task = asyncio.create_task(
        executable(CheckpointCapability("run_test", publish)).run(entry_input("run_test", "inb_source", "write"))
    )
    try:
        await asyncio.wait_for(crashed.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10)
    finally:
        task.cancel()
    assert crashed.is_set() and effects == ["effect"]
    saved, receipts = checkpoints[-1]
    result = await executable(CheckpointCapability("run_test", publish, receipts=receipts)).run(
        previous_state=saved, tool_recovery="declared", bindings=RunBindings.embedded()
    )
    assert result.output_or_raise() == "recovered unknown outcome"
    assert effects == ["effect"]
    assert result.state is not None
    assert "interrupted" in result.state.model_dump_json().lower()


async def test_steer_receipt_precedes_compaction_without_observer_processing():
    entered = asyncio.Event()
    release = asyncio.Event()
    checkpoints = []
    model_calls = 0

    async def publish(state, receipts):
        checkpoints.append((state.model_copy(deep=True), receipts))

    capability = CheckpointCapability("run_test", publish)

    async def pause_tool():
        entered.set()
        await release.wait()
        return "tool result"

    async def model(messages, info):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            yield {0: DeltaToolCall(name="pause_tool", json_args="{}", tool_call_id="pause-1")}
        else:
            assert capability.receipts == ["inb_source", "inb_steer"]
            yield "compacted" if not info.function_tools else "done"

    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(system_prompt="Follow instructions"),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(
                capability,
                Capability(id="tools", tools=[pause_tool]),
                CompactionCapability(CompactionPolicy(trigger_tokens=1)),
            ),
        )
    )
    async with executable.stream(entry_input("run_test", "inb_source", "start")) as stream:

        async def drain_without_observer():
            async for _ in stream:
                pass  # No event handler can establish the input receipts.

        task = asyncio.create_task(drain_without_observer())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            await stream.steer(entry_input("run_test", "inb_steer", "steer input"))
            assert capability.receipts == ["inb_source"]  # Enqueued does not mean incorporated.
            release.set()
            await asyncio.wait_for(task, timeout=10)
        finally:
            release.set()
            task.cancel()
        assert stream.result is not None
        assert stream.result.output_or_raise() == "done"
    assert model_calls == 3  # One ordinary request, a real nested compaction, final request.
    assert checkpoints[-1][1] == ("inb_source", "inb_steer")
