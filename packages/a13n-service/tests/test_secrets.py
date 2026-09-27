"""Secrets: write-only values, workspace ownership, preconditions and execution resolution."""

import json

import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.secrets import service as secrets
from a13n_service.resources.secrets.schemas import SecretCreate, SecretRequirement, SecretUpdate
from a13n_service.resources.secrets.tables import SecretRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from a13n_service.tenancy.tables import PrincipalRow
from pydantic import SecretStr
from sqlalchemy import select

pytestmark = pytest.mark.anyio

VALUE = "sk-live-do-not-leak"


def etag(resource: dict) -> str:
    return f'"{resource["id"]}:{resource["version"]}"'


async def test_values_are_write_only_and_changes_need_preconditions(service) -> None:  # type: ignore[no-untyped-def]
    base = f"{service.workspace}/secrets"
    created = await service.client.post(base, json={"key": "OPENAI_API_KEY", "value": VALUE})
    assert created.status_code == 201, created.text
    secret = created.json()
    assert "scope" not in secret and "principal_id" not in secret
    assert created.headers["etag"] == etag(secret)
    duplicate = await service.client.post(base, json={"key": "OPENAI_API_KEY", "value": "other"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "already_exists"
    assert (await service.client.post(base, json={"key": "1BAD", "value": "x"})).status_code == 400

    item = f"{base}/{secret['id']}"
    replaced_body = {"value": VALUE + "-rotated"}
    assert (await service.client.put(item, json=replaced_body)).status_code == 428
    replaced = await service.client.put(item, json=replaced_body, headers={"if-match": etag(secret)})
    assert replaced.status_code == 200 and replaced.json()["version"] == secret["version"] + 1
    assert (await service.client.delete(item, headers={"if-match": etag(secret)})).status_code == 412

    responses = [created, replaced, await service.client.get(base), await service.client.get(item)]
    assert all(VALUE not in response.text for response in responses)
    async with short_session(service.runtime.storage) as session:
        row = await session.get(SecretRow, secret["id"])
        audit = (await session.scalars(select(AuditEventRow).where(AuditEventRow.target_id == secret["id"]))).all()
    assert row is not None and VALUE not in json.dumps(row.ciphertext)
    assert [event.action for event in audit] == ["secret.create", "secret.update"]
    assert all(VALUE not in json.dumps(event.details) for event in audit)

    resolved = await secrets.resolve_secrets(
        service.runtime.storage,
        service.runtime.keys,
        workspace_id=service.tenant.workspace_id,
        requirements=[SecretRequirement(key="OPENAI_API_KEY")],
    )
    assert resolved["OPENAI_API_KEY"].get_secret_value() == VALUE + "-rotated"

    deleted = await service.client.delete(item, headers={"if-match": replaced.headers["etag"]})
    assert deleted.status_code == 204
    assert (await service.client.get(item)).status_code == 404
    with pytest.raises(ServiceError) as missing:
        await secrets.resolve_secrets(
            service.runtime.storage,
            service.runtime.keys,
            workspace_id=service.tenant.workspace_id,
            requirements=[SecretRequirement(key="OPENAI_API_KEY")],
        )
    assert missing.value.details == {"kind": "secret", "id": "OPENAI_API_KEY"}
    assert VALUE not in str(missing.value)


async def test_workspace_members_share_metadata_and_only_writers_change_values(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    workspace_id = tenant.workspace_id
    colleague_id = new_object_id("usr")
    async with transaction(runtime.storage) as session:
        session.add(PrincipalRow(id=colleague_id, kind="user", name="Colleague", email="colleague@example.com"))

    def actor(principal_id: str, role: str) -> Principal:
        return Principal(principal_id, "user", (Grant(tenant.organization_id, workspace_id, BUILT_IN_ROLES[role]),))

    owner = actor(tenant.principal_id, "builder")
    colleague = actor(colleague_id, "builder")
    body = SecretCreate(key="GITHUB_TOKEN", value=SecretStr("first"))
    secret = await secrets.create_secret(runtime.storage, runtime.keys, owner, workspace_id, body)
    for role in ("viewer", "runner", "builder"):
        reader = actor(colleague_id, role)
        listed = await secrets.list_secrets(runtime.storage, reader, workspace_id, limit=10, cursor=None)
        assert [item.id for item in listed.items] == [secret.id]
        assert "value" not in listed.model_dump_json()
        if role != "builder":
            with pytest.raises(ServiceError) as denied:
                await secrets.replace_secret(
                    runtime.storage,
                    runtime.keys,
                    reader,
                    workspace_id,
                    secret.id,
                    SecretUpdate(value=SecretStr("denied")),
                    if_match=f'"{secret.id}:1"',
                )
            assert denied.value.code == "forbidden"
    changed = await secrets.replace_secret(
        runtime.storage,
        runtime.keys,
        colleague,
        workspace_id,
        secret.id,
        SecretUpdate(value=SecretStr("rotated")),
        if_match=f'"{secret.id}:1"',
    )
    requirement = SecretRequirement(key=secret.key)
    values = await secrets.resolve_secrets(
        runtime.storage, runtime.keys, workspace_id=workspace_id, requirements=[requirement]
    )
    assert values[secret.key].get_secret_value() == "rotated"
    await secrets.delete_secret(
        runtime.storage, colleague, workspace_id, secret.id, if_match=f'"{secret.id}:{changed.version}"'
    )
    replacement = await secrets.create_secret(runtime.storage, runtime.keys, colleague, workspace_id, body)
    assert replacement.id != secret.id and replacement.key == secret.key
    values = await secrets.resolve_secrets(
        runtime.storage, runtime.keys, workspace_id=workspace_id, requirements=[requirement]
    )
    assert values[secret.key].get_secret_value() == "first"


async def test_ciphertext_is_bound_to_its_resource_identity(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    actor = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["builder"]),)
    )
    first, second = [
        await secrets.create_secret(
            runtime.storage, runtime.keys, actor, tenant.workspace_id, SecretCreate(key=key, value=SecretStr("value"))
        )
        for key in ("FIRST", "SECOND")
    ]
    async with transaction(runtime.storage) as session:
        source = await session.get_one(SecretRow, first.id)
        target = await session.get_one(SecretRow, second.id)
        target.ciphertext = dict(source.ciphertext)
    with pytest.raises(ServiceError) as unbound:
        await secrets.resolve_secrets(
            runtime.storage,
            runtime.keys,
            workspace_id=tenant.workspace_id,
            requirements=[SecretRequirement(key=second.key)],
        )
    assert unbound.value.code == "unavailable"


async def test_key_lookup_is_confined_to_the_runs_workspace(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    await client.post("/api/v1/secrets", json={"key": "TOKEN", "value": "home"})
    other = await client.post(f"{service.organization}/workspaces", json={"key": "other", "name": "Other"})
    assert other.status_code == 201
    workspace_id = other.json()["id"]
    requirement = SecretRequirement(key="TOKEN")
    with pytest.raises(ServiceError) as missing:
        await secrets.resolve_secrets(
            service.runtime.storage, service.runtime.keys, workspace_id=workspace_id, requirements=[requirement]
        )
    assert missing.value.code == "not_found"
    created = await client.post(
        "/api/v1/secrets",
        json={"key": "TOKEN", "value": "destination"},
        headers={"X-Workspace-ID": workspace_id},
    )
    assert created.status_code == 201
    values = await secrets.resolve_secrets(
        service.runtime.storage, service.runtime.keys, workspace_id=workspace_id, requirements=[requirement]
    )
    assert values["TOKEN"].get_secret_value() == "destination"
