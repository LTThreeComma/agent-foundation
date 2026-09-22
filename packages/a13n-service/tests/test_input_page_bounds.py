"""Accepted canonical inputs remain complete across byte-bounded public pages."""

import pytest
from a13n_service.infra.db import short_session
from a13n_service.runs import input_views
from a13n_service.runs.tables import InboxEntryRow
from sqlalchemy import String, cast, func, select

pytestmark = pytest.mark.anyio


async def test_large_accepted_input_has_complete_inbox_items_and_sse_bootstrap(public_service):
    service = public_service
    config = service.app.state.settings
    service.app.state.settings = config.model_copy(
        update={"control": config.control.model_copy(update={"inbox_bytes": 2097152})}
    )
    payload = {"content": [{"type": "text", "text": "x" * 65000} for _ in range(17)]}
    body = {"kind": "message", "agent_id": service.agent_id, "payload": payload}
    accepted = await service.client.post(
        service.workspace_path + "/threads", headers={"Idempotency-Key": "large"}, json=body
    )
    assert accepted.status_code == 201, accepted.text[:300]
    data = accepted.json()
    inbox_url = f"{service.workspace_path}/threads/{data['thread_id']}/inbox"
    tiny = {**body, "payload": {"content": [{"type": "text", "text": "next"}]}}
    queued = await service.client.post(inbox_url, headers={"Idempotency-Key": "tiny"}, json=tiny)
    assert queued.status_code == 201
    inbox = await service.client.get(inbox_url)
    assert inbox.status_code == 200, inbox.text[:300]
    assert [entry["payload"] for entry in inbox.json()["items"]] == [payload]
    assert inbox.json()["next_cursor"]
    second = await service.client.get(inbox_url, params={"cursor": inbox.json()["next_cursor"]})
    assert [entry["payload"] for entry in second.json()["items"]] == [tiny["payload"]]
    assert second.json()["next_cursor"] is None
    run_url = f"{service.workspace_path}/runs/{data['run']['id']}"
    items = await service.client.get(run_url + "/items")
    assert items.status_code == 200 and items.json()["inputs"][0]["payload"] == payload
    sse = await service.client.get(run_url + "/events")
    assert sse.status_code == 200 and "event: reset" in sse.text
    # Exact aggregate boundary includes both rows; one byte less produces truthful pagination.
    async with short_session(service.app.state.storage) as session:
        size = await session.scalar(
            select(func.sum(func.octet_length(cast(InboxEntryRow.payload, String)))).where(
                InboxEntryRow.thread_id == data["thread_id"]
            )
        )
        rows, cursor = await input_views.page(
            session, thread_id=data["thread_id"], run_id=None, limit=2, cursor=None, max_bytes=int(size)
        )
        assert len(rows) == 2 and cursor is None
        rows, cursor = await input_views.page(
            session, thread_id=data["thread_id"], run_id=None, limit=2, cursor=None, max_bytes=int(size) - 1
        )
        assert len(rows) == 1 and cursor is not None
    overflow = await service.client.post(inbox_url, headers={"Idempotency-Key": "over-capacity"}, json=body)
    assert overflow.status_code == 429
