"""Real HTTP MCP discovery and tool dispatch through public resources and Harness."""

import base64
import json

import httpx2
import pytest
from a13n_service.runs.attempts import claim_run
from a13n_service.settings import Worker as WorkerSettings

from dev.fixtures.service_mcp import configure_mcp, execute_claim

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
@pytest.mark.parametrize("auth", ["none", "bearer", "headers"])
async def test_public_connection_discovers_and_calls_real_mcp(public_service, mcp_url, model_url, auth):
    svc = public_service
    app, client, path = svc.app, svc.client, svc.workspace_path
    connection_id = await configure_mcp(svc, mcp_url, model_url, auth=auth)
    submitted = await client.post(
        path + "/threads",
        headers={"Idempotency-Key": "mcp-execute"},
        json={
            "kind": "message",
            "agent_id": svc.agent_id,
            "payload": {"content": [{"type": "text", "text": "[service-mcp:increment]"}]},
            "options": {"mcp_headers": {connection_id: {"X-Conversation": auth}}},
        },
    )
    assert submitted.status_code == 201, submitted.text
    claim = await claim_run(app.state.storage, worker_id="mcp-proof", worker_build="test", lease_seconds=60)
    assert claim is not None
    await execute_claim(app, claim)
    view = await client.get(path + f"/runs/{claim.run_id}/items")
    assert view.json()["status"] == "completed", view.text
    assert "service proof" in view.json()["output"]["text"]
    assert "fixture-secret" not in view.text
    with httpx2.Client(trust_env=False) as peer:
        state = peer.get(mcp_url + "/fixture/state").json()
    assert state["effects"] == 1 and len(state["calls"]) == 1
    assert json.loads(state["calls"][0]["context"])["conversation"] == auth
    assert state["calls"][0]["operation"]
    assert app.state.storage.engine.pool.checkedout() == 0


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
@pytest.mark.parametrize("public_service", [{"scan_seconds": 0.2}], indirect=True)
@pytest.mark.parametrize(
    "tool,safe,mutation",
    [
        ("increment", False, "none"),
        ("increment_once", True, "none"),
        ("increment_once", True, "revoke"),
        ("increment_once", True, "credential"),
        ("increment_once", True, "endpoint"),
        ("increment_once", True, "disable"),
    ],
)
async def test_production_worker_death_after_external_effect(
    public_service, mcp_url, model_url, tmp_path, tool, safe, mutation
):
    import asyncio
    import os
    import socket
    import sys

    from a13n_service.infra.db import short_session
    from a13n_service.runs.schemas import Checkpoint
    from a13n_service.runs.snapshots import object_key
    from a13n_service.runs.tables import AttemptRow, RunRow
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from sqlalchemy import select

    svc = public_service
    app, client, path = svc.app, svc.client, svc.workspace_path
    revoke = mutation == "revoke"
    disabled = mutation == "disable"
    connection_id = await configure_mcp(
        svc, mcp_url, model_url, tool=tool, safe=safe, auth="bearer" if mutation == "credential" else "none"
    )
    settings = app.state.settings
    lease_seconds = WorkerSettings().lease_seconds
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    configuration = tmp_path / "worker.toml"
    configuration.write_text(f"""[server]
port = {port}
shutdown_timeout = 2
[database]
url = {json.dumps(settings.database.url.get_secret_value())}
auto_migrate = false
[redis]
url = {json.dumps(settings.redis.url.get_secret_value())}
[objects]
root = {json.dumps(str(settings.objects.root))}
[providers]
private_cidrs = ["127.0.0.0/8"]
http_origins = [{json.dumps(mcp_url)}, {json.dumps(model_url.removesuffix("/v1"))}]
[encryption]
active_key_id = "test"
[encryption.keys]
test = {json.dumps(base64.b64encode(bytes(range(32))).decode())}
[worker]
slots = 1
lease_seconds = {lease_seconds}
scan_seconds = 0.1
authority_seconds = 0.2
""")
    configuration.chmod(0o600)
    processes = []
    handles = []

    async def start_worker():
        log = (tmp_path / f"worker-{len(processes)}.log").open("wb")
        handles.append(log)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from a13n_service.cli import main; main()",
            "--config",
            str(configuration),
            "run",
            "--role",
            "worker",
            env={name: value for name, value in os.environ.items() if not name.startswith("A13N_")},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        processes.append(process)
        return process

    submitted = await client.post(
        path + "/threads",
        headers={"Idempotency-Key": "worker-effect"},
        json={
            "kind": "message",
            "agent_id": svc.agent_id,
            "payload": {"content": [{"type": "text", "text": f"[service-mcp:{tool}] [hold-mcp]"}]},
            "options": {"mcp_headers": {connection_id: {"X-Conversation": "crash-proof", "X-Effect-Barrier": "true"}}},
        },
    )
    assert submitted.status_code == 201, submitted.text
    run_id = submitted.json()["run"]["id"]
    state_key = object_key(svc.initialized.organization_id, run_id, "state")
    try:
        first_worker = await start_worker()
        async with httpx2.AsyncClient(trust_env=False) as peer:
            try:
                async with asyncio.timeout(25):
                    while True:
                        counted = (await peer.get(mcp_url + "/fixture/state")).json()
                        if counted["barriers"]:
                            break
                        assert first_worker.returncode is None, (tmp_path / "worker-0.log").read_text()
                        view = await client.get(path + f"/runs/{run_id}/items")
                        if view.json()["status"] in {"completed", "failed", "cancelled"}:
                            origins = []
                            for line in (tmp_path / "worker-0.log").read_text().splitlines():
                                if line.startswith("{"):
                                    item = json.loads(line)
                                    if item.get("run_id") == run_id and item.get("error_type"):
                                        origins.append(
                                            {
                                                key: item[key]
                                                for key in ("error_type", "exception_details", "harness_failure")
                                                if key in item
                                            }
                                        )
                            pytest.fail(f"Run became {view.json()['status']} before effect barrier: {origins}")
                        await asyncio.sleep(0.025)
            except (TimeoutError, AssertionError, pytest.fail.Exception):
                diagnostic = {"worker_returncode": first_worker.returncode}
                for name, url in (
                    ("model", model_url.removesuffix("/v1") + "/fixture/model-state"),
                    ("peer", mcp_url + "/fixture/state"),
                ):
                    try:
                        async with asyncio.timeout(2):
                            value = (await peer.get(url)).json()
                        diagnostic[name] = (
                            value
                            if name == "model"
                            else {
                                "calls": len(value["calls"]),
                                "effects": value["effects"],
                                "barriers": len(value["barriers"]),
                            }
                        )
                    except Exception as error:
                        diagnostic[name] = {"observation_error": type(error).__name__}
                (tmp_path / "pre-effect-diagnostic.json").write_text(json.dumps(diagnostic, indent=2))
                raise
            assert counted["effects"] == 0 and counted["calls"] == []
            original = json.loads(counted["barriers"][0]["context"])
            before = await app.state.objects.read(state_key)
            assert before is not None
            checkpoint = Checkpoint.model_validate_json(before.content)
            pending = [
                part
                for message in checkpoint.state.message_history
                for part in message.parts
                if isinstance(part, ToolCallPart) and part.tool_call_id == original["tool_call_id"]
            ]
            assert len(pending) == 1
            assert not any(
                isinstance(part, ToolReturnPart) and part.tool_call_id == original["tool_call_id"]
                for message in checkpoint.state.message_history
                for part in message.parts
            )
            await peer.post(mcp_url + "/fixture/permit-effect")
            async with asyncio.timeout(10):
                while True:
                    counted = (await peer.get(mcp_url + "/fixture/state")).json()
                    if counted["effects"] == 1:
                        break
                    await asyncio.sleep(0.025)
            assert len(counted["calls"]) == 1 and counted["calls"][0]["returned"] == 0
            first_worker.kill()
            await first_worker.wait()
            assert first_worker.returncode == -9
            if mutation != "none":
                resource = path + "/connections/" + connection_id
                current = await client.get(resource)
                configuration_update = current.json()["config"]
                if revoke:
                    configuration_update["recovery_retry_safe_tools"] = []
                if mutation == "endpoint":
                    configuration_update["url"] += "?generation=2"
                    retired = await peer.post(
                        mcp_url + "/fixture/retire-destination", json={"destination": "/none/mcp"}
                    )
                    assert retired.status_code == 200
                    assert (await peer.post(mcp_url + "/none/mcp", json={})).status_code == 410
                change = {"config": configuration_update}
                if mutation == "credential":
                    change["credential"] = {"token": "rotated-secret"}
                if disabled:
                    change["enabled"] = False
                updated = await client.patch(resource, headers={"If-Match": current.headers["etag"]}, json=change)
                assert updated.status_code == 200, updated.text
            replacement = await start_worker()
            async with asyncio.timeout(lease_seconds + 30):
                while True:
                    view = await client.get(path + f"/runs/{run_id}/items")
                    if view.json()["status"] in {"completed", "failed", "cancelled"}:
                        break
                    assert replacement.returncode is None, (tmp_path / "worker-1.log").read_text()
                    await asyncio.sleep(0.05)
            assert view.json()["status"] == ("failed" if disabled else "completed"), (
                view.text,
                (tmp_path / "worker-1.log").read_text(),
            )
            counted = (await peer.get(mcp_url + "/fixture/state")).json()
            assert counted["effects"] == 1
            assert len(counted["calls"]) == (2 if safe and not revoke and not disabled else 1)
            assert len({call["operation"] for call in counted["calls"]}) == 1
            if mutation == "endpoint":
                assert [call["destination"] for call in counted["calls"]] == ["/none/mcp", "/none/mcp?generation=2"]
            if mutation == "credential":
                import hashlib

                assert counted["calls"][0]["auth_digest"] == hashlib.sha256(b"Bearer fixture-secret").hexdigest()
                assert counted["calls"][1]["auth_digest"] == hashlib.sha256(b"Bearer rotated-secret").hexdigest()
                assert all(json.loads(call["context"])["conversation"] == "crash-proof" for call in counted["calls"])
            after = await app.state.objects.read(state_key)
            assert after is not None
            if not disabled:
                assert after.version != before.version and after.writer > before.writer
            restored = Checkpoint.model_validate_json(after.content)
            results = [
                part
                for message in restored.state.message_history
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_call_id == original["tool_call_id"]
            ]
            assert len(results) == (0 if disabled else 1)
            if not safe or revoke:
                assert results[0].outcome == "failed"
                assert "may have partially or fully completed" in str(results[0].content), results[0]
            async with short_session(app.state.storage) as session:
                attempts = list(
                    await session.scalars(
                        select(AttemptRow).where(AttemptRow.run_id == run_id).order_by(AttemptRow.number)
                    )
                )
                run = await session.get(RunRow, run_id)
                assert len(attempts) == 2 and attempts[0].failure["code"] == "lease_expired"
                assert attempts[1].replaces_attempt_id == attempts[0].id
                assert run.status == ("failed" if disabled else "completed") and run.current_attempt_id is None
            await peer.post(mcp_url + "/fixture/release")
            await asyncio.sleep(0.1)
            late = await app.state.objects.read(state_key)
            assert late is not None and late.version == after.version
            (tmp_path / "g005-proof.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "connection_id": connection_id,
                        "tool": tool,
                        "safe": safe,
                        "revoked": revoke,
                        "mutation": mutation,
                        "signal": first_worker.returncode,
                        "lease_seconds": lease_seconds,
                        "first_attempt_failure": attempts[0].failure["code"],
                        "natural_lease_expiry": True,
                        "pending_checkpoint_verified_before_permitting_effect": True,
                        "original": original,
                        "effects": counted["effects"],
                        "calls": counted["calls"],
                        "checkpoint_before": {
                            "version": before.version,
                            "digest": before.digest,
                            "writer": before.writer,
                        },
                        "checkpoint_after": {"version": after.version, "digest": after.digest, "writer": after.writer},
                        "attempt_ids": [attempt.id for attempt in attempts],
                        "result": str(results[0].content) if results else None,
                        "late_result_did_not_change_checkpoint": True,
                    },
                    indent=2,
                )
            )
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
        for process in processes:
            try:
                async with asyncio.timeout(8):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
        for handle in handles:
            handle.close()


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_concurrent_runs_keep_context_and_credentials_scoped(public_service, mcp_url, model_url):
    import asyncio
    import hashlib

    svc = public_service
    app, client, path = svc.app, svc.client, svc.workspace_path
    first = await configure_mcp(svc, mcp_url, model_url, auth="bearer", tool="increment_once", safe=True)
    calls = []

    async def submit(connection, label):
        result = await client.post(
            path + "/threads",
            headers={"Idempotency-Key": label},
            json={
                "kind": "message",
                "agent_id": svc.agent_id,
                "payload": {"content": [{"type": "text", "text": "[service-mcp:increment_once] [fixed-call-id]"}]},
                "options": {"mcp_headers": {connection: {"X-Conversation": label}}},
            },
        )
        assert result.status_code == 201, result.text
        calls.append(result.json()["run"]["id"])

    await submit(first, "first")
    await submit(first, "same-connection-new-run")
    second = await configure_mcp(svc, mcp_url, model_url, auth="bearer", tool="increment_once", safe=True)
    resource = path + "/connections/" + second
    current = await client.get(resource)
    updated = await client.patch(
        resource,
        headers={"If-Match": current.headers["etag"]},
        json={
            "credential": {"token": "rotated-secret"},
            "config": current.json()["config"],
        },
    )
    assert updated.status_code == 200, updated.text
    await submit(second, "separate-credential")
    claims = [
        await claim_run(app.state.storage, worker_id=f"concurrent-{index}", worker_build="test", lease_seconds=60)
        for index in range(3)
    ]
    assert all(claim is not None for claim in claims)
    await asyncio.gather(*(execute_claim(app, claim) for claim in claims))
    with httpx2.Client(trust_env=False) as peer:
        counted = peer.get(mcp_url + "/fixture/state").json()
    assert counted["effects"] == 3 and len(counted["calls"]) == 3
    contexts = {json.loads(call["context"])["conversation"]: call for call in counted["calls"]}
    assert set(contexts) == {"first", "same-connection-new-run", "separate-credential"}
    assert len({call["operation"] for call in contexts.values()}) == 3
    assert {json.loads(call["context"])["tool_call_id"] for call in contexts.values()} == {"call_same_original_id"}
    for label, call in contexts.items():
        token = b"Bearer rotated-secret" if label == "separate-credential" else b"Bearer fixture-secret"
        assert call["auth_digest"] == hashlib.sha256(token).hexdigest()
        view = await client.get(path + "/runs/" + json.loads(call["context"])["run_id"] + "/items")
        assert view.json()["status"] == "completed" and label in view.json()["output"]["text"]
        assert "fixture-secret" not in view.text and "rotated-secret" not in view.text


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_oversized_tool_result_never_reaches_durable_output(public_service, mcp_url, model_url):
    from a13n_service.infra.errors import ServiceError

    svc = public_service
    app, client, path = svc.app, svc.client, svc.workspace_path
    await configure_mcp(svc, mcp_url, model_url, tool="oversized")
    submitted = await client.post(
        path + "/threads",
        headers={"Idempotency-Key": "oversized"},
        json={
            "kind": "message",
            "agent_id": svc.agent_id,
            "payload": {"content": [{"type": "text", "text": "[service-mcp:oversized]"}]},
        },
    )
    assert submitted.status_code == 201, submitted.text
    claim = await claim_run(app.state.storage, worker_id="bounded-result", worker_build="test", lease_seconds=60)
    assert claim is not None
    try:
        await execute_claim(app, claim)
    except ServiceError as error:
        assert error.code == "payload_too_large"
    else:
        view = await client.get(path + f"/runs/{claim.run_id}/items")
        assert "no usable result" in view.json()["output"]["text"], view.text
        assert "x" * 100 not in view.text
    with httpx2.Client(trust_env=False) as peer:
        counted = peer.get(mcp_url + "/fixture/state").json()
    assert len(counted["calls"]) == 1 and counted["effects"] == 0
    assert len(counted["calls"][0]["result"]) > 262144
    assert app.state.storage.engine.pool.checkedout() == 0
