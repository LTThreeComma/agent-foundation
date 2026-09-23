"""Real display/state CAS failure and takeover against the production executor."""

import asyncio

import httpx
import pytest
from a13n_service.infra.objects.local import ObjectConflict
from a13n_service.runs import input_preparation, inputs, seal
from a13n_service.runs.attempts import claim_run
from a13n_service.runs.execute import execute
from a13n_service.runs.input_frames import PreparedInputs
from a13n_service.runs.schemas import Checkpoint
from a13n_service.runs.snapshots import object_key

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("fault", ["failed_checkpoint", "takeover_before_checkpoint"])
@pytest.mark.parametrize("input_kind", ["text", "asset"])
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_display_ahead_of_state_remains_interrupted_after_recovery(
    public_service, monkeypatch, fault, model_url, input_kind
):
    service = public_service
    app = service.app
    if fault == "takeover_before_checkpoint":
        # Hold the real object write beyond natural lease expiry to exercise its late-writer fence.
        objects = type(app.state.settings.objects)(**{**app.state.settings.objects.model_dump(), "timeout": 60})
        app.state.settings = app.state.settings.model_copy(update={"objects": objects})
    if input_kind == "asset":
        staged = await service.client.post(
            service.workspace_path + "/uploads",
            headers={"Idempotency-Key": "publication-asset-upload"},
            files={"file": ("source.txt", b"never-refetch-after-receipt", "text/plain")},
        )
        assert staged.status_code == 200, staged.text
        asset = await service.client.post(
            service.workspace_path + "/assets",
            json={"upload_id": staged.json()["upload_id"], "name": "source.txt"},
        )
        assert asset.status_code == 201, asset.text
        content = [{"type": "asset", "asset_id": asset.json()["id"]}]
    else:
        content = [{"type": "text", "text": "One canonical source"}]
    response = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "publication-fault"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": content},
        },
    )
    assert response.status_code == 201
    run_id, entry_id = response.json()["run"]["id"], response.json()["entry"]["id"]
    lease_seconds = app.state.settings.worker.lease_seconds
    first = await claim_run(app.state.storage, worker_id="old", worker_build="test", lease_seconds=lease_seconds)
    assert first is not None
    state_key = object_key(first.organization_id, run_id, "state")
    original = app.state.objects.replace_snapshot
    reached, release = asyncio.Event(), asyncio.Event()

    async def replace(key, content, **kwargs):
        if key == state_key and kwargs["writer"] == first.number and Checkpoint.model_validate_json(content).candidate:
            reached.set()
            if fault == "failed_checkpoint":
                raise OSError("checkpoint PUT failed after display PUT")
            await release.wait()
        return await original(key, content, **kwargs)

    monkeypatch.setattr(app.state.objects, "replace_snapshot", replace)

    async def run(claim):
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

    task = asyncio.create_task(run(first))
    try:
        try:
            await asyncio.wait_for(reached.wait(), 10)
        except TimeoutError:
            if task.done():
                task.result()
            raise
        if fault == "failed_checkpoint":
            with pytest.raises(OSError):
                await task
        older = await app.state.objects.read(state_key)
        checkpoint = Checkpoint.model_validate_json(older.content)
        assert checkpoint.candidate is None and checkpoint.receipts == (entry_id,)
        before = await service.client.get(f"{service.workspace_path}/runs/{run_id}/items")
        assert before.status_code == 200, before.text
        assert before.json()["status"] == "running" and before.json()["output"] is None
        assert before.json()["inputs"][0]["status"] == "consumed"
        assert any(
            item.get("text", "").startswith("This is a local scripted")
            for item in before.json()["segments"][0]["items"]
        )
        assert before.json()["execution_checkpoint_cut"] == checkpoint.display_cut.model_dump(mode="json")
        async with asyncio.timeout(lease_seconds + 5):
            while not await seal.expire(app.state.storage, run_id, backoff_seconds=0):
                await asyncio.sleep(0.03)
        second = await claim_run(app.state.storage, worker_id="new", worker_build="test", lease_seconds=30)
        assert second is not None and second.number == 2
        assert await inputs.assigned_inputs(app.state.storage, second) == ()
        if input_kind == "asset":

            async def unexpected_refetch(*args, **kwargs):
                raise AssertionError("Incorporated Asset was fetched again")

            monkeypatch.setattr(input_preparation, "_asset", unexpected_refetch)
        # The exact older execution content still owns recovery; ahead display did not replace it.
        assert (await app.state.objects.read(state_key)).content == older.content
        await run(second)
        final_object = await app.state.objects.read(state_key)
        if fault == "takeover_before_checkpoint":
            release.set()
            with pytest.raises(ObjectConflict):
                await task
            assert await app.state.objects.read(state_key) == final_object
        after = await service.client.get(f"{service.workspace_path}/runs/{run_id}/items")
        assert after.status_code == 200, after.text
        value = after.json()
        assert value["status"] == "completed" and len(value["segments"]) == 2
        assert value["segments"][0]["interrupted"] and not value["segments"][1]["interrupted"]
        assert value["segments"][0]["execution_cut"] == checkpoint.display_cut.model_dump(mode="json")
        assert [(entry["id"], entry["status"]) for entry in value["inputs"]] == [(entry_id, "consumed")]
        assert value["output"]["text"].count("This is a local scripted response") == 1
        async with httpx.AsyncClient() as client:
            state = (await client.get(model_url.removesuffix("/v1") + "/fixture/model-state")).json()
        assert state["request_count"] == 2
        final_checkpoint = Checkpoint.model_validate_json(final_object.content)
        restored = PreparedInputs(run_id, receipts=(entry_id,))
        restored.restore(final_checkpoint.state.message_history)
        assert restored.incorporated(final_checkpoint.state.message_history) == (entry_id,)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_dispatched_checkpoint_finishes_after_terminal_without_reviving_input(
    public_service, monkeypatch, terminal
):
    from a13n_harness.errors import RunError

    service, app = public_service, public_service.app
    accepted = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "late-put"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "Must remain failed"}]},
        },
    )
    assert accepted.status_code == 201
    first = await claim_run(app.state.storage, worker_id="late", worker_build="test", lease_seconds=30)
    assert first is not None
    state_key = object_key(first.organization_id, first.run_id, "state")
    original = app.state.objects.replace_snapshot
    reached, release = asyncio.Event(), asyncio.Event()

    async def delayed(key, content, **kwargs):
        if key == state_key and Checkpoint.model_validate_json(content).receipts:
            reached.set()
            await release.wait()
        return await original(key, content, **kwargs)

    monkeypatch.setattr(app.state.objects, "replace_snapshot", delayed)
    task = asyncio.create_task(
        execute(
            app.state.storage,
            app.state.objects,
            first,
            config=app.state.settings,
            redis=app.state.redis,
            catalog=app.state.model_catalog,
            tool_catalog=app.state.tool_catalog,
            keys=app.state.key_ring,
            endpoint_policy=app.state.endpoint_policy,
            admission=app.state.admission,
        )
    )
    try:
        try:
            await asyncio.wait_for(reached.wait(), 10)
        except TimeoutError:
            if task.done():
                task.result()
            raise
        if terminal == "cancelled":
            response = await service.client.post(f"{service.workspace_path}/runs/{first.run_id}/interrupt")
            assert response.status_code == 200
        await seal.failed(app.state.storage, first, code="injected", message="Terminal precedes delayed PUT")
        before = await service.client.get(f"{service.workspace_path}/runs/{first.run_id}/items")
        assert before.json()["inputs"][0]["status"] == "failed"
        release.set()
        with pytest.raises(RunError, match="LeaseLost"):
            await task
        late = Checkpoint.model_validate_json((await app.state.objects.read(state_key)).content)
        assert late.receipts == (accepted.json()["entry"]["id"],)  # Physical PUT really completed after seal.
        after = await service.client.get(f"{service.workspace_path}/runs/{first.run_id}/items")
        assert after.status_code == 200, after.text
        assert after.json()["status"] == terminal and after.json()["complete"]
        assert after.json()["inputs"][0]["status"] == "failed"
        assert after.json()["inputs"][0]["incorporated_checkpoint_seq"] is None
        assert after.json()["output"] is None
        assert all(segment["interrupted"] for segment in after.json()["segments"])
        assert await claim_run(app.state.storage, worker_id="retry", worker_build="test", lease_seconds=30) is None
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
