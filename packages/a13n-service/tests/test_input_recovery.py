"""Canonical public inputs survive a crash before their delayed native display event."""

import asyncio

import pytest
from a13n_harness import AgentDefinition, HarnessBuilder, HarnessEvent, HarnessState
from a13n_harness.model_context import ModelInputEvent
from a13n_service.infra.db import short_session
from a13n_service.resources.agents.tables import AgentRevisionRow
from a13n_service.runs import inputs, seal
from a13n_service.runs.attempts import claim_run, start
from a13n_service.runs.harness import CheckpointCapability, entry_input
from a13n_service.runs.publisher import Publisher
from a13n_service.runs.schemas import AgentSelection, RunOptions
from a13n_service.runs.tables import RunRow
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelRequest, TextContent, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("resolution", ["recover", "fail"])
@pytest.mark.parametrize("input_kind", ["source", "steer"])
@pytest.mark.parametrize("cut", ["after_confirmation", "before_confirmation"])
async def test_public_input_survives_native_projection_gap_without_reoffer(
    public_service, monkeypatch, cut, input_kind, resolution
):
    service = public_service
    response = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "recovery-input"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "Keep this original input"}]},
        },
    )
    assert response.status_code == 201, response.text
    entry_id, run_id = response.json()["entry"]["id"], response.json()["run"]["id"]
    storage, objects = service.app.state.storage, service.app.state.objects
    first = await claim_run(storage, worker_id="first", worker_build="test", lease_seconds=3)
    assert first is not None
    async with short_session(storage) as session:
        row = await session.get(RunRow, run_id)
        revision = await session.get(AgentRevisionRow, row.agent_revision_id)
        selected = AgentSelection(
            agent_id=row.agent_id, revision_id=revision.id, digest=revision.digest, config=revision.config
        )
        options = RunOptions.model_validate(row.options)
    publisher = Publisher(storage, objects, first, selected, options, max_bytes=1048576, max_events=1000, timeout=3)
    expected_ids = [entry_id]
    target_id = entry_id if input_kind == "source" else None
    tool_entered, tool_release = asyncio.Event(), asyncio.Event()
    reached_cut = asyncio.Event()
    never = asyncio.Event()
    actual_confirm = inputs.confirm

    async def confirm_at_cut(storage, claim, checkpoint):
        if cut == "before_confirmation" and claim.attempt_id == first.attempt_id and target_id in checkpoint.receipts:
            reached_cut.set()
            await never.wait()
        await actual_confirm(storage, claim, checkpoint)

    monkeypatch.setattr(inputs, "confirm", confirm_at_cut)

    async def publish_at_cut(state, receipts, context):
        await publisher.publish(state, receipts, context)
        if cut == "after_confirmation" and target_id in receipts:
            reached_cut.set()
            await never.wait()

    calls = []

    async def pause_tool():
        tool_entered.set()
        await tool_release.wait()
        return "finished"

    async def model(messages, info):
        calls.append(messages)
        if input_kind == "steer" and len(calls) == 1:
            yield {0: DeltaToolCall(name="pause_tool", json_args="{}", tool_call_id="pause-1")}
        else:
            yield "Recovered"

    native_inputs = []
    capability = CheckpointCapability(run_id, publish_at_cut)
    executable = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(capability, Capability(id="tools", tools=[pause_tool])),
        )
    )
    await publisher.initialize(HarnessState.new())
    try:
        async with executable.stream(
            entry_input(run_id, entry_id, "Keep this original input"), previous_state=publisher.checkpoint.state
        ) as stream:
            await start(storage, first, harness_run_id=stream.run_id)

            async def drain():
                async for item in stream:
                    if isinstance(item, HarnessEvent) and isinstance(item.event, ModelInputEvent):
                        native_inputs.append(item)
                    publisher.observe(item)

            task = asyncio.create_task(drain())
            try:
                if input_kind == "steer":
                    await asyncio.wait_for(tool_entered.wait(), timeout=5)
                    steer = await service.client.post(
                        f"{service.workspace_path}/threads/{response.json()['entry']['thread_id']}/inbox",
                        headers={"Idempotency-Key": "recovery-steer"},
                        json={
                            "kind": "message",
                            "delivery": "steer",
                            "agent_id": service.agent_id,
                            "payload": {"content": [{"type": "text", "text": "Keep this steer input"}]},
                        },
                    )
                    assert steer.status_code == 201, steer.text
                    target_id = steer.json()["entry"]["id"]
                    expected_ids.append(target_id)
                    assigned = await inputs.assign_steers(storage, first, max_count=10, max_bytes=1048576)
                    assert [item[0] for item in assigned] == [target_id]
                    await stream.steer(entry_input(run_id, target_id, "Keep this steer input"))
                    tool_release.set()
                await asyncio.wait_for(reached_cut.wait(), timeout=5)
                assert len(calls) == (1 if input_kind == "steer" else 0)
                assert not any(
                    isinstance(content, TextContent) and content.metadata.get("source_id") == target_id
                    for item in native_inputs
                    for content in item.event.content
                )  # The target's native projection has not reached the consumer at this cut.
                before = await service.client.get(f"{service.workspace_path}/runs/{run_id}/items")
                assert before.status_code == 200, before.text
                assert [item["id"] for item in before.json()["inputs"]] == expected_ids
                assert before.json()["inputs"][0]["payload"]["content"][0]["text"] == "Keep this original input"
                assert before.json()["inputs"][-1]["status"] == (
                    "consumed" if cut == "after_confirmation" else "assigned"
                )
                assert "Keep this original input" not in str(before.json()["segments"])
                if resolution == "fail":
                    await seal.failed(storage, first, code="injected", message="Terminal failure at checkpoint cut")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        await publisher.close()
    if resolution == "fail":
        terminal = await service.client.get(f"{service.workspace_path}/runs/{run_id}/items")
        assert terminal.status_code == 200, terminal.text
        value = terminal.json()
        assert value["complete"] and value["status"] == "failed"
        assert [item["id"] for item in value["inputs"]] == expected_ids
        assert value["inputs"][-1]["status"] == ("consumed" if cut == "after_confirmation" else "failed")
        assert (
            value["inputs"][-1]["incorporated_checkpoint_seq"] is not None
            if cut == "after_confirmation"
            else value["inputs"][-1]["incorporated_checkpoint_seq"] is None
        )
        if input_kind == "steer":
            assert value["inputs"][0]["status"] == "consumed"
        assert all(segment["interrupted"] for segment in value["segments"])
        assert await claim_run(storage, worker_id="late", worker_build="test", lease_seconds=30) is None
        return
    async with asyncio.timeout(5):
        while not await seal.expire(storage, run_id, backoff_seconds=0):
            await asyncio.sleep(0.03)
    second = await claim_run(storage, worker_id="second", worker_build="test", lease_seconds=30)
    assert second is not None and second.number == 2
    recovered = Publisher(storage, objects, second, selected, options, max_bytes=1048576, max_events=1000, timeout=3)
    try:
        await recovered.initialize(HarnessState.new())
        assert recovered.checkpoint.receipts == tuple(expected_ids)
        assert await inputs.assigned_inputs(storage, second) == ()  # No reoffer to regenerate observations.
        capability = CheckpointCapability(run_id, recovered.publish, receipts=recovered.checkpoint.receipts)
        executable = HarnessBuilder().build(
            AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(capability, Capability(id="tools", tools=[pause_tool])),
            )
        )
        async with executable.stream(previous_state=recovered.checkpoint.state, tool_recovery="declared") as stream:
            await start(storage, second, harness_run_id=stream.run_id)
            async for item in stream:
                recovered.observe(item)
            assert stream.result is not None and stream.result.state is not None
            await recovered.finalize(
                stream.result.state, tuple(capability.receipts), output=stream.result.output_or_raise()
            )
        _, state_ref, display_ref = recovered.selected()
        await seal.completed(storage, objects, second, state_ref, display_ref)
        after = await service.client.get(f"{service.workspace_path}/runs/{run_id}/items")
        assert after.status_code == 200, after.text
        value = after.json()
        assert value["complete"] and value["status"] == "completed"
        assert [item["id"] for item in value["inputs"]] == expected_ids
        assert all(item["status"] == "consumed" for item in value["inputs"])
        assert value["inputs"][0]["payload"]["content"][0]["text"] == "Keep this original input"
        assert "Keep this original input" not in str(value["segments"])
        assert value["segments"][0]["interrupted"]
        assert len(calls) == (2 if input_kind == "steer" else 1)
        contents = [
            content.content
            for message in calls[-1]
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
            for content in part.content
            if isinstance(content, TextContent)
        ]
        assert contents.count("Keep this original input") == 1
        if input_kind == "steer":
            assert contents.count("Keep this steer input") == 1
            assert value["inputs"][1]["payload"]["content"][0]["text"] == "Keep this steer input"
            assert "Keep this steer input" not in str(value["segments"])
    finally:
        await recovered.close()
