"""Public waiting and exact feedback over HTTP models, native Harness and real storage."""

import httpx2
import pytest
from a13n_harness.tools.identity import source_tool_id
from a13n_service.runs.attempts import claim_run
from a13n_service.runs.worker import Worker

from dev.fixtures.service_mcp import configure_mcp, execute_claim

pytestmark = pytest.mark.anyio


async def revise(service, **updates):
    path = service.workspace_path + "/agents/" + service.agent_id
    current = await service.client.get(path)
    revision = await service.client.get(path + "/revisions/" + current.json()["default_revision_id"])
    response = await service.client.post(
        path + "/revisions",
        headers={"If-Match": current.headers["etag"]},
        json={"config": {**revision.json()["config"], **updates}},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def execute_next(service, *, supervised=False):
    claim = await claim_run(service.app.state.storage, worker_id="waiting-proof", worker_build="test", lease_seconds=60)
    assert claim is not None
    if supervised:
        state = service.app.state
        owner = Worker(
            state.storage,
            state.objects,
            state.redis,
            config=state.settings,
            catalog=state.model_catalog,
            tool_catalog=state.tool_catalog,
            keys=state.key_ring,
            endpoint_policy=state.endpoint_policy,
            admission=state.admission,
        )
        await owner._attempt(claim)
    else:
        await execute_claim(service.app, claim)
    view = await service.client.get(service.workspace_path + f"/runs/{claim.run_id}/items")
    assert view.status_code == 200, view.text
    return claim, view.json()


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
@pytest.mark.parametrize(
    "kind,empty",
    [
        ("question", False),
        ("client", False),
        ("client", True),
        ("approval", False),
        ("approval", True),
        ("mixed", False),
        ("mixed", True),
    ],
)
async def test_native_waiting_public_feedback_and_question_revision(public_service, mcp_url, model_url, kind, empty):
    service = public_service
    connection_id = None
    if kind in {"approval", "mixed"}:
        connection_id = await configure_mcp(service, mcp_url, model_url)
    await revise(
        service,
        user_questions=kind in {"question", "mixed"},
        client_tools=[
            {
                "name": "local_review",
                "description": "Manual review result",
                "parameters_json_schema": {"type": "object", "properties": {"prompt": {"type": "string"}}},
            }
        ]
        if kind in {"client", "mixed"}
        else [],
        tool_permissions={"rules": {source_tool_id(connection_id, "increment", kind="mcp"): "ask"}}
        if connection_id
        else {},
    )
    message = {
        "kind": "message",
        "agent_id": service.agent_id,
        "payload": {"content": [{"type": "text", "text": f"[service-wait:{kind}]"}]},
    }
    first = await service.client.post(
        service.workspace_path + "/threads", headers={"Idempotency-Key": "first"}, json=message
    )
    assert first.status_code == 201, first.text
    thread_id = first.json()["thread_id"]
    inbox = service.workspace_path + f"/threads/{thread_id}/inbox"
    waiting_claim, waiting = await execute_next(service)
    assert waiting["status"] == "waiting" and waiting["output"] is None
    assert waiting["wait_reason"] == (
        "multiple"
        if kind == "mixed"
        else "user_input"
        if kind == "question"
        else "client_tool"
        if kind == "client"
        else "approval"
    )
    assert waiting["inputs"][0]["status"] == "consumed"
    if kind == "question":
        revision_id = await revise(service, user_questions=False)
        answer = {
            **message,
            "payload": {"content": [{"type": "text", "text": "Use the small scope"}]},
            "options": {"labels": {"reply": "new revision"}},
        }
    else:
        revision_id = first.json()["run"]["agent_revision_id"]
        # Control feedback bypasses a full ordinary inbox and older ineligible messages.
        for index in range(3):
            queued = await service.client.post(
                inbox,
                headers={"Idempotency-Key": f"queued-{index}"},
                json={**message, "payload": {"content": [{"type": "text", "text": f"Queued {index}"}]}},
            )
            assert queued.status_code == 201 and queued.json()["run"] is None, queued.text
        answers = (
            []
            if empty
            else [
                {"tool_call_id": item["tool_call_id"], "action": "approve"}
                if item["kind"] == "approval"
                else {
                    "tool_call_id": item["tool_call_id"],
                    "action": "complete",
                    "result": {"review": "accepted external fact"},
                }
                for item in waiting["pending"]
                if item["kind"] != "user_input"
            ]
        )
        answer = {"kind": "feedback", "waiting_run_id": waiting_claim.run_id, "answers": answers}
    resumed = await service.client.post(inbox, headers={"Idempotency-Key": "answer"}, json=answer)
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["run"]["agent_revision_id"] == revision_id
    source_id = resumed.json()["entry"]["id"]
    _, completed = await execute_next(service)
    assert completed["status"] == "completed", completed
    consumed = [entry for entry in completed["inputs"] if entry["id"] == source_id]
    assert len(consumed) == 1 and consumed[0]["status"] == "consumed"
    replay = await service.client.post(inbox, headers={"Idempotency-Key": "answer"}, json=answer)
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert replay.json()["entry"]["id"] == source_id
    if kind != "question":
        stale = await service.client.post(inbox, headers={"Idempotency-Key": "stale-answer"}, json=answer)
        assert stale.status_code == 201 and stale.json()["entry"]["failure"]["code"] == "stale_feedback", stale.text
    if connection_id:
        async with httpx2.AsyncClient(trust_env=False) as peer:
            state = (await peer.get(mcp_url + "/fixture/state")).json()
        assert state["effects"] == (0 if empty else 1)
    assert service.app.state.storage.engine.pool.checkedout() == 0


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
@pytest.mark.parametrize("cut", ["waiting_candidate", "feedback_confirmed", "after_effect"])
async def test_real_process_death_preserves_waiting_and_mixed_feedback(
    public_service, mcp_url, model_url, tmp_path, cut
):
    import asyncio
    import base64
    import json
    import os
    import signal
    import sys
    from pathlib import Path

    from a13n_service.runs import seal
    from a13n_service.runs.schemas import Checkpoint
    from a13n_service.runs.snapshots import object_key
    from pydantic_ai.messages import ToolReturnPart

    service = public_service
    connection_id = await configure_mcp(service, mcp_url, model_url)
    await revise(
        service,
        user_questions=True,
        client_tools=[
            {
                "name": "local_review",
                "description": "Manual review",
                "parameters_json_schema": {"type": "object", "properties": {}},
            }
        ],
        tool_permissions={"rules": {source_tool_id(connection_id, "increment", kind="mcp"): "ask"}},
    )
    submitted = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "process-wait"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "[service-wait:mixed] [hold-mcp]"}]},
        },
    )
    assert submitted.status_code == 201, submitted.text
    value = submitted.json()
    run_id = value["run"]["id"]
    source_id = value["entry"]["id"]
    if cut != "waiting_candidate":
        _, waiting = await execute_next(service)
        assert waiting["status"] == "waiting"
        feedback = await service.client.post(
            service.workspace_path + f"/threads/{value['thread_id']}/inbox",
            headers={"Idempotency-Key": "process-feedback"},
            json={
                "kind": "feedback",
                "waiting_run_id": run_id,
                "answers": [
                    {"tool_call_id": "call_approval", "action": "approve"},
                    {
                        "tool_call_id": "call_client",
                        "action": "complete",
                        "result": {"proof": "external fact survives process death"},
                    },
                ],
            },
        )
        assert feedback.status_code == 201, feedback.text
        run_id, source_id = feedback.json()["run"]["id"], feedback.json()["entry"]["id"]
    settings = service.app.state.settings
    configuration = settings.model_dump(mode="json")
    configuration["database"].update(url=settings.database.url.get_secret_value(), auto_migrate=False)
    configuration["redis"]["url"] = settings.redis.url.get_secret_value()
    configuration["worker"].update(lease_seconds=6, scan_seconds=0.1, slots=1)
    configuration["providers"]["http_origins"] = [mcp_url, model_url.removesuffix("/v1")]
    configuration["encryption"] = {
        "active_key_id": "test",
        "keys": {"test": base64.b64encode(bytes(range(32))).decode()},
    }
    config = tmp_path / "worker.json"
    config.write_text(json.dumps(configuration))
    config.chmod(0o600)
    marker = tmp_path / "reached-cut"
    processes = []
    log = (tmp_path / "worker.log").open("wb")

    async def launch(point):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("fault_worker.py")),
            str(config),
            point,
            str(marker),
            stdout=log,
            stderr=log,
            env={key: value for key, value in os.environ.items() if not key.startswith("A13N_")},
        )
        processes.append(process)
        return process

    state_key = object_key(service.initialized.organization_id, run_id, "state")
    try:
        first = await launch(cut)
        async with httpx2.AsyncClient(trust_env=False) as peer:
            async with asyncio.timeout(25):
                while True:
                    counted = (await peer.get(mcp_url + "/fixture/state")).json()
                    if (cut == "after_effect" and counted["effects"] == 1) or marker.exists():
                        break
                    assert first.returncode is None, (tmp_path / "worker.log").read_text()
                    await asyncio.sleep(0.03)
            stored = await service.app.state.objects.read(state_key)
            assert stored is not None
            checkpoint = Checkpoint.model_validate_json(stored.content)
            assert checkpoint.receipts[0] == source_id
            assert counted["effects"] == (1 if cut == "after_effect" else 0)
            if cut == "waiting_candidate":
                assert checkpoint.candidate == "waiting" and checkpoint.waiting is not None
            else:
                assert checkpoint.candidate is None
                assert "external fact survives process death" in checkpoint.state.model_dump_json()
                assert not any(
                    isinstance(part, ToolReturnPart) and part.tool_call_id == "call_client"
                    for message in checkpoint.state.message_history
                    for part in message.parts
                )
                before = await service.client.get(service.workspace_path + f"/runs/{run_id}/items")
                assert next(item for item in before.json()["inputs"] if item["id"] == source_id)["status"] == "consumed"
            first.kill()
            assert await asyncio.wait_for(first.wait(), 5) == -signal.SIGKILL
            async with asyncio.timeout(9):
                while not await seal.expire(service.app.state.storage, run_id, backoff_seconds=0):
                    await asyncio.sleep(0.03)
            second = await launch("none")
            async with asyncio.timeout(25):
                while True:
                    result = await service.client.get(service.workspace_path + f"/runs/{run_id}/items")
                    assert second.returncode is None, (tmp_path / "worker.log").read_text()
                    if result.json()["complete"]:
                        break
                    await asyncio.sleep(0.03)
            assert result.json()["status"] == ("waiting" if cut == "waiting_candidate" else "completed"), result.text
            counted = (await peer.get(mcp_url + "/fixture/state")).json()
            assert counted["effects"] == (1 if cut == "after_effect" else 0)
            model = (await peer.get(model_url.removesuffix("/v1") + "/fixture/model-state")).json()
            assert model["request_count"] == (1 if cut == "waiting_candidate" else 2)
            if cut != "waiting_candidate":
                final = Checkpoint.model_validate_json((await service.app.state.objects.read(state_key)).content)
                returns = [
                    part
                    for message in final.state.message_history
                    for part in message.parts
                    if isinstance(part, ToolReturnPart)
                ]
                assert [part.content for part in returns if part.tool_call_id == "call_client"] == [
                    {"proof": "external fact survives process death"}
                ]
                assert [part.outcome for part in returns if part.tool_call_id == "call_approval"] == ["failed"]
                assert final.receipts.count(source_id) == 1
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), 5)
        log.close()


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_feedback_rejects_malformed_batches_and_serializes_distinct_keys(public_service, model_url):
    import asyncio

    service = public_service
    await revise(
        service,
        client_tools=[
            {
                "name": "local_review",
                "description": "Manual review",
                "parameters_json_schema": {"type": "object", "properties": {}},
            }
        ],
    )
    initial = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "waiting"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "[service-wait:client]"}]},
        },
    )
    assert initial.status_code == 201, initial.text
    target, _ = await execute_next(service)
    inbox = service.workspace_path + f"/threads/{initial.json()['thread_id']}/inbox"
    base = {"kind": "feedback", "waiting_run_id": target.run_id}
    malformed = [
        {**base, "agent_id": service.agent_id},
        {**base, "options": {}},
        {**base, "answers": [{"tool_call_id": "unknown", "action": "complete", "result": 1}]},
        {**base, "answers": [{"tool_call_id": "call_client", "action": "approve"}]},
        {**base, "answers": [{"tool_call_id": "call_client", "action": "complete", "result": 1}] * 2},
        {**base, "answers": [{"tool_call_id": "call_client", "action": "complete", "result": "x" * 262144}]},
    ]
    before = (await service.client.get(inbox)).json()
    for index, body in enumerate(malformed):
        response = await service.client.post(inbox, headers={"Idempotency-Key": f"invalid-{index}"}, json=body)
        assert response.status_code in {400, 422}, response.text
    assert (await service.client.get(inbox)).json() == before
    results = await asyncio.gather(
        *[
            service.client.post(inbox, headers={"Idempotency-Key": key}, json=base)
            for key in ("concurrent-first", "concurrent-second")
        ]
    )
    assert all(response.status_code == 201 for response in results), [r.text for r in results]
    bodies = [response.json() for response in results]
    assert sum(body["run"] is not None for body in bodies) == 1
    stale = next(body for body in bodies if body["run"] is None)
    assert stale["entry"]["status"] == "failed" and stale["entry"]["failure"]["code"] == "stale_feedback"
    _, finished = await execute_next(service)
    assert finished["status"] == "completed", finished
    for key, original in zip(("concurrent-first", "concurrent-second"), bodies, strict=True):
        replay = await service.client.post(inbox, headers={"Idempotency-Key": key}, json=base)
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert replay.json()["entry"]["id"] == original["entry"]["id"]


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_changed_connection_cannot_receive_old_approval(public_service, mcp_url, model_url):
    service = public_service
    connection_id = await configure_mcp(service, mcp_url, model_url)
    await revise(service, tool_permissions={"rules": {source_tool_id(connection_id, "increment", kind="mcp"): "ask"}})
    initial = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "waiting"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "[service-wait:approval]"}]},
        },
    )
    assert initial.status_code == 201, initial.text
    target, _ = await execute_next(service)
    connection_path = service.workspace_path + "/connections/" + connection_id
    current = await service.client.get(connection_path)
    updated = await service.client.patch(
        connection_path, headers={"If-Match": current.headers["etag"]}, json={"name": "Changed after waiting"}
    )
    assert updated.status_code == 200, updated.text
    inbox = service.workspace_path + f"/threads/{initial.json()['thread_id']}/inbox"
    answer = {
        "kind": "feedback",
        "waiting_run_id": target.run_id,
        "answers": [{"tool_call_id": "call_approval", "action": "approve"}],
    }
    submitted = await service.client.post(inbox, headers={"Idempotency-Key": "old-approval"}, json=answer)
    assert submitted.status_code == 201, submitted.text
    _, failed = await execute_next(service, supervised=True)
    assert failed["status"] == "failed", failed
    thread = (await service.client.get(service.workspace_path + f"/threads/{initial.json()['thread_id']}")).json()
    assert thread["head_run_id"] == target.run_id and thread["current_run_id"] is None
    # An explicit denial can close the original batch without granting the changed target.
    denied = await service.client.post(
        inbox,
        headers={"Idempotency-Key": "deny-after-failure"},
        json={"kind": "feedback", "waiting_run_id": target.run_id},
    )
    assert denied.status_code == 201 and denied.json()["run"] is not None, denied.text
    _, completed = await execute_next(service)
    assert completed["status"] == "completed", completed
    async with httpx2.AsyncClient(trust_env=False) as peer:
        assert (await peer.get(mcp_url + "/fixture/state")).json()["effects"] == 0
