"""Offline checks for secret-safe failures and exact upstream routing."""

import httpx2
import pytest

from .openconnector_flows import ORIGIN, HostedFlows
from .provider_config import OpenConnectorSettings

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("project", [False, True])
async def test_upstream_uses_only_selected_authority_and_exact_alias(project):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx2.Response(200, json={"success": True, "data": {}})

    settings = OpenConnectorSettings(provider="openconnector", project_api_key="project", catalog_api_key="catalog")
    async with httpx2.AsyncClient(base_url=ORIGIN, transport=httpx2.MockTransport(handler)) as http:
        await HostedFlows(http, settings).request("GET", "/v1/apps", project=project, alias="test-account")
    assert len(calls) == 1
    assert calls[0].headers["authorization"] == "Bearer " + ("project" if project else "catalog")
    assert calls[0].headers["x-oo-connector-alias"] == "test-account"


@pytest.mark.parametrize("failure", ["http", "transport", "envelope"])
async def test_upstream_write_failure_never_retries_or_echoes_credentials(failure):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if failure == "transport":
            raise httpx2.ReadError("private-test-secret", request=request)
        return httpx2.Response(
            503 if failure == "http" else 200,
            json={"success": False, "message": "private-test-secret"},
        )

    settings = OpenConnectorSettings(provider="openconnector", project_api_key="project", catalog_api_key="catalog")
    async with httpx2.AsyncClient(base_url=ORIGIN, transport=httpx2.MockTransport(handler)) as http:
        with pytest.raises(pytest.fail.Exception) as caught:
            await HostedFlows(http, settings).request(
                "POST", "/v1/saas/connected-accounts/api-key", body={"apiKey": "private-test-secret"}, project=True
            )
    assert calls == 1
    assert "private-test-secret" not in str(caught.value)
