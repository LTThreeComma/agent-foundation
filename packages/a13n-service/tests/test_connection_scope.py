"""Connection reference isolation and queued default-revision context validation."""

import pytest
from a13n_service.infra.db import transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.advance import advance_one
from a13n_service.runs.attempts import claim_run
from a13n_service.tenancy.tables import WorkspaceRow

from dev.fixtures.service_mcp import configure_mcp, execute_claim

pytestmark = pytest.mark.anyio


async def test_missing_and_foreign_connection_references_are_rejected(public_service, mcp_url, model_url):
    svc = public_service
    connection = await configure_mcp(svc, mcp_url, model_url)
    foreign = new_object_id("ws")
    async with transaction(svc.app.state.storage) as session:
        session.add(
            WorkspaceRow(id=foreign, organization_id=svc.initialized.organization_id, key="other", name="Other")
        )
    for suffix in ("", "/tools"):
        read = await svc.client.get(f"/api/v1/workspaces/{foreign}/connections/{connection}" + suffix)
        assert read.status_code == 404, read.text
    agent_path = svc.workspace_path + "/agents/" + svc.agent_id
    agent = await svc.client.get(agent_path)
    revision = await svc.client.get(agent_path + "/revisions/" + agent.json()["default_revision_id"])
    rejected = await svc.client.post(
        agent_path + "/revisions",
        headers={"If-Match": agent.headers["etag"]},
        json={
            "config": {
                **revision.json()["config"],
                "connections": [{"connection_id": new_object_id("conn"), "tools": ["increment"]}],
            },
        },
    )
    assert rejected.status_code == 404, rejected.text


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_queued_context_revalidated_after_default_revision_changes(public_service, mcp_url, model_url):
    svc = public_service
    app, client, path = svc.app, svc.client, svc.workspace_path
    connection = await configure_mcp(svc, mcp_url, model_url)
    body = {
        "kind": "message",
        "delivery": "next_run",
        "agent_id": svc.agent_id,
        "payload": {"content": [{"type": "text", "text": "No tool invocation"}]},
    }
    first = await client.post(path + "/threads", headers={"Idempotency-Key": "source"}, json=body)
    assert first.status_code == 201, first.text
    thread = first.json()["thread_id"]
    queued = await client.post(
        path + f"/threads/{thread}/inbox",
        headers={"Idempotency-Key": "queued"},
        json={
            **body,
            "options": {"mcp_headers": {connection: {"X-Ctx": "old-default"}}},
        },
    )
    assert queued.status_code == 201 and queued.json()["entry"]["status"] == "pending", queued.text
    agent_path = path + "/agents/" + svc.agent_id
    agent = await client.get(agent_path)
    revision = await client.get(agent_path + "/revisions/" + agent.json()["default_revision_id"])
    revised = await client.post(
        agent_path + "/revisions",
        headers={"If-Match": agent.headers["etag"]},
        json={
            "config": {**revision.json()["config"], "connections": []},
        },
    )
    assert revised.status_code == 201, revised.text
    claim = await claim_run(app.state.storage, worker_id="queued-context", worker_build="test", lease_seconds=60)
    assert claim is not None
    await execute_claim(app, claim)
    assert await advance_one(app.state.storage, max_attempts=3, keys=app.state.key_ring, policy=None)
    inbox = await client.get(path + f"/threads/{thread}/inbox")
    rejected = next(item for item in inbox.json()["items"] if item["id"] == queued.json()["entry"]["id"])
    assert rejected["status"] == "failed", rejected
    assert rejected["failure"]["code"] == "invalid_argument"
    assert rejected["assigned_run_id"] is None
