"""Secrets: write-only values, owner-private secrets, preconditions and execution resolution."""

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
    assert (secret["scope"], secret["principal_id"]) == ("workspace", None)
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
        principal_id=service.tenant.principal_id,
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
            principal_id=service.tenant.principal_id,
            requirements=[SecretRequirement(key="OPENAI_API_KEY")],
        )
    assert missing.value.details == {"kind": "secret", "id": "OPENAI_API_KEY"}
    assert VALUE not in str(missing.value)


async def test_private_secrets_belong_to_their_owner(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    storage, keys = runtime.storage, runtime.keys
    workspace_id = tenant.workspace_id
    colleague_id = new_object_id("usr")
    async with transaction(storage) as session:
        session.add(PrincipalRow(id=colleague_id, kind="user", name="Colleague", email="colleague@example.com"))
    owner = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, workspace_id, BUILT_IN_ROLES["builder"]),)
    )
    colleague = Principal(
        colleague_id, "user", (Grant(tenant.organization_id, workspace_id, BUILT_IN_ROLES["builder"]),)
    )
    viewer = Principal(colleague_id, "user", (Grant(tenant.organization_id, workspace_id, BUILT_IN_ROLES["viewer"]),))

    def body(value: str, scope: str) -> SecretCreate:
        return SecretCreate.model_validate({"key": "GITHUB_TOKEN", "value": value, "scope": scope})

    shared = await secrets.create_secret(storage, keys, owner, workspace_id, body("shared", "workspace"))
    private = await secrets.create_secret(storage, keys, owner, workspace_id, body("mine", "user"))
    theirs = await secrets.create_secret(storage, keys, colleague, workspace_id, body("theirs", "user"))
    assert (private.scope, private.principal_id) == ("user", tenant.principal_id)

    listed = await secrets.list_secrets(storage, colleague, workspace_id, limit=10, cursor=None)
    assert {secret.id for secret in listed.items} == {shared.id, theirs.id}
    with pytest.raises(ServiceError) as hidden:
        await secrets.get_secret(storage, colleague, workspace_id, private.id)
    assert hidden.value.code == "not_found"
    with pytest.raises(ServiceError) as untouchable:
        await secrets.delete_secret(storage, colleague, workspace_id, private.id, if_match=f'"{private.id}:1"')
    assert untouchable.value.code == "not_found"
    with pytest.raises(ServiceError) as denied:
        await secrets.create_secret(storage, keys, viewer, workspace_id, body("x", "user"))
    assert denied.value.code == "forbidden"

    async def resolve(principal_id: str, scope: str) -> SecretStr:
        values = await secrets.resolve_secrets(
            storage,
            keys,
            workspace_id=workspace_id,
            principal_id=principal_id,
            requirements=[SecretRequirement.model_validate({"key": "GITHUB_TOKEN", "scope": scope})],
        )
        return values["GITHUB_TOKEN"]

    assert (await resolve(colleague_id, "workspace")).get_secret_value() == "shared"
    assert (await resolve(colleague_id, "user")).get_secret_value() == "theirs"
    assert (await resolve(tenant.principal_id, "user")).get_secret_value() == "mine"

    # A ciphertext is bound to its row: copying it onto another row cannot be revealed there.
    async with transaction(storage) as session:
        source = await session.get_one(SecretRow, private.id)
        target = await session.get_one(SecretRow, shared.id)
        target.ciphertext = dict(source.ciphertext)
    with pytest.raises(ServiceError) as unbound:
        await resolve(colleague_id, "workspace")
    assert unbound.value.code == "unavailable"


async def test_runners_own_their_private_secrets_and_admins_may_remove_them(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    storage, keys = runtime.storage, runtime.keys
    organization_id, workspace_id = tenant.organization_id, tenant.workspace_id
    runner_id = new_object_id("usr")
    async with transaction(storage) as session:
        session.add(PrincipalRow(id=runner_id, kind="user", name="Runner", email="runner@example.com"))

    def member(principal_id: str, role: str) -> Principal:
        return Principal(principal_id, "user", (Grant(organization_id, workspace_id, BUILT_IN_ROLES[role]),))

    runner, builder = member(runner_id, "runner"), member(tenant.principal_id, "builder")
    admin = member(tenant.principal_id, "admin")

    def body(scope: str) -> SecretCreate:
        return SecretCreate.model_validate({"key": "GITHUB_TOKEN", "value": "runner-token", "scope": scope})

    # `run` is enough for one's own private secret; workspace secrets still need `write`.
    private = await secrets.create_secret(storage, keys, runner, workspace_id, body("user"))
    rotated = await secrets.replace_secret(
        storage,
        keys,
        runner,
        workspace_id,
        private.id,
        SecretUpdate.model_validate({"value": "rotated"}),
        if_match=f'"{private.id}:{private.version}"',
    )
    with pytest.raises(ServiceError) as shared:
        await secrets.create_secret(storage, keys, runner, workspace_id, body("workspace"))
    assert shared.value.code == "forbidden"

    # Nobody else may change it, and only an admin may find it to delete it.
    current = f'"{private.id}:{rotated.version}"'
    change = SecretUpdate.model_validate({"value": "x"})
    with pytest.raises(ServiceError) as replaced:
        await secrets.replace_secret(storage, keys, admin, workspace_id, private.id, change, if_match=current)
    with pytest.raises(ServiceError) as hidden:
        await secrets.delete_secret(storage, builder, workspace_id, private.id, if_match=current)
    assert (replaced.value.code, hidden.value.code) == ("not_found", "not_found")
    await secrets.delete_secret(storage, admin, workspace_id, private.id, if_match=current)

    mine = await secrets.create_secret(storage, keys, runner, workspace_id, body("user"))
    await secrets.delete_secret(storage, runner, workspace_id, mine.id, if_match=f'"{mine.id}:{mine.version}"')
    assert (await secrets.list_secrets(storage, runner, workspace_id, limit=10, cursor=None)).items == []
