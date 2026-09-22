"""Cancellation preserves failed-source disposition and requires explicit restart."""

import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session
from a13n_service.runs.advance import advance_one
from a13n_service.runs.attempts import claim_run
from a13n_service.runs.tables import RunRow, ThreadRow
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio


async def test_accepted_interrupt_is_atomic_idempotent_and_does_not_release_pending(public_service):
    service = public_service
    client, path, storage = service.client, service.workspace_path, service.app.state.storage
    body = {
        "kind": "message",
        "agent_id": service.agent_id,
        "payload": {"content": [{"type": "text", "text": "Write a poem"}]},
    }
    first = await client.post(path + "/threads", headers={"Idempotency-Key": "first"}, json=body)
    assert first.status_code == 201, first.text
    run_id, thread_id = first.json()["run"]["id"], first.json()["thread_id"]
    pending = await client.post(f"{path}/threads/{thread_id}/inbox", headers={"Idempotency-Key": "pending"}, json=body)
    assert pending.status_code == 201 and pending.json()["entry"]["status"] == "pending"
    for _ in range(2):
        interrupted = await client.post(f"{path}/runs/{run_id}/interrupt")
        assert interrupted.status_code == 200, interrupted.text
        assert interrupted.json()["status"] == "cancelled"
        assert interrupted.json()["cancel_requested_at"] is not None
    assert not await advance_one(storage, max_attempts=3, policy=None)
    assert await claim_run(storage, worker_id="none", worker_build="test", lease_seconds=30) is None
    cancelled = await client.get(f"{path}/runs/{run_id}/items")
    assert cancelled.json()["inputs"][0]["status"] == "failed"
    assert cancelled.json()["inputs"][0]["incorporated_checkpoint_seq"] is None
    restart = await client.post(
        f"{path}/threads/{thread_id}/inbox",
        headers={"Idempotency-Key": "restart"},
        json={**body, "payload": {"content": [{"type": "text", "text": "Hello"}]}},
    )
    assert restart.status_code == 201, restart.text
    assert restart.json()["run"]["source_entry_id"] == restart.json()["entry"]["id"]
    assert restart.json()["run"]["parent_run_id"] is None
    inbox = await client.get(f"{path}/threads/{thread_id}/inbox")
    assert [entry["status"] for entry in inbox.json()["items"]] == ["failed", "pending", "assigned"]
    async with short_session(storage) as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditEventRow).where(AuditEventRow.action == "run.interrupt")
            )
            == 1
        )
        thread = await session.get(ThreadRow, thread_id)
        assert thread.head_run_id is None and thread.last_run_id == run_id
        assert await session.scalar(select(func.count()).select_from(RunRow)) == 2
