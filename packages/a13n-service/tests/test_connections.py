"""Public Connection configuration, credential ownership and concurrent edits."""

import asyncio
import base64
import json

import pytest
from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import short_session
from a13n_service.resources.connections.headers import normalize_headers
from a13n_service.resources.connections.tables import ConnectionRow
from pydantic import SecretStr


@pytest.mark.anyio
@pytest.mark.parametrize(
    "auth,credential",
    [("none", None), ("bearer", {"token": "private-token"}), ("headers", {"headers": {"X-API-Key": "private-token"}})],
)
async def test_public_connection_credentials_etags_and_disabling(public_service, auth, credential):
    svc = public_service
    svc.app.state.key_ring = KeyRing(
        active_key_id="test", keys={"test": SecretStr(base64.b64encode(bytes(range(32))).decode())}
    )
    path = svc.workspace_path + "/connections"
    body = {
        "type": "mcp",
        "name": "Tools",
        "config": {"url": "http://127.0.0.1:7007/mcp", "tools": ["lookup"], "recovery_retry_safe_tools": ["lookup"]},
        "auth": auth,
        "credential": credential,
    }
    response = await svc.client.post(path, json=body)
    assert response.status_code == 201, response.text
    connection = response.json()
    assert connection["credential_configured"] == (credential is not None)
    assert "private-token" not in response.text and "credential" not in connection
    item_path = path + "/" + connection["id"]
    listed = await svc.client.get(path)
    assert [item["id"] for item in listed.json()["items"]] == [connection["id"]]
    async with short_session(svc.app.state.storage) as session:
        row = await session.get(ConnectionRow, connection["id"])
        if credential is not None:
            assert "private-token" not in json.dumps(row.credential)
            plain = json.loads(
                svc.app.state.key_ring.reveal(
                    Envelope.model_validate(row.credential),
                    SecretLocation(row.organization_id, "connections", "credential", row.id),
                )
            )
            assert "private-token" in json.dumps(plain)
        else:
            assert row.credential is None
    assert (await svc.client.patch(item_path, json={"name": "Missing precondition"})).status_code == 428
    races = await asyncio.gather(
        *(
            svc.client.patch(item_path, json={"name": name}, headers={"If-Match": response.headers["etag"]})
            for name in ("One", "Two")
        )
    )
    assert sorted(result.status_code for result in races) == [200, 412]
    fresh = await svc.client.get(item_path)
    assert fresh.json()["version"] == 2
    updated = await svc.client.patch(
        item_path,
        headers={"If-Match": fresh.headers["etag"]},
        json={"config": {"url": "http://127.0.0.1:7007/changed", "tools": ["lookup"]}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["config"]["recovery_retry_safe_tools"] == []
    disabled = await svc.client.patch(
        item_path, headers={"If-Match": updated.headers["etag"]}, json={"enabled": False, "credential": None}
    )
    assert disabled.status_code == 200, disabled.text
    assert not disabled.json()["enabled"] and not disabled.json()["credential_configured"]
    audit = await svc.client.get(svc.workspace_path + "/audit-events")
    assert "private-token" not in audit.text
    assert svc.app.state.storage.engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "credential"},
        {"CoOkIe": "value"},
        {"Proxy-Authorization": "value"},
        {"Mcp-Session-Id": "value"},
        {"Connection": "close"},
        {"X-Ctx": "one", "x-ctx": "two"},
        {"X-Ctx": "before\r\nafter"},
        {"Bad Name": "value"},
        {"X-Ctx": "\x7f"},
    ],
)
def test_caller_headers_reject_reserved_duplicates_and_controls(headers):
    with pytest.raises(ValueError):
        normalize_headers(headers)


def test_header_casing_normalized_once():
    assert normalize_headers({"X-Conversation": "one"}) == {"x-conversation": "one"}
    assert normalize_headers({"Authorization": "Bearer token"}, authentication=True) == {
        "authorization": "Bearer token"
    }


@pytest.mark.anyio
async def test_caller_context_is_checked_against_agent_and_live_auth(public_service):
    svc = public_service
    svc.app.state.key_ring = KeyRing(
        active_key_id="test", keys={"test": SecretStr(base64.b64encode(bytes(range(32))).decode())}
    )
    path = svc.workspace_path
    created = await svc.client.post(
        path + "/connections",
        json={
            "type": "mcp",
            "name": "Context",
            "auth": "headers",
            "credential": {"headers": {"X-Auth": "private-token"}},
            "config": {"url": "http://127.0.0.1:7007/mcp", "tools": ["lookup"]},
        },
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["id"]
    agent_path = path + "/agents/" + svc.agent_id
    agent = await svc.client.get(agent_path)
    revision = await svc.client.get(agent_path + "/revisions/" + agent.json()["default_revision_id"])
    config = revision.json()["config"]

    async def submit(headers, key):
        return await svc.client.post(
            path + "/threads",
            headers={"Idempotency-Key": key},
            json={
                "kind": "message",
                "agent_id": svc.agent_id,
                "payload": {"content": [{"type": "text", "text": "Context proof"}]},
                "options": {"mcp_headers": headers},
            },
        )

    outside = await submit({connection_id: {"X-Conversation": "one"}}, "outside")
    assert outside.status_code == 400, outside.text
    revised = await svc.client.post(
        agent_path + "/revisions",
        headers={"If-Match": agent.headers["etag"]},
        json={"config": {**config, "connections": [{"connection_id": connection_id, "tools": ["lookup"]}]}},
    )
    assert revised.status_code == 201, revised.text
    collision = await submit({connection_id: {"x-AUTH": "caller"}}, "collision")
    assert collision.status_code == 400 and "collide" in collision.text, collision.text
    accepted = await submit({connection_id: {"X-Conversation": "one"}}, "valid")
    assert accepted.status_code == 201, accepted.text
    from a13n_service.runs.tables import RunRow

    async with short_session(svc.app.state.storage) as session:
        run = await session.get(RunRow, accepted.json()["run"]["id"])
        assert run.options["mcp_headers"] == {connection_id: {"x-conversation": "one"}}
    assert "private-token" not in accepted.text


def test_steer_context_projects_only_declared_scope_without_mutating_snapshot():
    from a13n_service.resources.agents.schemas import AgentConfig
    from a13n_service.runs.options import compatible
    from a13n_service.runs.schemas import RunOptions

    config = AgentConfig(
        model_id="mdl_1111111111111111", connections=[{"connection_id": "conn_1111111111111111", "tools": ["read"]}]
    )
    frozen = RunOptions(
        mcp_headers={"conn_1111111111111111": {"X-Ctx": "same"}, "conn_2222222222222222": {"X-Ctx": "inherited"}}
    )
    submitted = RunOptions(mcp_headers={"conn_1111111111111111": {"x-ctx": "same"}})
    assert compatible(config, frozen, submitted)
    assert "conn_2222222222222222" in frozen.mcp_headers
    assert not compatible(config, frozen, RunOptions(mcp_headers={"conn_1111111111111111": {"x-ctx": "different"}}))
    assert not compatible(config, frozen, RunOptions())
