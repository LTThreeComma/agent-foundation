"""Public managed-account journey helpers for counted native protocol tests."""

import pytest
from a13n_harness.providers.connector.composio import catalog, runtime
from a13n_harness.providers.endpoint_policy import EndpointPolicy

from dev.fixtures.process import fixture_process


@pytest.fixture
def composio_url(tmp_path, monkeypatch):
    with fixture_process("dev.fixtures.composio", arguments=("--database", str(tmp_path / "composio.sqlite"))) as url:
        # Only replace the peer destination; native requests still use real policy and HTTP.
        monkeypatch.setattr(catalog, "COMPOSIO_ENDPOINT", url)
        monkeypatch.setattr(runtime, "COMPOSIO_ENDPOINT", url)
        monkeypatch.setattr(runtime, "COMPOSIO_CONNECT_ENDPOINT", url)
        yield url


async def configured_managed(svc, composio_url):
    import base64

    from a13n_service.infra.crypto import KeyRing
    from a13n_service.settings import Managed
    from pydantic import SecretStr

    svc.app.state.key_ring = KeyRing(
        active_key_id="test", keys={"test": SecretStr(base64.b64encode(bytes(range(32))).decode())}
    )
    svc.app.state.endpoint_policy = EndpointPolicy.from_operator_allowlist(
        private_cidrs=["127.0.0.0/8"], http_origins=[composio_url]
    )
    svc.app.state.settings = svc.app.state.settings.model_copy(
        update={
            "managed": Managed(
                verifier_url="https://service.test/managed/verify",
                return_urls=("https://service.test/connections",),
                operation_seconds=5,
            )
        }
    )
    created = await svc.client.post(
        svc.workspace_path + "/connections",
        json={
            "type": "composio",
            "name": "GitHub",
            "auth": "managed",
            "credential": {"api_key": "fixture-project-key"},
            "config": {
                "app": "github",
                "actions": ["GITHUB_CREATE_ISSUE"],
                "auth_config_id": "ac_fixture",
                "toolkit_version": "20260923_00",
            },
        },
    )
    assert created.status_code == 201, created.text
    assert "fixture-project-key" not in created.text
    path = svc.workspace_path + "/connections/" + created.json()["id"]
    return path


async def enroll_managed(svc, path):
    started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
    assert started.status_code == 200, started.text
    data = started.json()
    account_id = data["redirect_url"].rsplit("/", 1)[-1]
    selector = {"authorization_id": data["authorization"]["id"], "generation": data["authorization"]["generation"]}
    completed = await svc.client.post(
        path + "/authorization/complete",
        json={
            **selector,
            "session_uri": "fixture://" + account_id,
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json() == {"return_url": "https://service.test/connections"}
    status = await svc.client.get(path + "/authorization")
    assert status.json()["status"] == "active", status.text
    return account_id, selector
