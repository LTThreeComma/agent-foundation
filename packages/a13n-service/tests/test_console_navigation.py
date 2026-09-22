"""Browser identity restoration and authority-derived durable navigation."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.tenancy.authenticate import COOKIE_NAME, secret_hash
from a13n_service.tenancy.credentials import TokenRow
from a13n_service.tenancy.tables import GrantRow, OrganizationRow, WorkspaceRow
from sqlalchemy import func, select, update

pytestmark = pytest.mark.anyio


async def test_session_bootstrap_is_stable_isolated_and_cookie_only(public_service):
    service = public_service
    client = service.client
    endpoint = "/api/v1/auth/session"
    first = await client.get(endpoint)
    token = first.json()["csrf_token"]
    assert token == client.headers["x-csrf-token"]
    tabs = await asyncio.gather(client.get(endpoint), client.get(endpoint))
    assert all(response.json() == first.json() for response in tabs)
    transport = httpx.ASGITransport(app=service.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://service.test") as other:
        assert (await other.get(endpoint)).status_code == 401
        logged = await other.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "test-password"})
        other_token = logged.json()["csrf_token"]
        assert other_token != token
        for invalid in ("", "wrong", other_token):
            assert (await client.post("/api/v1/auth/logout", headers={"x-csrf-token": invalid})).status_code == 403
        keys = await asyncio.gather(
            *[
                client.post(
                    "/api/v1/users/me/keys",
                    headers={"x-csrf-token": tab.json()["csrf_token"]},
                    json={"workspace_id": service.initialized.workspace_id, "name": "tab"},
                )
                for tab in tabs
            ]
        )
        assert all(response.status_code == 201 for response in keys)
        bearer = {"authorization": "Bearer " + keys[0].json()["secret"]}
        # A valid cookie cannot turn a higher-precedence API key into a browser session.
        assert (await client.get(endpoint, headers=bearer)).status_code == 400
        secret = other.cookies.get(COOKIE_NAME)
        async with transaction(service.app.state.storage) as session:
            await session.execute(
                update(TokenRow)
                .where(TokenRow.secret_hash == secret_hash(secret))
                .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
            )
        assert (await other.get(endpoint)).status_code == 401
    old_cookies = httpx.Cookies(client.cookies)
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    async with httpx.AsyncClient(transport=transport, base_url="https://service.test", cookies=old_cookies) as revoked:
        assert (await revoked.get(endpoint)).status_code == 401


async def test_workspace_ambiguity_precedes_authority_and_collections_are_confined(public_service):
    service = public_service
    client = service.client
    initialized = service.initialized
    storage = service.app.state.storage
    other_org, other_ws = new_object_id("org"), new_object_id("ws")
    async with transaction(storage) as session:
        own = await session.get(WorkspaceRow, initialized.workspace_id)
        own.key = "collision"
        session.add(OrganizationRow(id=other_org, key="other", name="Other"))
        await session.flush()
        session.add(WorkspaceRow(id=other_ws, organization_id=other_org, key="collision", name="Hidden"))
    ambiguous = await client.get("/api/v1/workspaces/collision")
    assert ambiguous.status_code == 409
    assert other_org not in ambiguous.text and other_ws not in ambiguous.text and "Hidden" not in ambiguous.text
    assert (await client.get(service.workspace_path)).status_code == 200
    assert (await client.get("/api/v1/workspaces/" + other_ws)).status_code == 403
    page = await client.get("/api/v1/workspaces")
    assert [row["id"] for row in page.json()["items"]] == [initialized.workspace_id]
    assert page.json()["items"][0]["permissions"] == ["admin", "read", "run", "write"]
    async with transaction(storage) as session:
        other = await session.get(WorkspaceRow, other_ws)
        other.key = "unique-hidden"
        await session.execute(
            update(GrantRow).where(GrantRow.principal_id == initialized.principal_id).values(role="viewer")
        )
    assert (await client.get("/api/v1/workspaces/collision")).json()["id"] == initialized.workspace_id
    assert (await client.get("/api/v1/workspaces/unique-hidden")).status_code == 403
    page = await client.get("/api/v1/workspaces")
    assert page.json()["items"][0]["permissions"] == ["read"]
    assert (
        await client.post(
            service.workspace_path + "/agents",
            json={"key": "denied", "name": "Denied", "config": {"model_id": "mdl_0000000000000000"}},
        )
    ).status_code == 403


async def test_agent_selectors_revision_defaults_and_bounded_conversation_navigation(public_service):
    service = public_service
    client = service.client
    base = service.workspace_path
    agents = await client.get(base + "/agents", params={"limit": 1})
    assert agents.status_code == 200, agents.text
    assert [row["id"] for row in agents.json()["items"]] == [service.agent_id]
    head = await client.get(base + "/agents/assistant")
    old_revision = head.json()["default_revision_id"]
    revision = await client.get(base + f"/agents/assistant/revisions/{old_revision}")
    created = await client.post(
        base + "/agents/assistant/revisions",
        headers={"If-Match": head.headers["etag"]},
        json={"config": {**revision.json()["config"], "instructions": "Changed"}},
    )
    assert created.status_code == 201, created.text
    revisions = await client.get(base + "/agents/assistant/revisions", params={"limit": 1})
    next_page = await client.get(
        base + "/agents/assistant/revisions", params={"limit": 1, "cursor": revisions.json()["next_cursor"]}
    )
    assert next_page.status_code == 200, next_page.text
    assert {revisions.json()["items"][0]["id"], next_page.json()["items"][0]["id"]} == {
        old_revision,
        created.json()["id"],
    }
    current = await client.get(base + "/agents/assistant")
    default_url = base + f"/agents/assistant/revisions/{old_revision}/set-default"
    assert (await client.post(default_url, headers={"If-Match": head.headers["etag"]})).status_code == 412
    chosen = await client.post(default_url, headers={"If-Match": current.headers["etag"]})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["default_revision_id"] == old_revision
    org = "/api/v1/organizations/" + service.initialized.organization_id
    models = await client.get(org + "/models", params={"workspace_id": service.initialized.workspace_id})
    providers = await client.get(org + "/model-providers", params={"workspace_id": service.initialized.workspace_id})
    assert models.json()["items"][0]["id"] == revision.json()["config"]["model_id"]
    assert "credential" not in providers.json()["items"][0]
    submitted = await client.post(
        base + "/threads",
        headers={"Idempotency-Key": "navigation"},
        json={
            "kind": "message",
            "delivery": "next_run",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "Hello"}]},
        },
    )
    assert submitted.status_code == 201, submitted.text
    result = submitted.json()
    sessions = await client.get(base + "/sessions")
    assert [row["id"] for row in sessions.json()["items"]] == [result["session_id"]]
    threads = await client.get(base + "/threads", params={"session_id": result["session_id"]})
    assert [row["id"] for row in threads.json()["items"]] == [result["thread_id"]]
    runs = await client.get(base + f"/threads/{result['thread_id']}/runs")
    assert [row["id"] for row in runs.json()["items"]] == [result["run"]["id"]]
    assert (await client.get(base + f"/sessions/{result['session_id']}")).status_code == 200
    assert (await client.get(base + f"/threads/{result['thread_id']}")).status_code == 200
    async with short_session(service.app.state.storage) as session:
        assert await session.scalar(select(func.count()).select_from(TokenRow)) == 1
