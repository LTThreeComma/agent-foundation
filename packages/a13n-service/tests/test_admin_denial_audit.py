"""Public admin denial evidence survives the rejected operation with exact scope."""

import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.tenancy import audit
from a13n_service.tenancy.tables import GrantRow, OrganizationRow, WorkspaceRow
from sqlalchemy import select, update

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("target", ["same", "other_workspace", "other_organization", "confined_key"])
async def test_admin_denial_records_once_after_read_closes(public_service, monkeypatch, target):
    service, storage = public_service, public_service.app.state.storage
    organization_id, workspace_id = service.initialized.organization_id, service.initialized.workspace_id
    headers = {}
    if target == "confined_key":
        key = await service.client.post(
            "/api/v1/users/me/keys", json={"workspace_id": workspace_id, "name": "confined"}
        )
        assert key.status_code == 201
        headers["Authorization"] = "Bearer " + key.json()["secret"]
    if target != "same":
        workspace_id = new_object_id("ws")
        async with transaction(storage) as session:
            if target == "other_organization":
                organization_id = new_object_id("org")
                session.add(OrganizationRow(id=organization_id, key="foreign", name="Foreign"))
                await session.flush()
            session.add(WorkspaceRow(id=workspace_id, organization_id=organization_id, key="other", name="Other"))
    if target != "confined_key":
        async with transaction(storage) as session:
            await session.execute(
                update(GrantRow).where(GrantRow.principal_id == service.initialized.principal_id).values(role="viewer")
            )
    original, calls = audit.record, []

    def record(session, **kwargs):
        # New evidence transaction has not issued SQL yet; rejected read released its connection.
        assert storage.engine.pool.checkedout() == 0
        calls.append(kwargs)
        original(session, **kwargs)

    monkeypatch.setattr(audit, "record", record)
    response = await service.client.get(f"/api/v1/workspaces/{workspace_id}/audit-events", headers=headers)
    assert response.status_code == 403
    assert len(calls) == 1
    async with short_session(storage) as session:
        rows = (await session.scalars(select(AuditEventRow).where(AuditEventRow.outcome == "denied"))).all()
        assert len(rows) == 1
        row = rows[0]
        assert (row.organization_id, row.workspace_id, row.actor_id) == (
            organization_id,
            workspace_id,
            service.initialized.principal_id,
        )
        assert (row.action, row.target_kind, row.target_id) == ("audit.read", "workspace", workspace_id)
        assert row.details == {
            "verb": "admin",
            "credential_workspace_id": service.initialized.workspace_id if target == "confined_key" else None,
        }
    missing = await service.client.get("/api/v1/workspaces/ws_missing/audit-events", headers=headers)
    assert missing.status_code == 404 and len(calls) == 1


async def test_denial_audit_rollback_preserves_original_error_without_retry(public_service, monkeypatch):
    service, storage = public_service, public_service.app.state.storage
    async with transaction(storage) as session:
        await session.execute(
            update(GrantRow).where(GrantRow.principal_id == service.initialized.principal_id).values(role="viewer")
        )
    original, calls = audit.record, []

    def fail(session, **kwargs):
        original(session, **kwargs)
        calls.append(kwargs)
        raise RuntimeError("injected audit failure after staging")

    monkeypatch.setattr(audit, "record", fail)
    response = await service.client.get(service.workspace_path + "/audit-events")
    assert response.status_code == 403 and len(calls) == 1
    async with short_session(storage) as session:
        assert not (await session.scalars(select(AuditEventRow).where(AuditEventRow.outcome == "denied"))).all()
    assert storage.engine.pool.checkedout() == 0
