"""Real PostgreSQL/Redis identity, confinement and atomic account-wide audits."""

import httpx
import pytest
from a13n_service.app import build_app
from a13n_service.infra.audit import AuditEventRow, record
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.settings import Settings
from a13n_service.tenancy.authenticate import authenticate, login, logout
from a13n_service.tenancy.bootstrap import BootstrapInput, bootstrap
from a13n_service.tenancy.credentials import ApiKeyRow, TokenRow
from a13n_service.tenancy.tables import GrantRow, OrganizationRow, WorkspaceRow
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.anyio
PASSWORD = "test-only-password"


async def seed(storage):
    return await bootstrap(storage, BootstrapInput(email="user@example.com", password=SecretStr(PASSWORD)))


async def test_public_login_csrf_key_confinement_and_audit_isolation(database, redis_url):
    config = Settings(database=database, redis={"url": redis_url})
    app = build_app(settings=config)
    async with app.router.lifespan_context(app):
        storage = app.state.storage
        initialized = await seed(storage)
        other_workspace = new_object_id("ws")
        async with transaction(storage) as session:
            session.add(
                WorkspaceRow(id=other_workspace, organization_id=initialized.organization_id, key="other", name="Other")
            )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://service.test") as client:
            assert (await client.get("/api/v1/users/me")).status_code == 401
            response = await client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD})
            assert response.status_code == 200, response.text
            cookie = response.headers["set-cookie"]
            assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
            csrf = {"x-csrf-token": response.json()["csrf_token"]}
            assert (await client.get("/api/v1/users/me")).json()["id"] == initialized.principal_id
            endpoint = "/api/v1/users/me/keys"
            assert (
                await client.post(endpoint, json={"workspace_id": initialized.workspace_id, "name": "test"})
            ).status_code == 403
            created = await client.post(
                endpoint, headers=csrf, json={"workspace_id": initialized.workspace_id, "name": "test"}
            )
            assert created.status_code == 201, created.text
            secret = created.json()["secret"]
            bearer = {"authorization": "Bearer " + secret}
            assert (
                await client.get(f"/api/v1/workspaces/{initialized.workspace_id}", headers=bearer)
            ).status_code == 200
            assert (await client.get(f"/api/v1/workspaces/{other_workspace}", headers=bearer)).status_code == 403
            assert (
                await client.post(endpoint, headers=bearer, json={"workspace_id": other_workspace, "name": "escape"})
            ).status_code == 403
            assert (await client.post(endpoint, headers=csrf, json={"name": "missing workspace"})).status_code == 400
            audit = await client.get(f"/api/v1/workspaces/{initialized.workspace_id}/audit-events")
            assert audit.status_code == 200, audit.text
            assert {row["action"] for row in audit.json()["items"]} == {"organization.bootstrap", "credential.create"}
            first = await client.get(f"/api/v1/workspaces/{initialized.workspace_id}/audit-events?limit=1")
            next_cursor = first.json()["next_cursor"]
            assert next_cursor is not None
            second = await client.get(
                f"/api/v1/workspaces/{initialized.workspace_id}/audit-events",
                params={"limit": 1, "cursor": next_cursor},
            )
            assert second.status_code == 200, second.text
            assert second.json()["items"][0]["id"] != first.json()["items"][0]["id"]
            assert second.json()["next_cursor"] is None
            assert (
                await client.get(f"/api/v1/workspaces/{other_workspace}/audit-events", params={"cursor": next_cursor})
            ).status_code == 400
            assert (await client.get(f"/api/v1/workspaces/{other_workspace}/audit-events")).json()["items"] == []
            logout_response = await client.post("/api/v1/auth/logout", headers=csrf)
            assert logout_response.status_code == 200
            assert (await client.get("/api/v1/users/me")).status_code == 401
            # Logout revokes only the session, not the separately issued workspace key.
            assert (await client.get("/api/v1/users/me", headers=bearer)).status_code == 200
        async with short_session(storage) as session:
            events = (await session.scalars(select(AuditEventRow).where(AuditEventRow.organization_id.is_(None)))).all()
            assert [event.action for event in sorted(events, key=lambda row: row.occurred_at)] == [
                "session.create",
                "session.revoke",
            ]
            assert all(
                event.workspace_id is None and event.actor_id == initialized.principal_id and event.details == {}
                for event in events
            )
            key = await session.scalar(select(ApiKeyRow))
            assert key.secret_hash != secret and key.organization_id == initialized.organization_id
            assert await session.scalar(select(func.count()).select_from(ApiKeyRow)) == 1


@pytest.mark.parametrize("membership", ["zero", "multiple"])
async def test_session_audit_is_exactly_once_and_global_for_any_membership(database, membership):
    storage = Storage(database.url.get_secret_value())
    try:
        initialized = await seed(storage)
        async with transaction(storage) as session:
            if membership == "zero":
                await session.execute(delete(GrantRow))
            else:
                organization_id = new_object_id("org")
                session.add(OrganizationRow(id=organization_id, key="second", name="Second"))
                await session.flush()
                session.add(
                    GrantRow(
                        id=new_object_id("rb"),
                        principal_id=initialized.principal_id,
                        organization_id=organization_id,
                        role="admin",
                        created_by_id=initialized.principal_id,
                    )
                )
        issued = await login(storage, email="user@example.com", password=PASSWORD, session_seconds=60)
        proof = await authenticate(
            storage,
            secret=issued.secret,
            kind="session",
            csrf_token=issued.csrf_token,
            mutation=True,
            session_seconds=60,
        )
        await logout(storage, proof)
        async with short_session(storage) as session:
            events = (
                await session.scalars(
                    select(AuditEventRow).where(AuditEventRow.action.in_(["session.create", "session.revoke"]))
                )
            ).all()
            assert len(events) == 2
            assert {event.target_id for event in events} == {proof.credential_id}
            assert all(event.organization_id is None and event.workspace_id is None for event in events)
            token = await session.get(TokenRow, proof.credential_id)
            assert token.revoked_at is not None
    finally:
        await storage.close()


async def test_audit_failure_rolls_back_session_and_database_rejects_bad_scope(database, monkeypatch):
    import a13n_service.tenancy.authenticate as auth

    storage = Storage(database.url.get_secret_value())
    try:
        initialized = await seed(storage)
        actual_record = auth.record

        def fail_after_record(*args, **kwargs):
            actual_record(*args, **kwargs)
            raise RuntimeError("injected before commit")

        with monkeypatch.context() as patch:
            patch.setattr(auth, "record", fail_after_record)
            with pytest.raises(RuntimeError, match="injected"):
                await login(storage, email="user@example.com", password=PASSWORD, session_seconds=60)
        async with short_session(storage) as session:
            assert await session.scalar(select(func.count()).select_from(TokenRow)) == 0
            assert (
                await session.scalar(
                    select(func.count()).select_from(AuditEventRow).where(AuditEventRow.organization_id.is_(None))
                )
                == 0
            )
        issued = await login(storage, email="user@example.com", password=PASSWORD, session_seconds=60)
        proof = await authenticate(
            storage,
            secret=issued.secret,
            kind="session",
            csrf_token=issued.csrf_token,
            mutation=True,
            session_seconds=60,
        )
        with monkeypatch.context() as patch:
            patch.setattr(auth, "record", fail_after_record)
            with pytest.raises(RuntimeError, match="injected"):
                await logout(storage, proof)
        async with short_session(storage) as session:
            token = await session.get(TokenRow, proof.credential_id)
            assert token.revoked_at is None
            assert (
                await session.scalar(
                    select(func.count()).select_from(AuditEventRow).where(AuditEventRow.action == "session.revoke")
                )
                == 0
            )
        with pytest.raises(ValueError, match="account-wide"):
            async with transaction(storage) as session:
                record(
                    session,
                    organization_id=None,
                    workspace_id=None,
                    actor_id=initialized.principal_id,
                    action="credential.create",
                    target_kind="api_key",
                    target_id="key_example",
                )
        with pytest.raises(IntegrityError):
            async with transaction(storage) as session:
                session.add(
                    AuditEventRow(
                        id=new_object_id("audit"),
                        organization_id=None,
                        workspace_id=initialized.workspace_id,
                        actor_id=initialized.principal_id,
                        action="session.create",
                        target_kind="session",
                        target_id="session_example",
                        outcome="ok",
                        details={},
                    )
                )
        with pytest.raises(Exception, match="immutable"):
            async with transaction(storage) as session:
                await session.execute(update(AuditEventRow).values(action="changed"))
    finally:
        await storage.close()
