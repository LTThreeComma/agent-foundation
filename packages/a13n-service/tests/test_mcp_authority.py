"""Fresh production authority and cancellation at a real remote effect barrier."""

import asyncio

import httpx2
import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.tables import RunRow
from a13n_service.runs.worker import Worker
from a13n_service.tenancy.tables import GrantRow
from sqlalchemy import delete, text

from dev.fixtures.service_mcp import configure_mcp

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
@pytest.mark.parametrize("change", ["cancel", "grant", "admission"])
async def test_worker_rechecks_authority_and_cancels_actual_mcp_io(public_service, mcp_url, model_url, change):
    svc = public_service
    app, client, path = svc.app, svc.client, svc.workspace_path
    await configure_mcp(svc, mcp_url, model_url)
    admitted = []

    class Policy:
        async def accept(self, session, intent):
            pass

        async def proceed(self, session, call):
            admitted.append(call)
            if call.connection_id is not None:
                raise ServiceError("forbidden", "Tool admission denied")

    if change == "admission":
        app.state.admission = Policy()
    submitted = await client.post(
        path + "/threads",
        headers={"Idempotency-Key": change},
        json={
            "kind": "message",
            "agent_id": svc.agent_id,
            "payload": {"content": [{"type": "text", "text": "[service-mcp:increment] [hold-mcp]"}]},
        },
    )
    assert submitted.status_code == 201, submitted.text
    run_id = submitted.json()["run"]["id"]
    owner = Worker(
        app.state.storage,
        app.state.objects,
        app.state.redis,
        config=app.state.settings.model_copy(
            update={
                "worker": app.state.settings.worker.model_copy(update={"scan_seconds": 0.1, "authority_seconds": 0.1})
            }
        ),
        catalog=app.state.model_catalog,
        tool_catalog=app.state.tool_catalog,
        keys=app.state.key_ring,
        endpoint_policy=app.state.endpoint_policy,
        admission=app.state.admission,
    )
    active = asyncio.create_task(owner.run())
    try:
        async with httpx2.AsyncClient(trust_env=False) as peer:
            if change != "admission":
                async with asyncio.timeout(15):
                    while (await peer.get(mcp_url + "/fixture/state")).json()["effects"] != 1:
                        assert not active.done()
                        await asyncio.sleep(0.025)
                async with short_session(app.state.storage) as session:
                    assert (
                        await session.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state='idle in transaction'"
                            )
                        )
                        == 0
                    )
                if change == "grant":
                    async with transaction(app.state.storage) as session:
                        await session.execute(
                            delete(GrantRow).where(GrantRow.principal_id == svc.initialized.principal_id)
                        )
                else:
                    interrupted = await client.post(path + f"/runs/{run_id}/interrupt")
                    assert interrupted.status_code == 200, interrupted.text
            async with asyncio.timeout(15):
                while True:
                    async with short_session(app.state.storage) as session:
                        run = await session.get(RunRow, run_id)
                        status, failure = run.status, run.failure
                    if status in {"failed", "cancelled"}:
                        break
                    await asyncio.sleep(0.05)
            assert status == ("cancelled" if change == "cancel" else "failed")
            if change == "admission":
                assert failure["message"] == "Tool admission denied"
                tools = [call for call in admitted if call.connection_id is not None]
                assert len(tools) == 1 and tools[0].call_id and tools[0].tool_name == "increment"
            await peer.post(mcp_url + "/fixture/release")
            counted = (await peer.get(mcp_url + "/fixture/state")).json()
            assert counted["effects"] == (0 if change == "admission" else 1)
            assert len(counted["calls"]) == (0 if change == "admission" else 1)
    finally:
        active.cancel()
        await asyncio.gather(active, return_exceptions=True)
    assert app.state.storage.engine.pool.checkedout() == 0


async def test_discovery_cache_is_versioned_and_disabled_resource_stays_unavailable(public_service, mcp_url, model_url):
    svc = public_service
    connection = await configure_mcp(svc, mcp_url, model_url)
    path = svc.workspace_path + "/connections/" + connection
    read = await svc.client.get(path + "/tools")
    assert read.status_code == 200 and len(read.json()["tools"]) == 4, read.text
    cache_key = f"a13n:connection-tools:{svc.initialized.organization_id}:{connection}:1"
    assert 0 < await svc.app.state.redis.ttl(cache_key) <= 30
    current = await svc.client.get(path)
    disabled = await svc.client.patch(path, headers={"If-Match": current.headers["etag"]}, json={"enabled": False})
    assert disabled.status_code == 200
    assert (await svc.client.get(path + "/tools")).status_code != 200
    assert await svc.app.state.redis.exists(cache_key)
    # Retained hints cannot override a fresh SQL resource check.
    denied = await svc.client.post(path + "/test")
    assert denied.status_code != 200
