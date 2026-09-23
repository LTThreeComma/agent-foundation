"""Cross-principal managed-account isolation through the public Service boundary."""

import json
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.connections.oauth_state import reveal
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow, ConnectionRow
from a13n_service.tenancy.tables import GrantRow, PasswordRow, PrincipalRow, WorkspaceRow
from argon2 import PasswordHasher
from sqlalchemy import select

from dev.fixtures.composio_client import composio_url as composio_url
from dev.fixtures.composio_client import configured_managed, enroll_managed
from dev.fixtures.database import db_http_probe as db_http_probe

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db_http_probe")]


async def test_principal_workspace_and_key_isolation(public_service, composio_url, db_http_probe, caplog):
    svc = public_service
    path = await configured_managed(svc, composio_url)
    account_a, selector = await enroll_managed(svc, path)
    other_id, foreign = new_object_id("usr"), new_object_id("ws")
    async with transaction(svc.app.state.storage) as session:
        session.add(PrincipalRow(id=other_id, kind="user", email="other@example.com", name="Other", status="active"))
        session.add(
            WorkspaceRow(id=foreign, organization_id=svc.initialized.organization_id, key="foreign", name="Foreign")
        )
        await session.flush()
        session.add(PasswordRow(principal_id=other_id, hash=PasswordHasher().hash("other-password")))
        session.add(
            GrantRow(
                id=new_object_id("rb"),
                organization_id=svc.initialized.organization_id,
                workspace_id=svc.initialized.workspace_id,
                principal_id=other_id,
                role="runner",
                created_by_id=svc.initialized.principal_id,
            )
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=svc.app), base_url="https://service.test") as other:
        logged = await other.post(
            "/api/v1/auth/login", json={"email": "other@example.com", "password": "other-password"}
        )
        assert logged.status_code == 200
        other.headers["x-csrf-token"] = logged.json()["csrf_token"]
        assert (await other.get(path + "/authorization")).json()["status"] == "not_authorized"
        assert (await other.get(path + "/tools")).status_code != 200
        assert (
            await other.post(
                path + "/authorization/complete", json={**selector, "session_uri": "fixture://" + account_a}
            )
        ).status_code == 409
        account_b, _ = await enroll_managed(SimpleNamespace(client=other), path)
        assert account_a != account_b
        assert (await other.get(path + "/tools")).status_code == 200
        assert (await svc.client.post(path + "/test")).status_code == 200
        foreign_path = path.replace(svc.initialized.workspace_id, foreign)
        assert (await other.get(foreign_path + "/authorization")).status_code in {403, 404}
        key = await svc.client.post(
            "/api/v1/users/me/keys", json={"workspace_id": svc.initialized.workspace_id, "name": "Managed owner"}
        )
        assert key.status_code == 201
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=svc.app),
            base_url="https://service.test",
            headers={"authorization": "Bearer " + key.json()["secret"]},
        ) as confined:
            assert (await confined.get(foreign_path + "/authorization")).status_code == 403
            started = await confined.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
            assert started.status_code == 200, started.text
            value = started.json()
            body = {
                "authorization_id": value["authorization"]["id"],
                "generation": value["authorization"]["generation"],
                "session_uri": "fixture://" + value["redirect_url"].rsplit("/", 1)[-1],
            }
            assert (await confined.post(path + "/authorization/complete", json=body)).status_code == 403
            # The same actor can complete using an authenticated browser and the original flow cookie.
            svc.client.cookies.update(confined.cookies)
            assert (await svc.client.post(path + "/authorization/complete", json=body)).status_code == 200
        assert (await svc.client.post(path + "/revoke")).status_code == 200
        assert (await other.get(path + "/tools")).status_code == 200
        async with transaction(svc.app.state.storage) as session:
            principal = await session.get(PrincipalRow, other_id)
            principal.status = "disabled"
        async with httpx2.AsyncClient(base_url=composio_url, trust_env=False) as peer:
            before = (await peer.get("/fixture/state")).json()["requests"]
            assert (await other.get(path + "/tools")).status_code in {401, 403}
            assert (await peer.get("/fixture/state")).json()["requests"] == before
    async with short_session(svc.app.state.storage) as session:
        row = await session.scalar(
            select(ConnectionAuthorizationRow).where(ConnectionAuthorizationRow.principal_id == other_id)
        )
        private = reveal(row, svc.app.state.key_ring)
        connection = await session.get(ConnectionRow, row.connection_id)
        audits = list(await session.scalars(select(AuditEventRow)))
        persisted = json.dumps(
            {
                "authorization": row.credential,
                "connection": connection.credential,
                "audit": [
                    {column.name: str(getattr(event, column.name)) for column in AuditEventRow.__table__.columns}
                    for event in audits
                ],
            }
        )
        assert private["account_id"] not in persisted and private["user_id"] not in persisted
        assert "fixture-project-key" not in persisted and "session_uri" not in persisted
    public = (await svc.client.get(path)).text + (await svc.client.get(svc.workspace_path + "/audit-events")).text
    assert all(
        secret not in public + caplog.text
        for secret in [account_a, account_b, "fixture-project-key", "fixture-private-token", "fixture://"]
    )
    db_http_probe.assert_finished_tasks_released()


async def test_cancelled_completion_recovers_by_exact_readback_without_redeeming_again(
    public_service, composio_url, db_http_probe
):
    import asyncio

    svc = public_service
    path = await configured_managed(svc, composio_url)
    started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
    value = started.json()
    body = {
        "authorization_id": value["authorization"]["id"],
        "generation": value["authorization"]["generation"],
        "session_uri": "fixture://" + value["redirect_url"].rsplit("/", 1)[-1],
    }
    async with httpx2.AsyncClient(base_url=composio_url, trust_env=False) as peer:
        await peer.post("/fixture/control", json={"hold_complete": True})
        operation = asyncio.create_task(svc.client.post(path + "/authorization/complete", json=body))
        try:
            async with asyncio.timeout(4):
                while not any(
                    item["kind"] == "complete" for item in (await peer.get("/fixture/state")).json()["effects"]
                ):
                    await asyncio.sleep(0.01)
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
            status = (await svc.client.get(path + "/authorization")).json()
            assert status["status"] == "pending" and status["failure"] == {"reason": "unknown_after_dispatch"}
            await peer.post("/fixture/release-completion")
            assert (await svc.client.post(path + "/authorization/complete", json=body)).status_code == 200
            requests = (await peer.get("/fixture/state")).json()["requests"]
            assert sum(item["path"].endswith("/complete_auth") for item in requests) == 1
            db_http_probe.assert_finished_tasks_released()
        finally:
            await peer.post("/fixture/release-completion")
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("kind", ["setup", "revoke"])
async def test_orphaned_managed_operations_are_classified_without_redispatch(public_service, composio_url, kind):
    from datetime import UTC, datetime, timedelta

    from a13n_service.resources.connections.oauth_state import invalidate, seal
    from a13n_service.resources.connections.oauth_tokens import recover_deadlines

    svc = public_service
    path = await configured_managed(svc, composio_url)
    started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
    assert started.status_code == 200
    authorization_id = started.json()["authorization"]["id"]
    # Model the durable row left when an owner dies after claiming, before publishing.
    # Actual process death for completion and actions is qualified separately.
    async with transaction(svc.app.state.storage) as session:
        row = await session.get(ConnectionAuthorizationRow, authorization_id)
        if kind == "setup":
            bundle = reveal(row, svc.app.state.key_ring)
            for field in ("account_id", "scheme", "completion_method"):
                bundle.pop(field)
            seal(row, bundle, svc.app.state.key_ring)
        else:
            invalidate(row, "revoked", "pending_remote_attempt")
        row.failure = None
        row.operation_id = new_object_id("authop")
        row.operation_kind = kind
        row.operation_deadline = datetime.now(UTC) - timedelta(seconds=1)
        generation = row.generation
        operation_id = row.operation_id
    async with httpx2.AsyncClient(base_url=composio_url, trust_env=False) as peer:
        before = (await peer.get("/fixture/state")).json()["requests"]
        assert await recover_deadlines(svc.app.state.storage) == 1
        assert await recover_deadlines(svc.app.state.storage) == 0
        assert (await peer.get("/fixture/state")).json()["requests"] == before
        async with short_session(svc.app.state.storage) as session:
            row = await session.get(ConnectionAuthorizationRow, authorization_id)
            assert row.credential is None and row.operation_id == operation_id and row.generation == generation
            assert row.status == ("revoked" if kind == "revoke" else "reauthorization_required")
            assert row.failure == {
                "reason": "unknown_after_dispatch" if kind == "revoke" else "operation_deadline_unknown"
            }
