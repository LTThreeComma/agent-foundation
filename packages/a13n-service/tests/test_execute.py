"""Actual HTTP model, Harness, usage and sealed continuation across public inputs."""

import pytest
from a13n_harness.usage import ModelUsageRecord
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs import usage
from a13n_service.runs.advance import advance_one
from a13n_service.runs.attempts import claim_run
from a13n_service.runs.execute import execute
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.runs.usage import UsageRow
from sqlalchemy import select

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("replay_count", [1, 512])
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_public_inputs_execute_real_model_and_continue_sealed_head(public_service, replay_count):
    service = public_service
    app, client, path = service.app, service.client, service.workspace_path
    config = app.state.settings.model_copy(
        update={"worker": app.state.settings.worker.model_copy(update={"stream_count": replay_count})}
    )

    def body(index):
        return {
            "kind": "message",
            "delivery": "next_run",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": f"Input {index}"}]},
        }

    previous_run, thread_id = None, None
    for index in range(2):
        if index:
            assert await advance_one(app.state.storage, max_attempts=3, policy=None, keys=app.state.key_ring)
        target = path + "/threads" if thread_id is None else f"{path}/threads/{thread_id}/inbox"
        submitted = await client.post(
            target,
            headers={"Idempotency-Key": f"execute-{index}"},
            json=body(index),
        )
        assert submitted.status_code == (200 if index else 201), submitted.text
        value = submitted.json()
        thread_id, run_id = value["thread_id"], value["run"]["id"]
        assert value["run"]["parent_run_id"] == previous_run
        if not index:
            queued = await client.post(
                f"{path}/threads/{thread_id}/inbox", headers={"Idempotency-Key": "execute-1"}, json=body(1)
            )
            assert queued.status_code == 201 and queued.json()["entry"]["status"] == "pending"

        claim = await claim_run(app.state.storage, worker_id="actual-execute", worker_build="test", lease_seconds=30)
        assert claim is not None and claim.run_id == run_id
        await execute(
            app.state.storage,
            app.state.objects,
            claim,
            config=config,
            redis=app.state.redis,
            catalog=app.state.model_catalog,
            tool_catalog=app.state.tool_catalog,
            keys=app.state.key_ring,
            endpoint_policy=app.state.endpoint_policy,
            admission=app.state.admission,
        )
        view = await client.get(f"{path}/runs/{run_id}/items")
        assert view.status_code == 200, view.text
        assert view.json()["status"] == "completed" and view.json()["complete"]
        assert view.json()["inputs"][0]["status"] == "consumed"
        assert view.json()["output"]["text"].startswith("This is a local scripted response")
        async with short_session(app.state.storage) as session:
            run = await session.get(RunRow, run_id)
            thread = await session.get(ThreadRow, thread_id)
            records = (await session.scalars(select(UsageRow).where(UsageRow.run_id == run_id))).all()
            assert len(records) == 1 and records[0].call_id
            assert run.usage_at_seal["requests"] == 1
            assert run.usage_at_seal["input_tokens"] == 20
            assert thread.head_run_id == run.id and thread.current_run_id is None
        from a13n_service.runs.streams import AttemptStream, Bounds

        replay = AttemptStream(app.state.redis, run_id, claim.number, Bounds())
        assert await app.state.redis.xlen(replay.keys[0]) <= replay_count
        floor = int(await app.state.redis.hget(replay.keys[1], "floor"))
        assert view.json()["segments"][-1]["event_sequence"] >= floor
        if replay_count == 1:
            assert floor > 0
        previous_run = run_id
    record = ModelUsageRecord.model_validate(records[0].record)
    await usage.ingest(app.state.storage, claim, record, model_id=records[0].model_id, price_snapshot=None)
    with pytest.raises(ServiceError, match="different immutable content"):
        await usage.ingest(
            app.state.storage,
            claim,
            record.model_copy(update={"source": "different"}),
            model_id=records[0].model_id,
            price_snapshot=None,
        )
    late = record.model_copy(update={"record_id": "usage_late", "response_ordinal": record.response_ordinal + 1})
    await usage.ingest(app.state.storage, claim, late, model_id=records[0].model_id, price_snapshot=None)
    current = await client.get(path + "/usage", params={"run_id": run_id})
    assert current.status_code == 200, current.text
    assert current.json()["current"]["requests"] == 2
    assert current.json()["at_seal"]["requests"] == 1
    assert app.state.storage.engine.pool.checkedout() == 0


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_actual_compaction_preserves_durable_input_receipts(public_service):
    from a13n_service.runs.schemas import Checkpoint
    from a13n_service.runs.snapshots import object_key

    service = public_service
    app, client, path = service.app, service.client, service.workspace_path
    agent_path = f"{path}/agents/{service.agent_id}"
    agent = await client.get(agent_path)
    revision = await client.get(agent_path + "/revisions/" + agent.json()["default_revision_id"])
    changed = await client.post(
        agent_path + "/revisions",
        headers={"If-Match": agent.headers["etag"]},
        json={"config": {**revision.json()["config"], "compaction_trigger_tokens": 1}},
    )
    assert changed.status_code == 201, changed.text

    def body(text):
        return {
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": text}]},
        }

    submitted = await client.post(path + "/threads", headers={"Idempotency-Key": "compact-source"}, json=body("Source"))
    assert submitted.status_code == 201, submitted.text
    first = submitted.json()
    queued = await client.post(
        f"{path}/threads/{first['thread_id']}/inbox",
        headers={"Idempotency-Key": "compact-steer"},
        json=body("Steer"),
    )
    assert queued.status_code == 201, queued.text
    claim = await claim_run(app.state.storage, worker_id="compaction", worker_build="test", lease_seconds=30)
    assert claim is not None
    await execute(
        app.state.storage,
        app.state.objects,
        claim,
        config=app.state.settings,
        redis=app.state.redis,
        catalog=app.state.model_catalog,
        tool_catalog=app.state.tool_catalog,
        keys=app.state.key_ring,
        endpoint_policy=app.state.endpoint_policy,
        admission=app.state.admission,
    )
    stored = await app.state.objects.read(object_key(claim.organization_id, claim.run_id, "state"))
    assert stored is not None
    checkpoint = Checkpoint.model_validate_json(stored.content)
    assert set(checkpoint.receipts) == {first["entry"]["id"], queued.json()["entry"]["id"]}
    view = await client.get(f"{path}/runs/{claim.run_id}/items")
    assert view.status_code == 200 and view.json()["status"] == "completed", view.text
    assert [entry["status"] for entry in view.json()["inputs"]] == ["consumed", "consumed"]
    async with short_session(app.state.storage) as session:
        records = (await session.scalars(select(UsageRow).where(UsageRow.run_id == claim.run_id))).all()
        assert len(records) >= 3, [record.record for record in records]
        assert all(record.call_id for record in records)
    from pydantic_ai.messages import ModelRequest

    assert any(
        isinstance(message, ModelRequest) and (message.metadata or {}).get("a13n.context") == "compaction"
        for message in checkpoint.state.message_history
    )
