"""Explicit addresses, immutable keys, workspace confinement and deletion identity boundaries."""

import pytest
from a13n_service.infra.db import transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.secrets.tables import SecretRow
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.anyio


async def test_deleted_keys_can_be_reused_without_reusing_identity_or_etags(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    first = await client.post("/api/v1/secrets", json={"key": "TOKEN", "value": "first"})
    assert first.status_code == 201, first.text
    old_id, tag = first.json()["id"], first.headers["etag"]
    assert (await client.get("/api/v1/secrets/@TOKEN")).json()["id"] == old_id
    assert (await client.get("/api/v1/secrets/TOKEN")).status_code == 400
    assert (
        await client.put("/api/v1/secrets/@TOKEN", json={"key": "RENAMED", "value": "x"}, headers={"If-Match": tag})
    ).status_code == 400
    with pytest.raises(DBAPIError):
        async with transaction(service.runtime.storage) as session:
            await session.execute(update(SecretRow).where(SecretRow.id == old_id).values(key="RENAMED"))
    assert (await client.delete("/api/v1/secrets/@TOKEN", headers={"If-Match": tag})).status_code == 204
    replacement = await client.post("/api/v1/secrets", json={"key": "TOKEN", "value": "second"})
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["id"] != old_id
    assert (await client.get(f"/api/v1/secrets/{old_id}")).status_code == 404
    assert (await client.get("/api/v1/secrets/@TOKEN")).json()["id"] == replacement.json()["id"]
    assert (
        await client.put("/api/v1/secrets/@TOKEN", json={"value": "stale"}, headers={"If-Match": tag})
    ).status_code == 412
    # A key that looks exactly like an ID is still addressed explicitly.
    collision = await client.post("/api/v1/secrets", json={"key": old_id, "value": "collision"})
    assert collision.status_code == 201, collision.text
    assert (await client.get(f"/api/v1/secrets/{old_id}")).status_code == 404
    assert (await client.get(f"/api/v1/secrets/@{old_id}")).json()["id"] == collision.json()["id"]


async def test_workspace_selection_is_explicit_and_applies_to_both_address_forms(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    secret = (await client.post("/api/v1/secrets", json={"key": "TOKEN", "value": "value"})).json()
    other = await client.post(f"{service.organization}/workspaces", json={"key": "other", "name": "Other"})
    assert other.status_code == 201, other.text
    workspace_id = other.json()["id"]
    for address in (secret["id"], "@TOKEN"):
        response = await client.get(f"/api/v1/secrets/{address}", headers={"X-Workspace-ID": workspace_id})
        assert response.status_code == 404, response.text
    issued = await client.post(
        "/api/v1/users/me/keys", json={"workspace_id": service.tenant.workspace_id, "name": "references"}
    )
    assert issued.status_code == 201, issued.text
    bearer = {"Authorization": f"Bearer {issued.json()['secret']}"}
    del client.headers["X-Workspace-ID"]
    assert (await client.get("/api/v1/secrets")).status_code == 400
    assert (await client.get("/api/v1/secrets/@TOKEN", headers=bearer)).json()["id"] == secret["id"]
    mismatched = await client.get("/api/v1/secrets", headers={**bearer, "X-Workspace-ID": workspace_id})
    assert mismatched.status_code == 403


async def test_nested_references_resolve_once_and_all_keyed_kinds_keep_their_keys(service) -> None:  # type: ignore[no-untyped-def]
    import io
    import zipfile
    from collections import Counter

    from a13n_service.resources.agents.tables import AgentRow
    from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
    from a13n_service.resources.memories.tables import MemoryRow
    from a13n_service.resources.models.tables import ModelRow
    from a13n_service.resources.skills.tables import SkillRow
    from sqlalchemy import event

    from .environments_support import with_fake_providers

    await with_fake_providers(service)
    client = service.client
    memory = (await client.post("/api/v1/memories", json={"key": "box", "name": "Box"})).json()
    secret = (await client.post("/api/v1/secrets", json={"key": "box", "value": "value"})).json()
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: box\ndescription: A test skill.\n---\nRead carefully.\n")
    uploaded = await client.post(
        "/api/v1/uploads",
        files={"file": ("box.zip", package.getvalue(), "application/zip")},
        headers={"Idempotency-Key": "skill"},
    )
    assert uploaded.status_code == 200, uploaded.text
    skill = await client.post(
        "/api/v1/skills",
        json={"source": {"kind": "upload", "upload_id": uploaded.json()["upload_id"]}},
    )
    assert skill.status_code == 201, skill.text
    model = (await client.get("/api/v1/models/@unused")).json()
    body = {
        "key": "box",
        "name": "Box",
        "config": {
            "model": {"key": "unused"},
            "reviewer": {"model": {"key": "unused"}},
            "skills": [{"key": "box"}],
            "subagents": {"helper": {"agent": {"key": "builder"}}},
            "default_environment_template": {"key": "box"},
            "secret_requirements": [{"key": "box"}],
            "memory_mounts": [{"name": "box", "memory": {"key": "box"}, "access": "read"}],
        },
    }
    for malformed in ({}, {"id": model["id"], "key": "unused"}, "unused"):
        invalid_body = {**body, "config": {"model": malformed}}
        assert (await client.post("/api/v1/agents", json=invalid_body)).status_code == 400
    for legacy in (
        {"secret_id": secret["id"], "key": "box"},
        {"secret": {"key": "box"}},
        {"secret": {"id": secret["id"]}},
    ):
        invalid_body = {**body, "config": {**body["config"], "secret_requirements": [legacy]}}
        assert (await client.post("/api/v1/agents", json=invalid_body)).status_code == 400
    counts: Counter[str] = Counter()
    tables = (AgentRow, SkillRow, ModelRow, MemoryRow, EnvironmentTemplateRow, SecretRow)

    def count_references(connection, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        for table in tables:
            if f"{table.__tablename__}.key IN" in statement:
                counts[table.__tablename__] += 1

    engine = service.runtime.storage.engine.sync_engine
    event.listen(engine, "before_cursor_execute", count_references)
    try:
        created = await client.post("/api/v1/agents", json=body)
    finally:
        event.remove(engine, "before_cursor_execute", count_references)
    assert created.status_code == 201, created.text
    assert counts == {table.__tablename__: 1 for table in tables if table is not SecretRow}
    agent = created.json()
    revision = (await client.get(f"/api/v1/agents/{agent['id']}/revisions/{agent['default_revision_id']}")).json()
    config = revision["config"]
    assert config["model"]["id"] == config["reviewer"]["model"]["id"] == model["id"]
    assert config["skills"][0] == {"id": skill.json()["id"], "revision_id": skill.json()["default_revision_id"]}
    assert config["subagents"]["helper"]["agent"]["id"] == service.agent["id"]
    assert config["subagents"]["helper"]["revision_id"] == service.agent["default_revision_id"]
    assert config["default_environment_template"]["id"] == service.template["id"]
    assert config["memory_mounts"][0]["memory"]["id"] == memory["id"]
    assert config["secret_requirements"] == [{"key": "box"}]
    copied = await client.post("/api/v1/agents", json={"key": "copy", "name": "Copy", "config": config})
    assert copied.status_code == 201, copied.text
    copy = copied.json()
    saved = await client.get(f"/api/v1/agents/{copy['id']}/revisions/{copy['default_revision_id']}")
    assert saved.json()["config"] == config
    obsolete = {**body, "config": {"model": {"model_id": model["id"]}}}
    assert (await client.post("/api/v1/agents", json=obsolete)).status_code == 400

    for table, item, collection in (
        (AgentRow, agent, "agents"),
        (SkillRow, skill.json(), "skills"),
        (ModelRow, model, "models"),
        (MemoryRow, memory, "memories"),
        (EnvironmentTemplateRow, service.template, "environment-templates"),
        (SecretRow, secret, "secrets"),
    ):
        with pytest.raises(DBAPIError):
            async with transaction(service.runtime.storage) as session:
                await session.execute(update(table).where(table.id == item["id"]).values(key="renamed"))
        update_body = {"key": "renamed", **({"value": "changed"} if table is SecretRow else {})}
        result = await client.request(
            "PUT" if table is SecretRow else "PATCH",
            f"/api/v1/{collection}/{item['id']}",
            json=update_body,
            headers={"If-Match": f'"{item["id"]}:{item["version"]}"'},
        )
        assert result.status_code == 400, result.text
    archived = await client.post(f"/api/v1/agents/{agent['id']}/archive", headers={"If-Match": created.headers["etag"]})
    assert archived.status_code == 200, archived.text
    duplicate = await client.post("/api/v1/agents", json=body)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "already_exists"


async def test_id_paths_read_the_workspace_and_resource_only_once(service) -> None:  # type: ignore[no-untyped-def]
    from collections import Counter

    from sqlalchemy import event

    client = service.client
    created = await client.post("/api/v1/secrets", json={"key": "TOKEN", "value": "value"})
    assert created.status_code == 201
    counts: Counter[str] = Counter()

    def count_reads(connection, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        if statement.startswith("SELECT"):
            for table in ("workspaces", "secrets"):
                if f"FROM {table} " in statement or f"FROM {table}\n" in statement:
                    counts[table] += 1

    engine = service.runtime.storage.engine.sync_engine
    event.listen(engine, "before_cursor_execute", count_reads)
    try:
        response = await client.get(f"/api/v1/secrets/{created.json()['id']}")
    finally:
        event.remove(engine, "before_cursor_execute", count_reads)
    assert response.status_code == 200
    assert counts == {"workspaces": 1, "secrets": 1}


async def test_body_reference_does_not_disclose_foreign_resources(service) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.resources.agents.tables import AgentRow
    from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow

    organization_id, workspace_id, agent_id = new_object_id("org"), new_object_id("ws"), new_object_id("ap")
    async with transaction(service.runtime.storage) as session:
        session.add(OrganizationRow(id=organization_id, key="foreign", name="Foreign"))
        await session.flush()
        session.add(WorkspaceRow(id=workspace_id, organization_id=organization_id, key="foreign", name="Foreign"))
        await session.flush()
        session.add(
            AgentRow(
                id=agent_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                key="present",
                name="Present",
                description="",
                labels={},
                created_by_id=service.tenant.principal_id,
                updated_by_id=service.tenant.principal_id,
            )
        )
    errors = []
    for key in ("present", "absent"):
        response = await service.client.post(
            "/api/v1/subscriptions",
            headers={"X-Workspace-ID": workspace_id},
            json={
                "name": "Probe",
                "url": "https://example.com/hook",
                "kinds": ["run.completed"],
                "filter": {"agent": {"key": key}},
            },
        )
        assert response.status_code == 404
        errors.append(response.json()["error"]["details"])
    assert errors == [{"kind": "workspace", "id": workspace_id}] * 2, errors

    # Every body-reference entry point must authorize before looking up even a missing resource.
    config = {"model": {"key": "absent"}}
    for method, path, body in (
        ("POST", "/agents", {"key": "probe", "name": "Probe", "config": config}),
        ("POST", "/agents/validate", {"config": config}),
        ("POST", f"/agents/{agent_id}/revisions", {"config": config}),
        ("PUT", "/media-understanding-defaults", {"image": {"key": "absent"}}),
        ("POST", "/environments", {"template": {"key": "absent"}}),
        (
            "POST",
            f"/threads/{new_object_id('th')}/memories",
            {"name": "notes", "memory": {"key": "absent"}, "access": "read"},
        ),
    ):
        response = await service.client.request(
            method, f"/api/v1{path}", headers={"X-Workspace-ID": workspace_id}, json=body
        )
        assert response.status_code == 404, (path, response.text)
        assert response.json()["error"]["details"] == {"kind": "workspace", "id": workspace_id}, path
