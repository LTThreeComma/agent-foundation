"""Public configuration creates encrypted connections and immutable selected revisions."""

import asyncio
import base64
import json

import httpx
import pytest
from a13n_service.app import build_app
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import short_session, transaction
from a13n_service.resources.agents.tables import AgentRevisionRow
from a13n_service.resources.models.tables import ModelProviderRow
from a13n_service.settings import Settings
from a13n_service.tenancy.bootstrap import BootstrapInput, bootstrap
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.anyio


async def test_public_model_agent_configuration_is_scoped_encrypted_and_immutable(database, redis_url):
    config = Settings(
        database=database,
        redis={"url": redis_url},
        encryption={"active_key_id": "test", "keys": {"test": base64.b64encode(bytes(range(32))).decode()}},
        providers={"private_cidrs": ["127.0.0.0/8"], "http_origins": ["http://127.0.0.1:7007"]},
    )
    app = build_app(settings=config)
    async with app.router.lifespan_context(app):
        storage = app.state.storage
        initialized = await bootstrap(
            storage, BootstrapInput(email="user@example.com", password=SecretStr("test-password"))
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://service.test") as client:
            logged_in = await client.post(
                "/api/v1/auth/login", json={"email": "user@example.com", "password": "test-password"}
            )
            assert logged_in.status_code == 200, logged_in.text
            client.headers["x-csrf-token"] = logged_in.json()["csrf_token"]
            org = f"/api/v1/organizations/{initialized.organization_id}"
            provider_body = {
                "workspace_id": initialized.workspace_id,
                "type": "openai",
                "name": "Test provider",
                "config": {"base_url": "http://127.0.0.1:7007/v1", "auth_mode": "bearer"},
                "credential": {"api_key": "provider-private-secret"},
            }
            created = await client.post(org + "/model-providers", json=provider_body)
            assert created.status_code == 201, created.text
            provider_id = created.json()["id"]
            assert created.json()["credential_configured"] is True
            assert "provider-private-secret" not in created.text and "credential" not in created.json()
            async with short_session(storage) as session:
                row = await session.get(ModelProviderRow, provider_id)
                assert "provider-private-secret" not in json.dumps(row.credential)
                plaintext = app.state.key_ring.reveal(
                    Envelope.model_validate(row.credential),
                    SecretLocation(initialized.organization_id, "model_providers", "credential", provider_id),
                )
                assert json.loads(plaintext) == provider_body["credential"]
            model_body = {
                "workspace_id": initialized.workspace_id,
                "provider_id": provider_id,
                "key": "test-model",
                "name": "Test model",
                "config": {"model_name": "scripted", "model_api": "openai.chat_completions", "context_window": 8192},
            }
            incompatible = await client.post(org + "/models", json={**model_body, "workspace_id": None})
            assert incompatible.status_code == 400, incompatible.text
            model = await client.post(org + "/models", json=model_body)
            assert model.status_code == 201, model.text
            assert (await client.post(org + "/models", json=model_body)).status_code == 409
            model_id = model.json()["id"]
            agent_path = f"/api/v1/workspaces/{initialized.workspace_id}/agents"
            agent_body = {
                "key": "assistant",
                "name": "Assistant",
                "config": {"model_id": model_id, "instructions": "First"},
            }
            agent = await client.post(agent_path, json=agent_body)
            assert agent.status_code == 201, agent.text
            assert (await client.post(agent_path, json=agent_body)).status_code == 409
            agent_id, first_revision = agent.json()["id"], agent.json()["default_revision_id"]
            current = await client.get(f"{agent_path}/{agent_id}")
            assert current.headers["etag"] == agent.headers["etag"]
            revisions = f"{agent_path}/{agent_id}/revisions"
            revision_body = {"config": {"model_id": model_id, "instructions": "Second"}}
            assert (await client.post(revisions, json=revision_body)).status_code == 428
            results = await asyncio.gather(
                *[
                    client.post(revisions, json=revision_body, headers={"If-Match": current.headers["etag"]})
                    for _ in range(2)
                ]
            )
            assert sorted(result.status_code for result in results) == [201, 412], [result.text for result in results]
            old = await client.get(revisions + "/" + first_revision)
            assert old.json()["config"]["instructions"] == "First"
            new_head = await client.get(f"{agent_path}/{agent_id}")
            assert new_head.json()["default_revision_id"] != first_revision
            assert new_head.headers["etag"] != current.headers["etag"]
        async with short_session(storage) as session:
            revisions = (await session.scalars(select(AgentRevisionRow))).all()
            assert [row.number for row in sorted(revisions, key=lambda row: row.number)] == [1, 2]
        with pytest.raises(DBAPIError, match="immutable"):
            async with transaction(storage) as session:
                await session.execute(update(AgentRevisionRow).values(note="rewrite history"))
        assert storage.engine.pool.checkedout() == 0
