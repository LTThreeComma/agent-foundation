"""Kill a production worker after baseline CAS and recover the same public source."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import httpx
import pytest
from a13n_service.infra.db import short_session
from a13n_service.runs import seal
from a13n_service.runs.input_frames import PreparedInputs
from a13n_service.runs.schemas import Checkpoint
from a13n_service.runs.snapshots import object_key
from a13n_service.runs.tables import AttemptRow, RunRow
from pydantic_ai.messages import BinaryContent, ModelRequest, UserPromptPart

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("cut", ["before_offer", "offered"])
@pytest.mark.parametrize("input_kind", ["text", "asset"])
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_process_death_without_input_checkpoint_recovers_exact_source(
    public_service, tmp_path, model_url, cut, input_kind
):
    service = public_service
    settings = service.app.state.settings
    value = settings.model_dump(mode="json")
    value["database"]["url"] = settings.database.url.get_secret_value()
    value["database"]["auto_migrate"] = False
    value["redis"]["url"] = settings.redis.url.get_secret_value()
    value["worker"].update(lease_seconds=3, scan_seconds=0.1, slots=1)
    config = tmp_path / "worker.json"
    import json

    config.write_text(json.dumps(value))
    config.chmod(0o600)
    marker = tmp_path / "before-offer"
    if input_kind == "asset":
        uploaded = await service.client.post(
            service.workspace_path + "/uploads",
            headers={"Idempotency-Key": "process-media-upload"},
            files={"file": ("source.txt", b"process-exact-bytes", "text/plain")},
        )
        assert uploaded.status_code == 200, uploaded.text
        asset = await service.client.post(
            service.workspace_path + "/assets",
            json={"upload_id": uploaded.json()["upload_id"], "name": "source.txt"},
        )
        assert asset.status_code == 201, asset.text
        content = [{"type": "asset", "asset_id": asset.json()["id"]}]
    else:
        content = [{"type": "text", "text": "Exactly one source"}]
    accepted = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "process-crash"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": content},
        },
    )
    assert accepted.status_code == 201
    entry_id, run_id = accepted.json()["entry"]["id"], accepted.json()["run"]["id"]
    processes = []
    log = (tmp_path / "worker.log").open("wb")

    async def launch(cut):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("fault_worker.py")),
            str(config),
            cut,
            str(marker),
            stdout=log,
            stderr=log,
            env={key: value for key, value in os.environ.items() if not key.startswith("A13N_")},
        )
        processes.append(process)
        return process

    try:
        first = await launch(cut)
        async with asyncio.timeout(15):
            while not marker.exists():
                assert first.returncode is None, (tmp_path / "worker.log").read_text()
                await asyncio.sleep(0.03)
        async with short_session(service.app.state.storage) as session:
            run = await session.get(RunRow, run_id)
            attempt = await session.get(AttemptRow, run.current_attempt_id)
            assert (attempt.harness_run_id is None) == (cut == "before_offer")
        stored = await service.app.state.objects.read(object_key(run.organization_id, run_id, "state"))
        assert Checkpoint.model_validate_json(stored.content).receipts == ()
        before = await service.client.get(f"{service.workspace_path}/runs/{run_id}/items")
        assert before.json()["inputs"][0]["status"] == "assigned"
        async with httpx.AsyncClient() as model:
            assert (await model.get(model_url.removesuffix("/v1") + "/fixture/model-state")).json()[
                "request_count"
            ] == 0
        first.kill()
        assert await asyncio.wait_for(first.wait(), 5) == -signal.SIGKILL
        async with asyncio.timeout(6):
            while not await seal.expire(service.app.state.storage, run_id, backoff_seconds=0):
                await asyncio.sleep(0.03)
        second = await launch("none")
        async with asyncio.timeout(15):
            while True:
                assert second.returncode is None, (tmp_path / "worker.log").read_text()
                result = await service.client.get(f"{service.workspace_path}/runs/{run_id}/items")
                if result.json()["complete"]:
                    break
                await asyncio.sleep(0.03)
        assert result.json()["status"] == "completed", result.text
        assert [(item["id"], item["status"]) for item in result.json()["inputs"]] == [(entry_id, "consumed")]
        assert result.json()["segments"][0]["interrupted"]
        assert len(result.json()["segments"]) == 2
        if input_kind == "asset":
            stored = await service.app.state.objects.read(object_key(run.organization_id, run_id, "state"))
            checkpoint = Checkpoint.model_validate_json(stored.content)
            prepared = PreparedInputs(run_id, receipts=(entry_id,))
            prepared.restore(checkpoint.state.message_history)
            assert prepared.incorporated(checkpoint.state.message_history) == (entry_id,)
            assert any(
                isinstance(native, BinaryContent) and native.data == b"process-exact-bytes"
                for message in checkpoint.state.message_history
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
                for native in part.content
            )
        async with httpx.AsyncClient() as model:
            assert (await model.get(model_url.removesuffix("/v1") + "/fixture/model-state")).json()[
                "request_count"
            ] == 1
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), 5)
        log.close()
