"""OOMOL connection partitions, separate from Foundation's OAuth-only adapter."""

import logging
from datetime import UTC, datetime

import httpx2
import pytest

from .openconnector_flows import ORIGIN, REPRESENTATIVES, HostedFlows, require
from .provider_config import OpenConnectorSettings, load_provider_settings

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


@pytest.fixture
async def oomol(request):
    if not request.config.getoption("--live-openconnector"):
        pytest.skip("Opt in with --live-openconnector; upstream accounts can be created")
    settings = load_provider_settings().connector
    if not isinstance(settings, OpenConnectorSettings):
        pytest.fail("--live-openconnector requires an openconnector Provider configuration")
    async with httpx2.AsyncClient(base_url=ORIGIN, timeout=30, follow_redirects=False) as http:
        yield HostedFlows(http, settings)


async def test_oomol_live_authentication_partitions(oomol):
    providers = (await oomol.request("GET", "/v1/providers"))["data"]
    by_service = {item["service"]: set(item["authTypes"]) for item in providers}
    observed = set().union(*by_service.values())
    assert observed == set(REPRESENTATIVES.values()), "New OOMOL auth flow needs a representative live journey"
    for service, auth_type in REPRESENTATIVES.items():
        assert auth_type in by_service[service]
        logger.info("OOMOL authentication representative: service=%s auth=%s", service, auth_type)


async def test_oomol_e2b_api_key_synchronous_connection(oomol):
    key = oomol.settings.e2b_api_key
    if key is None:
        pytest.skip("Set connector.e2b_api_key and create an E2B API Key Provider config in the OOMOL test Project")
    binding = await oomol.connect("e2b", "api-key", {"apiKey": key.get_secret_value()})
    output = await oomol.action("e2b.list_sandboxes", {"limit": 1}, binding=binding)
    require(isinstance(output.get("sandboxes"), list), "E2B did not return a sandbox list")
    logger.info("E2B API key validated and read executed; no sandbox was created")


async def test_oomol_feishu_custom_credential_synchronous_connection(oomol):
    app = oomol.settings.feishu_app
    if app is None:
        pytest.skip("Set connector.feishu_app and create a Feishu App Bot custom-credential Provider config")
    binding = await oomol.connect(
        "feishu_app_bot",
        "custom-credential",
        {"values": {"appId": app.app_id.get_secret_value(), "appSecret": app.app_secret.get_secret_value()}},
    )
    output = await oomol.action("feishu_app_bot.list_chats", {"pageSize": 1}, binding=binding)
    require(output.get("code") == 0, "Feishu returned an application error")
    require(isinstance(output.get("data", {}).get("items"), list), "Feishu did not return a chat list")
    logger.info("Feishu app credentials validated and read executed; no message was sent")


async def test_oomol_hackernews_no_auth_without_account(oomol):
    # Personal hosted gateway API; no Project config, third-party credential, or alias.
    output = await oomol.action("hackernews.get_top_stories", {})
    require(bool(output.get("story_ids")), "Hacker News returned no stories")
    require(output.get("count") == len(output["story_ids"]), "Hacker News count mismatch")
    logger.info("Hacker News public action succeeded without a third-party account")


async def test_oomol_aliyun_federated_console_connection(oomol):
    alias = oomol.settings.federated_connection_name
    if alias is None:
        pytest.skip("Connect a dedicated aliyun_sts OIDC test role in OOMOL Console; set federated_connection_name")
    # Federation setup is Console-owned, absent from the public Project connect API.
    apps = (await oomol.request("GET", "/v1/apps"))["data"]
    matches = [app for app in apps if app.get("service") == "aliyun_sts" and app.get("alias") == alias]
    require(len(matches) == 1, "Expected exactly one explicitly named Alibaba Cloud STS connection")
    require(matches[0].get("authType") == "federated", "Selected connection does not use federation")
    output = await oomol.action("aliyun_sts.get_federated_credentials", {"durationSeconds": 900}, alias=alias)
    require(output.get("mode") == "StsToken", "Expected temporary STS credentials")
    require(
        all(bool(output.get(key)) for key in ("access_key_id", "access_key_secret", "sts_token")),
        "Missing STS credential",
    )
    expires = datetime.fromisoformat(output["expiration"].replace("Z", "+00:00"))
    require(expires > datetime.now(UTC), "STS credentials already expired")
    # Never write or log the returned temporary cloud credentials.
    logger.info("Alibaba Cloud OIDC exchange succeeded; temporary credentials expire at %s", expires.isoformat())
