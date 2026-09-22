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
from a13n_service.runs.schemas import Checkpoint
from a13n_service.runs.snapshots import object_key
from a13n_service.runs.tables import AttemptRow, RunRow

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("cut", ["before_offer", "offered"])
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_process_death_without_input_checkpoint_recovers_exact_source(public_service, tmp_path, model_url, cut):
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
    accepted = await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": "process-crash"},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "Exactly one source"}]},
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
