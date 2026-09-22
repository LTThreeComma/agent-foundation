"""Public input ownership, pagination and terminal disposition stay distinct."""

import pytest
from a13n_service.infra.db import transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.runs import seal
from a13n_service.runs.attempts import claim_run
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow

pytestmark = pytest.mark.anyio


async def test_run_inputs_exclude_pending_other_runs_and_other_tenants(public_service):
    service = public_service
    client, path, storage = service.client, service.workspace_path, service.app.state.storage

    async def submit(target, key, text):
        response = await client.post(
            target,
            headers={"Idempotency-Key": key},
            json={
                "kind": "message",
                "agent_id": service.agent_id,
                "payload": {"content": [{"type": "text", "text": text}]},
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    first = await submit(path + "/threads", "first", "First input")
    inbox_path = f"{path}/threads/{first['thread_id']}/inbox"
    pending = await submit(inbox_path, "pending", "Unassigned private input")
    other = await submit(path + "/threads", "other", "Other run input")
    run_path = f"{path}/runs/{first['run']['id']}/items"
    view = await client.get(run_path)
    assert view.status_code == 200, view.text
    assert [entry["id"] for entry in view.json()["inputs"]] == [first["entry"]["id"]]
    assert view.json()["inputs"][0]["status"] == "assigned"
    assert "Unassigned private input" not in view.text and "Other run input" not in view.text
    inbox = await client.get(inbox_path, params={"limit": 1})
    assert inbox.status_code == 200
    assert inbox.json()["items"] == view.json()["inputs"]
    next_cursor = inbox.json()["next_cursor"]
    remaining = await client.get(inbox_path, params={"cursor": next_cursor})
    assert remaining.json()["items"][0]["id"] == pending["entry"]["id"]
    assert remaining.json()["items"][0]["status"] == "pending"
    assert remaining.json()["items"][0]["incorporated_checkpoint_seq"] is None
    wrong_cursor = await client.get(run_path, params={"input_cursor": next_cursor})
    assert wrong_cursor.status_code == 400
    assert (
        await client.get(f"{path}/threads/{other['thread_id']}/inbox", params={"cursor": next_cursor})
    ).status_code == 400

    organization_id, workspace_id = new_object_id("org"), new_object_id("ws")
    async with transaction(storage) as session:
        session.add(OrganizationRow(id=organization_id, key="isolated", name="Isolated"))
        await session.flush()
        session.add(WorkspaceRow(id=workspace_id, organization_id=organization_id, key="isolated", name="Isolated"))
    for suffix in (f"runs/{first['run']['id']}/items", f"threads/{first['thread_id']}/inbox"):
        denied = await client.get(f"/api/v1/workspaces/{workspace_id}/{suffix}")
        assert denied.status_code == 403
        assert "First input" not in denied.text

    claim = await claim_run(storage, worker_id="fail-test", worker_build="test", lease_seconds=30)
    assert claim is not None and claim.run_id == first["run"]["id"]
    await seal.failed(storage, claim, code="injected", message="Execution never incorporated input")
    failed = await client.get(run_path)
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed" and failed.json()["complete"]
    assert failed.json()["inputs"][0]["status"] == "failed"
    assert failed.json()["inputs"][0]["incorporated_checkpoint_seq"] is None
    assert failed.json()["inputs"][0]["payload"]["content"][0]["text"] == "First input"
    assert len(failed.json()["inputs"]) == 1
    assert storage.engine.pool.checkedout() == 0
