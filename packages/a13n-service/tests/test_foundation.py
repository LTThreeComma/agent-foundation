"""The assembled control process serves authenticated requests over the template schema."""

import pytest
from a13n_service.app import build_app
from a13n_service.settings import Settings

pytestmark = pytest.mark.anyio


async def test_control_process_is_ready_and_authenticates(service) -> None:  # type: ignore[no-untyped-def]
    assert (await service.client.get("/healthz")).json()["role"] == "control"
    ready = await service.client.get("/readyz")
    assert ready.status_code == 200, ready.text
    me = await service.client.get("/api/v1/users/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "admin@example.com"


async def test_losing_redis_degrades_readiness_without_failing_it(service, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def unreachable() -> None:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(service.runtime.redis, "ping", unreachable)
    ready = await service.client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "role": "control", "degraded": ["redis"]}


async def test_page_limits_are_bounded(service) -> None:  # type: ignore[no-untyped-def]
    assert (await service.client.get("/api/v1/workspaces", params={"limit": 100})).status_code == 200
    refused = await service.client.get("/api/v1/workspaces", params={"limit": 101})
    assert refused.status_code == 400 and refused.json()["error"]["details"]["fields"][0]["field"] == "query.limit"


def test_contract_describes_failures_and_credentials_as_sent() -> None:
    document = build_app(settings=Settings()).openapi()
    error = {"$ref": "#/components/responses/Error"}
    assert {"HTTPValidationError", "ValidationError"}.isdisjoint(document["components"]["schemas"])
    assert document["components"]["responses"]["Error"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorEnvelope"
    }
    for path, operations in document["paths"].items():
        for operation in operations.values():
            assert "422" not in operation["responses"]
            if path.startswith("/api/"):
                assert operation["responses"]["default"] == error
    login = document["paths"]["/api/v1/auth/login"]["post"]
    assert login["responses"]["400"] == error and "security" not in login
    profile = document["paths"]["/api/v1/users/me"]
    assert profile["get"]["security"] == [{"apiKey": []}, {"loginSession": []}]
    assert profile["patch"]["security"] == [{"apiKey": []}, {"loginSession": [], "csrf": []}]
    schemes = document["components"]["securitySchemes"]
    assert (schemes["loginSession"]["name"], schemes["csrf"]["name"]) == ("__Host-a13n_session", "X-CSRF-Token")
