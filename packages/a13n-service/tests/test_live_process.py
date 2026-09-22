"""Fixture-owned CLI bootstrap and HTTPS control/two-worker execution journey."""

import asyncio
import ipaddress
import json
import os
import socket
import ssl
import sys
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx2
import pytest
from a13n_service.runs.streams import AttemptStream, Bounds
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from redis.asyncio import Redis

pytestmark = pytest.mark.anyio


def certificate(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "certificate.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    key_path.chmod(0o600)
    return cert_path, key_path


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


async def cli(config, *args, stdin=None):
    environment = {name: value for name, value in os.environ.items() if not name.startswith("A13N_")}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "from a13n_service.cli import main; main()",
        "--config",
        str(config),
        *args,
        env=environment,
        stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(30):
            output, errors = await process.communicate(stdin)
        assert process.returncode == 0, errors.decode()
        return output.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.fixture
async def live_service(empty_database, redis_url, model_url, tmp_path, agent_configurator):
    cert, key = certificate(tmp_path)
    processes, handles = [], []
    config_paths = []
    for index in range(3):
        config = tmp_path / f"role-{index}.toml"
        port = available_port()
        config.write_text(f"""[server]
port = {port}
tls_certificate = {json.dumps(str(cert))}
tls_key = {json.dumps(str(key))}
shutdown_timeout = 5
[database]
url = {json.dumps(empty_database.url.get_secret_value())}
auto_migrate = false
[redis]
url = {json.dumps(redis_url)}
[objects]
root = {json.dumps(str(tmp_path / "objects"))}
[providers]
private_cidrs = ["127.0.0.0/8"]
http_origins = [{json.dumps(model_url.removesuffix("/v1"))}]
[worker]
slots = 1
lease_seconds = 3
scan_seconds = 0.2
authority_seconds = 0.2
[control]
scan_seconds = 0.2
""")
        config.chmod(0o600)
        config_paths.append((config, f"https://127.0.0.1:{port}"))
    await cli(config_paths[0][0], "migrate")
    bootstrap = await cli(
        config_paths[0][0],
        "bootstrap",
        "--email",
        "live@example.com",
        stdin=b"live-test-password\nlive-test-password\n",
    )
    initialized = SimpleNamespace(**json.loads(bootstrap[bootstrap.index("{") :]))
    environment = {name: value for name, value in os.environ.items() if not name.startswith("A13N_")}
    try:
        async with AsyncExitStack() as stack:
            clients = []
            for index, (config, url) in enumerate(config_paths):
                log = (tmp_path / f"role-{index}.log").open("wb")
                handles.append(log)
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    "from a13n_service.cli import main; main()",
                    "--config",
                    str(config),
                    "run",
                    "--role",
                    "control" if index == 0 else "worker",
                    env=environment,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                )
                processes.append(process)
                client = await stack.enter_async_context(
                    httpx2.AsyncClient(
                        base_url=url, trust_env=False, verify=ssl.create_default_context(cafile=str(cert)), timeout=5
                    )
                )
                clients.append(client)
                async with asyncio.timeout(30):
                    while True:
                        assert process.returncode is None, (tmp_path / f"role-{index}.log").read_text()
                        try:
                            ready = await client.get("/readyz")
                            if ready.status_code == 200:
                                break
                        except httpx2.HTTPError:
                            pass
                        await asyncio.sleep(0.05)
            client = clients[0]
            logged = await client.post(
                "/api/v1/auth/login", json={"email": "live@example.com", "password": "live-test-password"}
            )
            assert logged.status_code == 200, logged.text
            assert "Secure" in logged.headers["set-cookie"]
            client.headers["x-csrf-token"] = logged.json()["csrf_token"]
            agent_id = await agent_configurator(client, initialized)
            yield SimpleNamespace(
                client=client,
                workspace_path=f"/api/v1/workspaces/{initialized.workspace_id}",
                agent_id=agent_id,
                workers=clients[1:],
                directory=tmp_path,
                initialized=initialized,
                model_url=model_url,
                redis=await stack.enter_async_context(Redis.from_url(redis_url, decode_responses=True)),
            )
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
        for process in processes:
            try:
                async with asyncio.timeout(10):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
        for handle in handles:
            handle.close()


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_public_process_journey_with_two_workers_and_slow_model(live_service):
    service = live_service
    client, path = service.client, service.workspace_path
    previous_run, thread_id = None, None
    for index in range(2):
        target = path + "/threads" if thread_id is None else f"{path}/threads/{thread_id}/inbox"
        body = {
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": f"[slow] [interruptible] Input {index}"}]},
        }
        submitted = await client.post(target, headers={"Idempotency-Key": f"live-{index}"}, json=body)
        assert submitted.status_code == 201, submitted.text
        value = submitted.json()
        thread_id, run_id = value["thread_id"], value["run"]["id"]
        assert value["run"]["parent_run_id"] == previous_run
        replay = await client.post(target, headers={"Idempotency-Key": f"live-{index}"}, json=body)
        assert replay.status_code == 200 and replay.json()["run"]["id"] == run_id
        seen_running = False
        live_events, controls = [], []
        dropped = False
        async with asyncio.timeout(40):
            while True:
                view = await client.get(f"{path}/runs/{run_id}/items")
                assert view.status_code == 200, view.text
                result = view.json()
                seen_running |= result["status"] == "running"
                if result["complete"]:
                    break
                params = {"cursor": result["cursor"]} if result["cursor"] else {}
                async with client.stream("GET", f"{path}/runs/{run_id}/events", params=params) as response:
                    assert response.status_code == 200
                    event_kind = None
                    async for line in response.aiter_lines():
                        if line.startswith("event: "):
                            event_kind = line.removeprefix("event: ")
                        if not line.startswith("data: "):
                            continue
                        data = json.loads(line.removeprefix("data: "))
                        if event_kind == "data":
                            live_events.append(data)
                            if index == 0 and not dropped:
                                replay = AttemptStream(service.redis, run_id, data["attempt_number"], Bounds())
                                await service.redis.delete(*replay.keys)
                                dropped = True  # Real Redis loss must trigger a durable snapshot handshake.
                        else:
                            controls.append(event_kind)
                            assert data["display_version"]
                await asyncio.sleep(0.02)
        assert live_events
        assert "Input " not in str(live_events) and "trusted dynamic context" not in str(live_events)
        if index == 0:
            assert dropped and any(kind in {"reset", "retry_later"} for kind in controls)
        terminal_stream = await client.get(f"{path}/runs/{run_id}/events")
        assert "event: closed" in terminal_stream.text
        assert result["status"] == "completed", result
        assert seen_running
        assert result["inputs"][0]["status"] == "consumed"
        assert len(result["segments"]) == 1  # Heartbeat outlives the three-second lease; no competing takeover.
        assert result["segments"][0]["attempt_number"] == 1
        assert result["output"]["text"].startswith("This is a local scripted response")
        previous_run = run_id
    for worker in service.workers:
        assert (await worker.get("/api/v1/users/me")).status_code == 404


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
@pytest.mark.parametrize("action", ["interrupt", "revoke"])
async def test_running_authority_changes_cancel_a_real_outstanding_request(live_service, empty_database, action):
    from a13n_service.infra.db import Storage, short_session, transaction
    from a13n_service.runs.tables import RunRow
    from a13n_service.tenancy.tables import GrantRow
    from sqlalchemy import delete

    service = live_service
    client, path = service.client, service.workspace_path
    submitted = await client.post(
        path + "/threads",
        headers={"Idempotency-Key": "authority"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "[interruptible] slow request"}]},
        },
    )
    assert submitted.status_code == 201, submitted.text
    run_id = submitted.json()["run"]["id"]
    async with httpx2.AsyncClient(trust_env=False) as model:
        async with asyncio.timeout(10):
            while True:
                state = await model.get(service.model_url.removesuffix("/v1") + "/fixture/model-state")
                if state.json()["request_count"]:
                    break
                await asyncio.sleep(0.02)
    storage = Storage(empty_database.url.get_secret_value())
    try:
        if action == "interrupt":
            for _ in range(2):
                result = await client.post(f"{path}/runs/{run_id}/interrupt")
                assert result.status_code == 200, result.text
        else:
            async with transaction(storage) as session:
                await session.execute(delete(GrantRow).where(GrantRow.principal_id == service.initialized.principal_id))
        async with asyncio.timeout(2):  # The fixture's outstanding HTTP request is still waiting for three seconds.
            while True:
                async with short_session(storage) as session:
                    run = await session.get(RunRow, run_id)
                    status, failure = run.status, run.failure
                if status in {"cancelled", "failed"}:
                    break
                await asyncio.sleep(0.02)
        assert status == ("cancelled" if action == "interrupt" else "failed")
        assert failure["code"] == ("cancelled" if action == "interrupt" else "forbidden")
        observed = await client.get(f"{path}/runs/{run_id}/items")
        if action == "revoke":
            assert observed.status_code == 403
        else:
            assert observed.status_code == 200 and observed.json()["complete"]
            assert observed.json()["inputs"][0]["status"] == "consumed"
        assert storage.engine.pool.checkedout() == 0
    finally:
        await storage.close()


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_steer_preserves_two_distinct_live_and_durable_responses(live_service):
    service = live_service
    client, path = service.client, service.workspace_path
    submitted = await client.post(
        path + "/threads",
        headers={"Idempotency-Key": "steer-display-source"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "[slow] [interruptible] [steer-proof] First question"}]},
        },
    )
    assert submitted.status_code == 201, submitted.text
    result = submitted.json()
    run_id, thread_id = result["run"]["id"], result["thread_id"]
    items_url = f"{path}/runs/{run_id}/items"
    async with asyncio.timeout(30):
        while True:
            initial = (await client.get(items_url)).json()
            if initial["cursor"] and initial["segments"]:
                replay = AttemptStream(service.redis, run_id, initial["segments"][-1]["attempt_number"], Bounds())
                if await service.redis.exists(*replay.keys) == 2:
                    break
            await asyncio.sleep(0.02)
        steered = await client.post(
            f"{path}/threads/{thread_id}/inbox",
            headers={"Idempotency-Key": "steer-display-update"},
            json={
                "kind": "message",
                "delivery": "steer",
                "agent_id": service.agent_id,
                "payload": {"content": [{"type": "text", "text": "[steer-update] Revise the answer"}]},
            },
        )
        assert steered.status_code == 201, steered.text
        events = []
        while True:
            final = (await client.get(items_url)).json()
            if final["complete"]:
                break
            async with client.stream(
                "GET", f"{path}/runs/{run_id}/events", params={"cursor": final["cursor"]}
            ) as stream:
                assert stream.status_code == 200
                kind = None
                async for line in stream.aiter_lines():
                    if line.startswith("event: "):
                        kind = line.removeprefix("event: ")
                    elif line.startswith("data: ") and kind == "data":
                        events.append(json.loads(line.removeprefix("data: "))["event"])
            await asyncio.sleep(0.02)
    assert final["status"] == "completed"
    texts = [item for segment in final["segments"] for item in segment["items"] if item["type"] == "text"]
    assert [item["text"] for item in texts] == ["Initial answer: cobalt.", "Revised answer: amber."]
    assert len({item["id"] for item in texts}) == 2
    assert all(item["complete"] for item in texts)
    # A checkpoint may trim frames before the reader polls; reconnects start from
    # fresh durable boundaries. Every actually delivered text frame must retain
    # the same request identity and content as its durable message.
    chunks = [event for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"]
    assert chunks
    by_id = {item["id"]: item["text"] for item in texts}
    assert all(event["messageId"] in by_id and event["delta"] in by_id[event["messageId"]] for event in chunks)
    starts = [event["messageId"] for event in events if event["type"] == "TEXT_MESSAGE_START"]
    assert len(starts) == len(set(starts))
    assert [item["id"] for item in final["inputs"]] == [result["entry"]["id"], steered.json()["entry"]["id"]]
    assert all(item["status"] == "consumed" for item in final["inputs"])
    reopened = (await client.get(items_url)).json()
    assert reopened["segments"] == final["segments"] and reopened["inputs"] == final["inputs"]
