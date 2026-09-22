"""Production claim loops with real Redis wake loss/reconnect and real HTTP executions."""

import asyncio
from datetime import timedelta

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.runs.wakeups import KEY, notify
from a13n_service.runs.worker import Worker
from redis.asyncio import Redis
from sqlalchemy import func, select, update

pytestmark = pytest.mark.anyio


def worker(service, *, scan):
    app = service.app
    config = app.state.settings.model_copy(
        update={
            "worker": app.state.settings.worker.model_copy(
                update={"slots": 1, "scan_seconds": scan, "lease_seconds": 6}
            )
        }
    )
    return Worker(
        app.state.storage,
        app.state.objects,
        app.state.redis,
        config=config,
        catalog=app.state.model_catalog,
        keys=app.state.key_ring,
        endpoint_policy=app.state.endpoint_policy,
        admission=app.state.admission,
    )


async def submit(service, key):
    response = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": key},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "[interruptible] " + key}]},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["run"]["id"]


@pytest.mark.parametrize("wake_storm", [False, True])
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_blocked_workers_survive_lost_hint_due_time_storm_and_redis_reconnect(
    public_service, redis_url, wake_storm
):
    service, app = public_service, public_service.app
    runs = [await submit(service, f"future-{index}") for index in range(2)]
    async with transaction(app.state.storage) as session:
        due = (await session.scalar(select(func.clock_timestamp()))) + timedelta(seconds=60)
        await session.execute(update(RunRow).where(RunRow.id.in_(runs)).values(available_at=due))
    await app.state.redis.delete(KEY)
    workers = [worker(service, scan=0.2), worker(service, scan=0.2)]
    tasks = []
    async with Redis.from_url(redis_url, decode_responses=True) as admin, Redis.from_url(redis_url) as doomed:
        popped = asyncio.create_task(doomed.blpop(KEY, timeout=5))
        try:
            async with asyncio.timeout(5):
                while sum(client["cmd"] == "blpop" for client in await admin.client_list()) < 1:
                    await asyncio.sleep(0.01)
            tasks = [asyncio.create_task(value.run()) for value in workers]
            async with asyncio.timeout(5):
                while sum(client["cmd"] == "blpop" for client in await admin.client_list()) < 3:
                    await asyncio.sleep(0.01)
            async with transaction(app.state.storage) as session:
                due = (await session.scalar(select(func.clock_timestamp()))) + timedelta(seconds=0.7)
                await session.execute(update(RunRow).where(RunRow.id.in_(runs)).values(available_at=due))
            await notify(app.state.redis, timeout=1)
            assert await popped is not None
            await doomed.aclose()  # This consumer cannot claim or forward its lost popped hint.

            async def storm():
                async with asyncio.timeout(4):
                    for _ in range(150):
                        await notify(app.state.redis, timeout=0.1)
                        assert await admin.llen(KEY) <= 1
                        await asyncio.sleep(0.01)

            storm_task = asyncio.create_task(storm() if wake_storm else asyncio.sleep(0))
            try:
                async with asyncio.timeout(5):
                    while True:
                        async with short_session(app.state.storage) as session:
                            attempts = (await session.scalars(select(AttemptRow))).all()
                        assert all(len(value._active) <= 1 for value in workers)
                        if len(attempts) == 2:
                            assert all(attempt.created_at >= due for attempt in attempts)
                            assert len({attempt.worker_id for attempt in attempts}) == 2
                            break
                        await asyncio.sleep(0.02)
                await storm_task
                if not wake_storm:
                    assert await admin.llen(KEY) == 0
            finally:
                storm_task.cancel()
                await asyncio.gather(storm_task, return_exceptions=True)
            async with asyncio.timeout(12):
                while True:
                    async with short_session(app.state.storage) as session:
                        statuses = list(await session.scalars(select(RunRow.status).where(RunRow.id.in_(runs))))
                    if statuses == ["completed", "completed"]:
                        break
                    await asyncio.sleep(0.03)
            async with asyncio.timeout(3):
                while sum(client["cmd"] == "blpop" for client in await admin.client_list()) < 2:
                    await asyncio.sleep(0.01)
            blocked = [client for client in await admin.client_list() if client["cmd"] == "blpop"]
            for client in blocked:
                assert await admin.client_kill_filter(_id=client["id"]) == 1
            # Actual Redis writes stall, yet the HTTP command must commit and return after its hint timeout.
            previous = app.state.settings
            app.state.settings = previous.model_copy(
                update={"redis": previous.redis.model_copy(update={"timeout": 0.05})}
            )
            await admin.execute_command("CLIENT", "PAUSE", 5000, "WRITE")
            stalled = asyncio.create_task(admin.set("test:paused-write", "probe"))
            try:
                third = await submit(service, "reconnect")
                assert not stalled.done()
                async with short_session(app.state.storage) as session:
                    assert await session.get(RunRow, third) is not None
            finally:
                await admin.execute_command("CLIENT", "UNPAUSE")
                await stalled
                app.state.settings = previous
            await admin.delete(KEY)
            async with asyncio.timeout(10):
                while True:
                    result = await service.client.get(f"{service.workspace_path}/runs/{third}/items")
                    if result.json()["complete"]:
                        break
                    await asyncio.sleep(0.03)
            assert result.json()["status"] == "completed", result.text
            assert all(not task.done() for task in tasks)
        finally:
            popped.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(popped, *tasks, return_exceptions=True)


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_slot_release_claims_without_waiting_for_marker_or_periodic_scan(public_service):
    service, app = public_service, public_service.app
    runs = [await submit(service, f"slot-{index}") for index in range(2)]
    await app.state.redis.delete(KEY)
    instance = worker(service, scan=10)
    task = asyncio.create_task(instance.run())
    try:
        async with asyncio.timeout(12):
            while True:
                assert len(instance._active) <= 1
                async with short_session(app.state.storage) as session:
                    rows = (await session.scalars(select(RunRow).where(RunRow.id.in_(runs)))).all()
                    attempts = (await session.scalars(select(AttemptRow).order_by(AttemptRow.created_at))).all()
                if len(attempts) == 2:
                    first = next(row for row in rows if row.id == attempts[0].run_id)
                    assert first.status == "completed"
                    assert (attempts[1].created_at - first.sealed_at).total_seconds() < 2
                    break
                assert len(attempts) <= 1
                await asyncio.sleep(0.03)
        assert await app.state.redis.llen(KEY) == 0
        async with asyncio.timeout(8):
            while True:
                async with short_session(app.state.storage) as session:
                    statuses = list(await session.scalars(select(RunRow.status).where(RunRow.id.in_(runs))))
                if statuses == ["completed", "completed"]:
                    break
                await asyncio.sleep(0.03)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
