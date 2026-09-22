"""Late input and actual completion/interrupt contention at the thread lock."""

import asyncio

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs import inputs, seal
from a13n_service.runs.advance import advance_one
from a13n_service.runs.attempts import claim_run
from a13n_service.runs.execute import execute
from a13n_service.runs.schemas import Checkpoint
from a13n_service.runs.tables import RunRow, ThreadRow
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def submit(service, key, thread=None):
    response = await service.client.post(
        service.workspace_path + (f"/threads/{thread}/inbox" if thread else "/threads"),
        headers={"Idempotency-Key": key},
        json={"kind": "message", "agent_id": service.agent_id, "payload": {"content": [{"type": "text", "text": key}]}},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def run(service, claim):
    app = service.app
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


@pytest.mark.parametrize("assigned", [False, True])
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_final_boundary_steer_returns_pending_and_runs_once_as_successor(public_service, monkeypatch, assigned):
    service, app = public_service, public_service.app
    initial = await submit(service, "first")
    claim = await claim_run(app.state.storage, worker_id="final", worker_build="test", lease_seconds=30)
    reached, release = asyncio.Event(), asyncio.Event()
    original = app.state.objects.replace_snapshot

    async def hold_final(key, content, **kwargs):
        if key.endswith("/state.json") and Checkpoint.model_validate_json(content).candidate:
            reached.set()
            await release.wait()
        return await original(key, content, **kwargs)

    monkeypatch.setattr(app.state.objects, "replace_snapshot", hold_final)
    task = asyncio.create_task(run(service, claim))
    try:
        await asyncio.wait_for(reached.wait(), 10)
        late = await submit(service, "late steer", initial["thread_id"])
        if assigned:
            # Assignment is a real concurrent SQL operation; no native incorporation can follow this final cut.
            bound = await inputs.assign_steers(app.state.storage, claim, max_count=3, max_bytes=1048576)
            assert [identity for identity, _ in bound] == [late["entry"]["id"]]
        release.set()
        await task
        inbox = await service.client.get(f"{service.workspace_path}/threads/{initial['thread_id']}/inbox")
        assert [(item["id"], item["status"]) for item in inbox.json()["items"]] == [
            (initial["entry"]["id"], "consumed"),
            (late["entry"]["id"], "pending"),
        ]
        assert inbox.json()["items"][1]["assigned_run_id"] is None
        assert await advance_one(app.state.storage, max_attempts=3, policy=None, keys=app.state.key_ring)
        second = await claim_run(app.state.storage, worker_id="next", worker_build="test", lease_seconds=30)
        assert second is not None and second.run_id != claim.run_id
        await run(service, second)
        async with short_session(app.state.storage) as session:
            successor = await session.get(RunRow, second.run_id)
            assert successor.parent_run_id == claim.run_id and successor.source_entry_id == late["entry"]["id"]
        first_view = await service.client.get(f"{service.workspace_path}/runs/{claim.run_id}/items")
        second_view = await service.client.get(f"{service.workspace_path}/runs/{second.run_id}/items")
        assert [item["id"] for item in first_view.json()["inputs"]] == [initial["entry"]["id"]]
        assert [(item["id"], item["status"]) for item in second_view.json()["inputs"]] == [
            (late["entry"]["id"], "consumed")
        ]
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("winner", ["completion", "interrupt"])
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_completion_and_interrupt_contend_on_real_thread_lock(public_service, monkeypatch, winner):
    service, app = public_service, public_service.app
    initial = await submit(service, "race")
    claim = await claim_run(app.state.storage, worker_id="race", worker_build="test", lease_seconds=30)
    reached, release = asyncio.Event(), asyncio.Event()
    original = app.state.objects.read

    async def hold_seal_read(key):
        result = await original(key)
        if (
            result is not None
            and key.endswith("/state.json")
            and Checkpoint.model_validate_json(result.content).candidate
        ):
            reached.set()
            await release.wait()
        return result

    monkeypatch.setattr(app.state.objects, "read", hold_seal_read)
    completion = asyncio.create_task(run(service, claim))
    interrupt = None
    try:
        await asyncio.wait_for(reached.wait(), 10)
        async with transaction(app.state.storage) as session:
            await session.get(ThreadRow, initial["thread_id"], with_for_update=True)

            async def wait_locks(count):
                async with asyncio.timeout(5):
                    while (
                        await session.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
                            )
                        )
                        < count
                    ):
                        await session.execute(text("SELECT pg_stat_clear_snapshot()"))
                    await asyncio.sleep(0.01)

            def request_interrupt():
                return asyncio.create_task(
                    service.client.post(f"{service.workspace_path}/runs/{claim.run_id}/interrupt")
                )

            if winner == "completion":
                release.set()
                await wait_locks(1)
                interrupt = request_interrupt()
            else:
                interrupt = request_interrupt()
                await wait_locks(1)
                release.set()
            await wait_locks(2)
            assert not completion.done() and not interrupt.done()
        response = await interrupt
        if winner == "completion":
            await completion
            assert response.status_code == 409
        else:
            assert response.status_code == 200
            with pytest.raises(ServiceError, match="Cancellation"):
                await completion
            await seal.failed(app.state.storage, claim, code="cancelled", message="Cancelled")
        final = await service.client.get(f"{service.workspace_path}/runs/{claim.run_id}/items")
        assert final.json()["status"] == ("completed" if winner == "completion" else "cancelled")
        assert final.json()["inputs"][0]["status"] == "consumed"
        async with short_session(app.state.storage) as session:
            thread = await session.get(ThreadRow, initial["thread_id"])
            assert thread.current_run_id is None
            assert thread.head_run_id == (claim.run_id if winner == "completion" else None)
    finally:
        release.set()
        for task in (completion, interrupt):
            if task is not None:
                task.cancel()
        await asyncio.gather(*[task for task in (completion, interrupt) if task is not None], return_exceptions=True)
