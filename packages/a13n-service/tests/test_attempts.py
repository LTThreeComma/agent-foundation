"""Real PostgreSQL claim contention and fresh-time authority after a lock wait."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.runs import attempts as attempt_service
from a13n_service.runs.attempts import LeaseLost, claim_run, heartbeat
from a13n_service.runs.events import EventRow
from a13n_service.runs.tables import AttemptRow, RunRow
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio


async def test_claim_contention_and_expired_heartbeat_after_lock_wait(public_service):
    service = public_service
    response = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "claim"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "Hello"}]},
        },
    )
    assert response.status_code == 201, response.text
    storage = service.app.state.storage
    claims = await asyncio.gather(
        *[claim_run(storage, worker_id=f"worker-{number}", worker_build="test", lease_seconds=2) for number in range(2)]
    )
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    claim = winners[0]
    extended = await heartbeat(storage, claim, lease_seconds=2)
    assert extended > claim.lease_expires_at
    async with transaction(storage) as session:
        await session.get(RunRow, claim.run_id, with_for_update=True)
        task = asyncio.create_task(heartbeat(storage, claim, lease_seconds=2))
        await asyncio.sleep(0.05)
        assert not task.done()
        # The waiting heartbeat began before expiry; release the row only after DB time expires.
        while (await session.execute(select(func.clock_timestamp()))).scalar_one() <= extended:
            await asyncio.sleep(0.05)
    with pytest.raises(LeaseLost):
        await asyncio.wait_for(task, timeout=5)
    with pytest.raises(LeaseLost):
        await heartbeat(storage, claim, lease_seconds=2)
    async with short_session(storage) as session:
        attempt = await session.get(AttemptRow, claim.attempt_id)
        assert attempt.lease_expires_at == extended
        assert await session.scalar(select(func.count()).select_from(AttemptRow)) == 1
        events = (await session.scalars(select(EventRow).order_by(EventRow.seq))).all()
        assert [event.kind for event in events] == ["run.accepted", "run_attempt.leased", "run.running"]
        assert [event.seq for event in events] == [1, 2, 3]
    assert storage.engine.pool.checkedout() == 0


async def test_creation_timestamp_uses_claim_clock_not_transaction_start(public_service, monkeypatch):
    service = public_service
    response = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "claim-clock"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "Hello"}]},
        },
    )
    assert response.status_code == 201
    storage = service.app.state.storage
    started = due = None

    @asynccontextmanager
    async def earlier_transaction(owner):
        nonlocal started, due
        async with transaction(owner) as session:
            started = await session.scalar(select(func.now()))
            due = started + timedelta(milliseconds=30)
            run = await session.get(RunRow, response.json()["run"]["id"])
            run.available_at = due
            await session.flush()
            while await session.scalar(select(func.clock_timestamp())) < due:
                await asyncio.sleep(0.01)
            yield session

    monkeypatch.setattr(attempt_service, "transaction", earlier_transaction)
    claimed = await claim_run(storage, worker_id="clock-proof", worker_build="test", lease_seconds=2)
    assert claimed is not None
    async with short_session(storage) as session:
        attempt = await session.get(AttemptRow, claimed.attempt_id)
        assert started < due <= attempt.created_at
        assert attempt.updated_at == attempt.created_at == attempt.heartbeat_at
