"""Models: catalogue and manual creation, scope compatibility with the provider, resolution and execution."""

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.models.runtime import open_model
from a13n_service.resources.models.schemas import ModelConfig, ModelCreate, ModelUpdate
from a13n_service.resources.models.service import create_model, resolve_model, update_model
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, ExecutionAuthority, Grant, Principal, WorkspaceScope
from a13n_service.tenancy.tables import WorkspaceRow
from pydantic_ai.models.openai import OpenAIResponsesModel

pytestmark = pytest.mark.anyio

SECRET = "sk-model-secret"


def etag(resource: dict) -> str:
    return f'"{resource["id"]}:{resource["version"]}"'


async def post(service, path: str, body: dict, status: int = 201) -> dict:  # type: ignore[no-untyped-def]
    response = await service.client.post(service.organization + path, json=body)
    assert response.status_code == status, response.text
    return response.json()


async def provider(service, workspace_id: str | None = None, **changes: object) -> dict:  # type: ignore[no-untyped-def]
    body = {"workspace_id": workspace_id, "type": "openai", "name": "OpenAI", "credential": {"api_key": SECRET}}
    return await post(service, "/model-providers", {**body, **changes})


def manual(provider_id: str, key: str, workspace_id: str | None, **config: object) -> dict:
    return {
        "workspace_id": workspace_id,
        "provider_id": provider_id,
        "key": key,
        "name": key,
        "config": {"model_name": key, "model_api": "openai.chat_completions", **config},
    }


async def add_workspace(service) -> str:  # type: ignore[no-untyped-def]
    workspace_id = new_object_id("ws")
    async with transaction(service.runtime.storage) as session:
        session.add(
            WorkspaceRow(id=workspace_id, organization_id=service.tenant.organization_id, key="second", name="Second")
        )
    return workspace_id


def admin(service) -> Principal:  # type: ignore[no-untyped-def]
    return Principal(
        service.tenant.principal_id, "user", (Grant(service.tenant.organization_id, None, BUILT_IN_ROLES["admin"]),)
    )


async def test_models_are_created_from_the_catalogue_or_manually(service) -> None:  # type: ignore[no-untyped-def]
    shared = await provider(service)
    catalog = (await service.client.get(f"{service.organization}/model-providers/{shared['id']}/catalog")).json()
    known = {item["key"]: item for item in catalog["items"]}
    assert known and all(key.startswith("openai:") for key in known)
    assert known["openai:gpt-5.5"]["pricing"]["provider"] == "openai"

    body = {"workspace_id": None, "provider_id": shared["id"], "key": "gpt-5-5", "name": "GPT-5.5"}
    created = await post(service, "/models", {**body, "catalog_key": "openai:gpt-5.5"})
    assert created["config"]["model_name"] == "gpt-5.5"
    assert created["config"]["model_api"] == "openai.responses"
    assert created["config"]["characteristics"]["context_window_tokens"] == 1050000
    assert created["pricing"]["model"] == known["openai:gpt-5.5"]["pricing"]["model"]

    duplicate = await post(service, "/models", {**body, "catalog_key": "openai:gpt-5.5"}, status=409)
    assert duplicate["error"]["code"] == "already_exists"
    other_vendor = await post(
        service, "/models", {**body, "key": "claude", "catalog_key": "anthropic:claude-opus-5"}, 400
    )
    assert other_vendor["error"]["details"]["field"] == "catalog_key"
    await post(service, "/models", {**body, "key": "both", "catalog_key": "openai:gpt-5.5", "config": {}}, status=400)
    neither = await post(service, "/models", {**body, "key": "neither"}, status=400)
    assert neither["error"]["details"]["field"] == "config"

    workspace_model = await post(service, "/models", manual(shared["id"], "custom", service.tenant.workspace_id))
    assert workspace_model["workspace_id"] == service.tenant.workspace_id and workspace_model["pricing"] is None
    unsupported = await post(
        service, "/models", manual(shared["id"], "messages", None, model_api="anthropic.messages"), status=400
    )
    assert unsupported["error"]["details"]["field"] == "config.model_api"

    listed = (await service.client.get(f"{service.organization}/models")).json()
    assert {item["id"] for item in listed["items"]} == {created["id"], workspace_model["id"]}


async def test_a_model_scope_must_be_covered_by_its_provider(service) -> None:  # type: ignore[no-untyped-def]
    first, second = service.tenant.workspace_id, await add_workspace(service)
    confined = await provider(service, first)
    for workspace_id in (None, second):
        refused = await post(service, "/models", manual(confined["id"], "model", workspace_id), status=400)
        assert refused["error"]["details"]["field"] == "workspace_id"
    await post(service, "/models", manual(confined["id"], "model", first))

    disabled = await provider(service, None, name="Disabled")
    patched = await service.client.patch(
        f"{service.organization}/model-providers/{disabled['id']}",
        json={"enabled": False},
        headers={"if-match": etag(disabled)},
    )
    assert patched.status_code == 200, patched.text
    refused = await post(service, "/models", manual(disabled["id"], "model", None), status=422)
    assert refused["error"]["details"] == {"kind": "model_provider", "id": disabled["id"]}

    # A model spends its provider's credential, so a workspace builder configures models only under providers
    # it may write: never in the shared collection, nor under a shared provider, even for its own workspace.
    storage, organization_id, registry = (
        service.runtime.storage,
        service.tenant.organization_id,
        service.runtime.registry,
    )
    builder = Principal(
        service.tenant.principal_id, "user", (Grant(organization_id, first, BUILT_IN_ROLES["builder"]),)
    )
    shared = await provider(service, None, name="Shared")
    for workspace_id in (None, first):
        with pytest.raises(ServiceError, match="cannot perform"):
            body = ModelCreate.model_validate(manual(shared["id"], "model", workspace_id))
            await create_model(storage, builder, organization_id, body, registry=registry)
    body = ModelCreate.model_validate(manual(confined["id"], "own", first))
    assert (await create_model(storage, builder, organization_id, body, registry=registry)).workspace_id == first

    # Under a shared provider, the workspace's model may be renamed there, but only reconfigured by the provider's writers.
    used = await post(service, "/models", manual(shared["id"], "used", first))
    renamed = await update_model(
        storage,
        builder,
        organization_id,
        used["id"],
        ModelUpdate(name="Renamed"),
        if_match=etag(used),
        registry=registry,
    )
    config = ModelConfig.model_validate({**used["config"], "max_tokens": 1024})
    with pytest.raises(ServiceError, match="cannot perform"):
        await update_model(
            storage,
            builder,
            organization_id,
            used["id"],
            ModelUpdate(config=config),
            if_match=f'"{renamed.id}:{renamed.version}"',
            registry=registry,
        )


async def test_models_resolve_for_execution_only_while_enabled_and_in_scope(service) -> None:  # type: ignore[no-untyped-def]
    organization_id, workspace_id = service.tenant.organization_id, service.tenant.workspace_id
    scope = WorkspaceScope(organization_id, workspace_id)
    shared = await provider(service)
    model = await post(service, "/models", manual(shared["id"], "gpt", None, max_tokens=1024))
    item = f"{service.organization}/models/{model['id']}"

    assert (await service.client.patch(item, json={"enabled": False})).status_code == 428
    disabled = await service.client.patch(
        item, json={"enabled": False, "name": "Paused"}, headers={"if-match": etag(model)}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False and disabled.json()["name"] == "Paused"
    storage, actor = service.runtime.storage, admin(service)
    async with short_session(storage) as session:
        with pytest.raises(ServiceError) as refused:
            await resolve_model(session, actor, scope, model["id"])
    assert refused.value.code == "disabled"

    enabled = await service.client.patch(item, json={"enabled": True}, headers={"if-match": disabled.headers["etag"]})
    assert enabled.status_code == 200, enabled.text
    async with short_session(storage) as session:
        resolved = await resolve_model(session, actor, scope, model["id"])
    assert resolved.config.max_tokens == 1024 and resolved.provider.id == shared["id"]
    assert resolved.provider.reveal_credential(service.runtime.keys) == {"api_key": SECRET}
    assert SECRET not in repr(resolved)

    # Execution authority narrows what the run's principal may use.
    reader = ExecutionAuthority(
        principal_id=actor.id, organization_id=organization_id, workspace_id=workspace_id, verbs=frozenset({"read"})
    )
    elsewhere = WorkspaceScope(organization_id, await add_workspace(service))
    confined = await post(service, "/models", manual(shared["id"], "confined", elsewhere.workspace_id))
    async with short_session(storage) as session:
        with pytest.raises(ServiceError, match="delegation"):
            await resolve_model(session, actor, scope, model["id"], authority=reader)
        with pytest.raises(ServiceError) as hidden:
            await resolve_model(session, actor, scope, confined["id"])
    assert hidden.value.code == "not_found"

    provider_item = f"{service.organization}/model-providers/{shared['id']}"
    current = (await service.client.get(provider_item)).headers["etag"]
    response = await service.client.patch(provider_item, json={"enabled": False}, headers={"if-match": current})
    assert response.status_code == 200, response.text
    async with short_session(storage) as session:
        with pytest.raises(ServiceError) as refused:
            await resolve_model(session, actor, scope, model["id"])
    assert refused.value.details == {"kind": "model_provider", "id": shared["id"]}


async def test_model_updates_validate_the_api_and_replace_pricing(service) -> None:  # type: ignore[no-untyped-def]
    shared = await provider(service)
    body = {"workspace_id": None, "provider_id": shared["id"], "key": "gpt", "name": "GPT"}
    model = await post(service, "/models", {**body, "catalog_key": "openai:gpt-5.5"})
    item = f"{service.organization}/models/{model['id']}"
    config = {**model["config"], "model_api": "anthropic.messages"}
    refused = await service.client.patch(item, json={"config": config}, headers={"if-match": etag(model)})
    assert refused.status_code == 400 and refused.json()["error"]["details"]["field"] == "config.model_api"

    config["model_api"] = "openai.chat_completions"
    updated = await service.client.patch(
        item, json={"config": config, "pricing": None}, headers={"if-match": etag(model)}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["config"]["model_api"] == "openai.chat_completions" and updated.json()["pricing"] is None
    assert updated.json()["version"] == model["version"] + 1


async def test_models_carry_a_description_and_may_start_disabled(service) -> None:  # type: ignore[no-untyped-def]
    shared = await provider(service)
    body = {**manual(shared["id"], "draft", None), "description": "Staged rollout", "enabled": False}
    model = await post(service, "/models", body)
    assert (model["description"], model["enabled"]) == ("Staged rollout", False)
    # Created disabled, it is never usable by runs before it is reviewed and enabled.
    scope = WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)
    async with short_session(service.runtime.storage) as session:
        with pytest.raises(ServiceError) as refused:
            await resolve_model(session, admin(service), scope, model["id"])
    assert refused.value.code == "disabled"

    item = f"{service.organization}/models/{model['id']}"
    described = await service.client.patch(item, json={"description": "Ready"}, headers={"if-match": etag(model)})
    assert described.status_code == 200 and described.json()["description"] == "Ready"
    too_long = await service.client.patch(
        item, json={"description": "x" * 2049}, headers={"if-match": described.headers["etag"]}
    )
    assert too_long.status_code == 400
    assert (await post(service, "/models", manual(shared["id"], "plain", None)))["description"] == ""


async def test_a_price_names_the_model_it_prices(service) -> None:  # type: ignore[no-untyped-def]
    """Execution looks a price up by the upstream model name, so a price for another model is refused."""
    shared = await provider(service)
    catalog = (await service.client.get(f"{service.organization}/model-providers/{shared['id']}/catalog")).json()
    assert all(item["pricing"]["model"] == item["model_name"] for item in catalog["items"] if item["pricing"])
    price = next(item["pricing"] for item in catalog["items"] if item["key"] == "openai:gpt-5.5")

    other = await post(service, "/models", {**manual(shared["id"], "gpt-5-5", None), "pricing": price}, status=400)
    assert other["error"]["details"]["field"] == "pricing.model"
    model = await post(
        service, "/models", {**manual(shared["id"], "gpt", None, model_name="gpt-5.5"), "pricing": price}
    )
    assert model["pricing"]["model"] == "gpt-5.5"

    # Renaming the upstream model must replace or remove its price in the same change.
    item = f"{service.organization}/models/{model['id']}"
    renamed = {**model["config"], "model_name": "gpt-5.5-pro"}
    stale = await service.client.patch(item, json={"config": renamed}, headers={"if-match": etag(model)})
    assert stale.status_code == 400 and stale.json()["error"]["details"]["field"] == "pricing.model"
    repriced = await service.client.patch(
        item,
        json={"config": renamed, "pricing": {**price, "model": "gpt-5.5-pro"}},
        headers={"if-match": etag(model)},
    )
    assert repriced.status_code == 200, repriced.text
    assert repriced.json()["pricing"]["model"] == "gpt-5.5-pro"


async def test_open_model_builds_the_native_model_with_the_revealed_secrets(service) -> None:  # type: ignore[no-untyped-def]
    runtime = service.runtime
    # Building makes no request; a loopback endpoint keeps the endpoint policy independent of local DNS.
    shared = await provider(
        service, None, config={"base_url": "http://127.0.0.1:9/v1"}, extra_headers={"x-gateway-key": "gw-secret"}
    )
    body = {"workspace_id": None, "provider_id": shared["id"], "key": "gpt", "name": "GPT"}
    model = await post(service, "/models", {**body, "catalog_key": "openai:gpt-5.5"})
    scope = WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)
    async with short_session(runtime.storage) as session:
        resolved = await resolve_model(session, admin(service), scope, model["id"])
    async with open_model(
        resolved,
        registry=runtime.registry,
        keys=runtime.keys,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    ) as native:
        assert isinstance(native, OpenAIResponsesModel)
        assert native.model_name == "gpt-5.5"
        assert native.client.api_key == SECRET
        assert native.client.default_headers["x-gateway-key"] == "gw-secret"
        assert str(native.client.base_url) == "http://127.0.0.1:9/v1/"


async def test_agents_reference_models_through_model_resolution(service) -> None:  # type: ignore[no-untyped-def]
    shared = await provider(service)
    model = await post(service, "/models", manual(shared["id"], "gpt", None))
    agents = f"{service.workspace}/agents"
    created = await service.client.post(
        agents, json={"key": "helper", "name": "Helper", "config": {"model": {"model_id": model["id"]}}}
    )
    assert created.status_code == 201, created.text

    elsewhere = await post(service, "/models", manual(shared["id"], "elsewhere", await add_workspace(service)))
    hidden = await service.client.post(
        agents, json={"key": "hidden", "name": "Hidden", "config": {"model": {"model_id": elsewhere["id"]}}}
    )
    assert hidden.status_code == 400 and hidden.json()["error"]["details"]["kind"] == "model"

    await service.client.patch(
        f"{service.organization}/models/{model['id']}", json={"enabled": False}, headers={"if-match": etag(model)}
    )
    refused = await service.client.post(
        agents, json={"key": "late", "name": "Late", "config": {"model": {"model_id": model["id"]}}}
    )
    assert refused.status_code == 400 and refused.json()["error"]["details"]["kind"] == "model"
