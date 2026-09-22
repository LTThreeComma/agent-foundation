"""Public resource setup and direct Harness execution for real MCP fixture tests."""

import base64

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.infra.crypto import KeyRing
from a13n_service.runs.execute import execute
from pydantic import SecretStr


async def configure_mcp(svc, mcp_url, model_url, *, auth="none", tool="increment", safe=False):
    app, client, path = svc.app, svc.client, svc.workspace_path
    app.state.key_ring = KeyRing(
        active_key_id="test", keys={"test": SecretStr(base64.b64encode(bytes(range(32))).decode())}
    )
    app.state.endpoint_policy = EndpointPolicy.from_operator_allowlist(
        private_cidrs=["127.0.0.0/8"], require_https=True, http_origins=[mcp_url, model_url.removesuffix("/v1")]
    )
    credential = {"token": "fixture-secret"} if auth == "bearer" else {"headers": {"X-API-Key": "fixture-secret"}}
    created = await client.post(
        path + "/connections",
        json={
            "type": "mcp",
            "name": "Counted effects",
            "auth": auth,
            "credential": None if auth == "none" else credential,
            "config": {
                "url": mcp_url + f"/{auth}/mcp",
                "tools": [tool, "read_count"],
                "recovery_retry_safe_tools": [tool] if safe else [],
            },
        },
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["id"]
    tested = await client.post(path + f"/connections/{connection_id}/test")
    assert tested.status_code == 200, tested.text
    assert {tool["name"] for tool in tested.json()["tools"]} >= {"increment", "read_count"}
    agent_path = path + "/agents/" + svc.agent_id
    agent = await client.get(agent_path)
    revision = await client.get(agent_path + "/revisions/" + agent.json()["default_revision_id"])
    revised = await client.post(
        agent_path + "/revisions",
        headers={"If-Match": agent.headers["etag"]},
        json={
            "config": {
                **revision.json()["config"],
                "connections": [{"connection_id": connection_id, "tools": [tool]}],
            }
        },
    )
    assert revised.status_code == 201, revised.text
    return connection_id


async def execute_claim(app, claim):
    await execute(
        app.state.storage,
        app.state.objects,
        claim,
        config=app.state.settings,
        redis=app.state.redis,
        catalog=app.state.model_catalog,
        tool_catalog=app.state.tool_catalog,
        keys=app.state.key_ring,
        endpoint_policy=app.state.endpoint_policy,
        admission=app.state.admission,
    )
