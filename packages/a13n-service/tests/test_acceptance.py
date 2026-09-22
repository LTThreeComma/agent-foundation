"""Atomic public input acceptance, request-key contention and committed lifecycle facts."""

import asyncio

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.runs.events import EventCursorRow, EventRow
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.anyio


async def test_concurrent_public_replay_conflict_and_bounded_inbox_leave_no_residue(public_service, monkeypatch):
    service = public_service
    client, path, storage = service.client, service.workspace_path, service.app.state.storage
    body = {
        "kind": "message",
        "agent_id": service.agent_id,
        "payload": {"content": [{"type": "text", "text": "First"}]},
    }
    from a13n_service.runs import acceptance

    original_replay = acceptance.replay
    arrived = 0
    all_arrived = asyncio.Event()

    async def synchronize_preflight(*args, **kwargs):
        nonlocal arrived
        result = await original_replay(*args, **kwargs)
        if result is None and arrived < 4:
            arrived += 1
            if arrived == 4:
                all_arrived.set()
            await asyncio.wait_for(all_arrived.wait(), timeout=10)
        return result

    monkeypatch.setattr(acceptance, "replay", synchronize_preflight)
    results = await asyncio.gather(
        *[client.post(path + "/threads", json=body, headers={"Idempotency-Key": "same-request"}) for _ in range(4)]
    )
    assert sorted(result.status_code for result in results) == [200, 200, 200, 201], [r.text for r in results]
    assert arrived == 4
    ids = {(r.json()["thread_id"], r.json()["entry"]["id"], r.json()["run"]["id"]) for r in results}
    assert len(ids) == 1
    thread_id, entry_id, run_id = ids.pop()
    assert all(r.json()["entry"]["status"] == "assigned" for r in results)
    replay = await client.post(path + "/threads", json=body, headers={"Idempotency-Key": "same-request"})
    assert replay.status_code == 200 and replay.json()["run"]["id"] == run_id
    conflict = await client.post(
        path + "/threads", json={**body, "delivery": "next_run"}, headers={"Idempotency-Key": "same-request"}
    )
    assert conflict.status_code == 409
    assert (await client.post(path + "/threads", json=body)).status_code == 400
    for index in range(2):
        queued = await client.post(
            f"{path}/threads/{thread_id}/inbox", json=body, headers={"Idempotency-Key": f"queued-{index}"}
        )
        assert queued.status_code == 201, queued.text
        assert queued.json()["entry"]["status"] == "pending" and queued.json()["run"] is None
    full = await client.post(f"{path}/threads/{thread_id}/inbox", json=body, headers={"Idempotency-Key": "overflow"})
    assert full.status_code == 429, full.text
    # Capacity and later archive state do not invalidate evidence of the original request.
    async with transaction(storage) as session:
        await session.execute(
            update(ThreadRow).where(ThreadRow.id == thread_id).values(archived_at=func.clock_timestamp())
        )
    archived_replay = await client.post(path + "/threads", json=body, headers={"Idempotency-Key": "same-request"})
    assert archived_replay.status_code == 200, archived_replay.text
    async with short_session(storage) as session:
        for row, count in ((SessionRow, 1), (ThreadRow, 1), (RunRow, 1), (InboxEntryRow, 3), (EventRow, 1)):
            assert await session.scalar(select(func.count()).select_from(row)) == count
        run = await session.get(RunRow, run_id)
        entry = await session.get(InboxEntryRow, entry_id)
        assert run.source_entry_id == entry.id and entry.assigned_run_id == run.id
        event = await session.scalar(select(EventRow))
        cursor = await session.get(EventCursorRow, service.initialized.workspace_id)
        assert event.kind == "run.accepted" and event.seq == cursor.last_seq == 1
        assert event.entity_seq == run.lifecycle_seq == 1
    with pytest.raises(DBAPIError, match="immutable"):
        async with transaction(storage) as session:
            await session.execute(
                update(InboxEntryRow).where(InboxEntryRow.id == entry_id).values(payload={"changed": True})
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with transaction(storage) as session:
            await session.execute(update(EventRow).values(payload={"changed": True}))
    assert storage.engine.pool.checkedout() == 0
