"""Real Harness, local object CAS and PostgreSQL receipt durability ordering."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessState,
)
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelInputEvent,
)
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.tables import AgentRevisionRow
from a13n_service.runs import input_frames, inputs, seal
from a13n_service.runs.attempts import LeaseLost, claim_run, heartbeat, start
from a13n_service.runs.display import Segment
from a13n_service.runs.execute import execute
from a13n_service.runs.harness import CheckpointCapability
from a13n_service.runs.input_frames import PreparedInputs
from a13n_service.runs.publisher import Publisher
from a13n_service.runs.schemas import AgentSelection, RunOptions
from a13n_service.runs.tables import InboxEntryRow, RunRow
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    BinaryContent,
    EnqueuedMessagesEvent,
    ModelRequest,
    ModelResponse,
    TextContent,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


class _OverlayCapability(AbstractModelContextCapability):
    id = "test.publisher-overlay"

    async def wrap_model_context(
        self, ctx: RunContext[AgentContext], request: ModelContextProjectionRequest, handler: ModelContextNext
    ) -> ModelContextProjection:
        projection = await handler(request)
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock("test.overlay", ModelContextPlacement.REQUEST_EPILOGUE, "Internal context"),
            )
        )


async def test_publisher_keeps_unowned_input_and_enqueue_diagnostic(public_service, monkeypatch):
    service = public_service
    response = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "frame-observation"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "source"}]},
        },
    )
    assert response.status_code == 201, response.text
    storage = service.app.state.storage
    claim = await claim_run(storage, worker_id="frame-observer", worker_build="test", lease_seconds=30)
    assert claim is not None
    async with short_session(storage) as session:
        run = await session.get(RunRow, claim.run_id)
        revision = await session.get(AgentRevisionRow, run.agent_revision_id)
        selected = AgentSelection(
            agent_id=run.agent_id, revision_id=revision.id, digest=revision.digest, config=revision.config
        )
        options = RunOptions.model_validate(run.options)
    publisher = Publisher(
        storage, service.app.state.objects, claim, selected, options, max_bytes=1048576, max_events=1000, timeout=3
    )
    try:
        await publisher.initialize(HarnessState.new())
        prepared = PreparedInputs(claim.run_id)
        publisher.prepared = prepared
        owned = prepared.offer(
            response.json()["entry"]["id"],
            [TextContent("owned" * 20000), BinaryContent(b"owned-image", media_type="image/png")],
        )
        hashes = 0
        actual_digest = input_frames.payload_digest

        def count_digest(items):
            nonlocal hashes
            hashes += 1
            return actual_digest(items)

        monkeypatch.setattr(input_frames, "payload_digest", count_digest)
        now = datetime.now(UTC)
        initial = HarnessEvent(
            thread_id=claim.thread_id,
            run_id=claim.run_id,
            sequence=1,
            occurred_at=now,
            event=ModelInputEvent(
                content=[TextContent("outside", metadata={"display": False}), *owned, TextContent("suffix")]
            ),
        )
        events = publisher.observe(initial)
        assert "outside" in str(events) and "suffix" in str(events)
        assert any(event.get("metadata", {}).get("display") is False for event in events)
        assert "outside" not in str(publisher.fold.display.segments[-1].items)
        assert "suffix" in str(publisher.fold.display.segments[-1].items)
        assert "owned" not in str(events)
        assert "owned-image" not in str(events)
        assert hashes == 1  # Many projected text chunks validate their source once.

        damaged = deepcopy(owned)
        damaged[0].metadata["display"] = False
        before = publisher.fold.display.segments[-1].event_sequence
        with pytest.raises(ServiceError):
            publisher.observe(
                HarnessEvent(
                    thread_id=claim.thread_id,
                    run_id=claim.run_id,
                    sequence=2,
                    occurred_at=now,
                    event=ModelInputEvent(content=damaged),
                )
            )
        assert publisher.fold.display.segments[-1].event_sequence == before

        enqueued = HarnessEvent(
            thread_id=claim.thread_id,
            run_id=claim.run_id,
            sequence=3,
            occurred_at=now,
            event=EnqueuedMessagesEvent(
                enqueue_id="mixed",
                messages=(
                    ModelRequest(
                        parts=[
                            UserPromptPart(
                                [TextContent("adjacent"), *owned, BinaryContent(b"other-image", media_type="image/png")]
                            )
                        ]
                    ),
                ),
            ),
        )
        events = publisher.observe(enqueued)
        assert "a13n.pydantic_ai.enqueued_messages" in str(events)
        assert "adjacent" in str(events)
        assert "owned" not in str(events)
        assert sum(event.get("name") == "a13n.input.media" for event in events) == 1
        assert "adjacent" in str(publisher.fold.display.segments[-1].items)
        assert any(
            item.get("type") == "event" and item["event"].get("name") == "a13n.input.media"
            for item in publisher.fold.display.segments[-1].items
        )
        assert hashes == 2

        async def model(messages, info):
            yield "answer"

        executable = HarnessBuilder().build(
            AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(_OverlayCapability(),),
            )
        )
        native_publisher = Publisher(
            storage, service.app.state.objects, claim, selected, options, max_bytes=1048576, max_events=1000, timeout=3
        )
        native_publisher.fold.display.segments.append(Segment(attempt_id=claim.attempt_id, attempt_number=claim.number))
        native = []
        async with executable.stream("prompt") as stream:
            async for item in stream:
                native.extend(native_publisher.observe(item))
        overlay = [
            event
            for event in native
            if event.get("input_source") is not None and event.get("metadata", {}).get("source_id") == "test.overlay"
        ]
        assert overlay and all(event["metadata"]["display"] is False for event in overlay)
        assert "Internal context" in str(overlay)
        assert "Internal context" not in str(native_publisher.fold.display.segments[-1].items)
        assert "answer" in str(native_publisher.fold.display.segments[-1].items)
    finally:
        await publisher.close()


@pytest.mark.parametrize("recover_candidate", [False, True])
async def test_awaited_checkpoint_flushes_delayed_display_then_confirms_input(public_service, recover_candidate):
    service = public_service
    response = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "publish"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "Hello"}]},
        },
    )
    assert response.status_code == 201, response.text
    storage = service.app.state.storage
    claim = await claim_run(storage, worker_id="worker-test", worker_build="test", lease_seconds=3)
    assert claim is not None
    entry_id = response.json()["entry"]["id"]
    async with short_session(storage) as session:
        run = await session.get(RunRow, claim.run_id)
        revision = await session.get(AgentRevisionRow, run.agent_revision_id)
        selection = AgentSelection(
            agent_id=run.agent_id, revision_id=revision.id, digest=revision.digest, config=revision.config
        )
        options = RunOptions.model_validate(run.options)
    objects = service.app.state.objects
    publisher = Publisher(storage, objects, claim, selection, options, max_bytes=1048576, max_events=1000, timeout=3)
    try:
        await publisher.initialize(HarnessState.new())

        effects = []
        calls = 0

        async def unsafe_write():
            checkpoint, _, _ = publisher.selected()
            assert any(
                isinstance(part, ToolCallPart) and part.tool_call_id == "unsafe-1"
                for message in checkpoint.state.message_history
                if isinstance(message, ModelResponse)
                for part in message.parts
            )
            assert "unsafe-1" in publisher.display_object.content.decode()
            effects.append("effect")
            return "done"

        async def model(messages, info):
            nonlocal calls
            calls += 1
            async with short_session(storage) as session:
                entry = await session.get(InboxEntryRow, entry_id)
                assert entry.status == "consumed"
                assert entry.incorporated_checkpoint_seq <= publisher.checkpoint.sequence
            checkpoint, _, display_ref = publisher.selected()
            assert checkpoint.receipts == (entry_id,)
            assert display_ref.sequence >= checkpoint.display_cut.sequence
            if calls == 1:
                yield {0: DeltaToolCall(name="unsafe_write", json_args="{}", tool_call_id="unsafe-1")}
            else:
                yield "Answer"

        prepared = PreparedInputs(claim.run_id)
        publisher.prepared = prepared
        capability = CheckpointCapability(claim.run_id, publisher.publish, prepared=prepared)
        executable = HarnessBuilder().build(
            AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(capability, Capability(id="tools", tools=[unsafe_write])),
            )
        )
        checked_barrier = False
        async with executable.stream(
            prepared.offer(entry_id, [TextContent("Hello")]), previous_state=publisher.checkpoint.state
        ) as stream:
            await start(storage, claim, harness_run_id=stream.run_id)
            async for item in stream:
                await asyncio.sleep(0.01)  # Observer lag must delay publication, never establish receipts itself.
                if (
                    not checked_barrier
                    and isinstance(item, HarnessEvent)
                    and isinstance(item.event, HarnessExtensionEvent)
                    and isinstance(item.event.payload, dict)
                    and item.event.payload.get("type") == "a13n.service.display_barrier"
                ):
                    publisher.observe(replace(item, run_id="run_unrelated"))
                    publisher.observe(
                        replace(
                            item,
                            event=item.event.model_copy(
                                update={"payload": {"type": "a13n.service.display_barrier", "id": "unknown"}}
                            ),
                        )
                    )
                    await asyncio.sleep(0.02)
                    assert calls == 0 and publisher.checkpoint.receipts == ()
                    async with short_session(storage) as session:
                        assert (await session.get(InboxEntryRow, entry_id)).status == "assigned"
                    checked_barrier = True
                publisher.observe(item)
            assert checked_barrier
            assert stream.result is not None and stream.result.state is not None
            await publisher.finalize(
                stream.result.state, tuple(capability.receipts), output=stream.result.output_or_raise()
            )
        checkpoint, state_ref, display_ref = publisher.selected()
        assert effects == ["effect"]
        assert checkpoint.candidate == "completed" and checkpoint.output == "Answer"
        assert state_ref.sequence == checkpoint.sequence
        assert display_ref.sequence == checkpoint.display_cut.sequence
        assert any(item.get("text") == "Answer" for item in publisher.fold.display.segments[-1].items)
        async with short_session(storage) as session:
            entry = await session.get(InboxEntryRow, entry_id)
            assert entry.incorporated_checkpoint_seq < checkpoint.sequence  # First receipt is immutable.
        if not recover_candidate:
            await asyncio.sleep(3)
            with pytest.raises(LeaseLost):
                await heartbeat(storage, claim, lease_seconds=3)
            unchanged = await objects.read(publisher.state_object.key)
            with pytest.raises(LeaseLost):
                await inputs.confirm(storage, claim, checkpoint)
            with pytest.raises(LeaseLost):
                await seal.continuation(storage, objects, claim, state_ref, display_ref)
            with pytest.raises(LeaseLost):
                await seal.failed(storage, claim, code="unavailable", message="Stale failure")
            assert await objects.read(publisher.state_object.key) == unchanged
        if recover_candidate:
            await publisher.close()
            async with asyncio.timeout(5):
                while not await seal.expire(storage, claim.run_id, backoff_seconds=0):
                    await asyncio.sleep(0.03)
            next_claim = await claim_run(storage, worker_id="recovery", worker_build="test", lease_seconds=30)
            assert next_claim is not None and next_claim.number == 2
            await execute(
                storage,
                objects,
                next_claim,
                config=service.app.state.settings,
                redis=service.app.state.redis,
                catalog=service.app.state.model_catalog,
                tool_catalog=service.app.state.tool_catalog,
                keys=service.app.state.key_ring,
                endpoint_policy=service.app.state.endpoint_policy,
                admission=service.app.state.admission,
            )
            final = await service.client.get(f"{service.workspace_path}/runs/{claim.run_id}/items")
            assert final.status_code == 200, final.text
            assert final.json()["status"] == "completed" and final.json()["output"] == {"text": "Answer"}
            assert calls == 2 and effects == ["effect"]
            assert len(final.json()["segments"]) == 1  # Recovery sealed the candidate without another execution.
        assert storage.engine.pool.checkedout() == 0
    finally:
        await publisher.close()
