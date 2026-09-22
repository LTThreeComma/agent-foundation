"""Real replay transport timeouts never substitute for durable execution authority."""

import asyncio

import pytest
from a13n_service.runs.streams import AttemptStream
from a13n_service.runs.worker import Worker
from redis.asyncio import Redis

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("fault_at", ["create", "append"])
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_worker_survives_real_replay_timeout_and_reestablishes_durable_floor(
    public_service, redis_url, monkeypatch, caplog, fault_at
):
    service, app = public_service, public_service.app
    config = app.state.settings.model_copy(
        update={
            "redis": app.state.settings.redis.model_copy(update={"timeout": 0.05}),
            "worker": app.state.settings.worker.model_copy(
                update={"slots": 1, "scan_seconds": 0.1, "lease_seconds": 6}
            ),
        }
    )
    submitted = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "replay-outage"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "[long] [slow] [interruptible] Replay transport proof"}]},
        },
    )
    assert submitted.status_code == 201
    run_id = submitted.json()["run"]["id"]
    items_url = f"{service.workspace_path}/runs/{run_id}/items"
    original_create, original_append = AttemptStream.create, AttemptStream.append
    failures, floors, appended = [], [], []
    injected = False
    async with Redis.from_url(redis_url, decode_responses=True) as admin:

        async def fault(call):
            nonlocal injected
            injected = True
            await admin.execute_command("CLIENT", "PAUSE", 5000, "WRITE")
            try:
                await call
            except TimeoutError as error:
                failures.append(type(error).__name__)
                snapshot = (await service.client.get(items_url)).json()
                response = await service.client.get(
                    f"{service.workspace_path}/runs/{run_id}/events", params={"cursor": snapshot["cursor"]}
                )
                assert response.status_code == 200 and "event: retry_later" in response.text
                raise
            finally:
                await admin.execute_command("CLIENT", "UNPAUSE")

        async def create(stream, floor):
            if fault_at == "create" and not injected:
                await fault(original_create(stream, floor))
                return
            # Reset may publish only after the authoritative covering display write.
            durable = (await service.client.get(items_url)).json()
            assert durable["segments"][-1]["event_sequence"] >= floor
            await original_create(stream, floor)
            assert int(await admin.hget(stream.keys[1], "floor")) == floor
            floors.append(floor)

        async def append(stream, sequence, event):
            if fault_at == "append" and not injected and event["type"] == "TEXT_MESSAGE_CONTENT":
                await fault(original_append(stream, sequence, event))
                return 0
            result = await original_append(stream, sequence, event)
            if result == 1:
                appended.append((sequence, floors[-1]))
                assert sequence > floors[-1]
            return result

        monkeypatch.setattr(AttemptStream, "create", create)
        monkeypatch.setattr(AttemptStream, "append", append)
        worker = Worker(
            app.state.storage,
            app.state.objects,
            app.state.redis,
            config=config,
            catalog=app.state.model_catalog,
            tool_catalog=app.state.tool_catalog,
            keys=app.state.key_ring,
            endpoint_policy=app.state.endpoint_policy,
            admission=app.state.admission,
        )
        task = asyncio.create_task(worker.run())
        try:
            async with asyncio.timeout(25):
                while True:
                    view = (await service.client.get(items_url)).json()
                    if view["complete"]:
                        break
                    await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert failures == ["TimeoutError"]
    assert view["status"] == "completed", (view, [getattr(record, "error_type", None) for record in caplog.records])
    assert len(view["inputs"]) == 1 and view["inputs"][0]["status"] == "consumed"
    texts = [item for segment in view["segments"] for item in segment["items"] if item["type"] == "text"]
    assert len(texts) == 1 and texts[0]["complete"]
    assert texts[0]["text"] == view["output"]["text"]
    assert floors[-1] > 0 and appended
    assert any(sequence > floors[-1] for sequence, _ in appended)
