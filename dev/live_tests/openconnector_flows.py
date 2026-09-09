"""Bounded OOMOL upstream probes; third-party secrets never enter Foundation."""

import logging
from uuid import uuid4

import httpx2
import pytest
from jsonschema import Draft202012Validator

logger = logging.getLogger(__name__)
ORIGIN = "https://connector.oomol.com"
REPRESENTATIVES = {
    "slack": "oauth2",
    "e2b": "api_key",
    "feishu_app_bot": "custom_credential",
    "hackernews": "no_auth",
    "aliyun_sts": "federated",
}


def require(condition, message):
    """Avoid pytest introspection printing upstream response bodies or credentials."""
    if not condition:
        pytest.fail(message, pytrace=False)


class HostedFlows:
    def __init__(self, http, settings):
        self.http = http
        self.settings = settings

    async def request(self, method, path, *, body=None, project=False, alias=None):
        key = self.settings.project_api_key if project else self.settings.catalog_api_key
        headers = {"Authorization": "Bearer " + key.get_secret_value()}
        if alias is not None:
            headers["x-oo-connector-alias"] = alias
        try:
            response = await self.http.request(method, path, headers=headers, json=body)
        except httpx2.HTTPError:
            pytest.fail(f"OOMOL {method} {path}: transport failure; writes are not retried", pytrace=False)
        require(response.is_success, f"OOMOL {method} {path}: HTTP {response.status_code}")
        try:
            envelope = response.json()
        except ValueError:
            pytest.fail(f"OOMOL {path}: invalid JSON", pytrace=False)
        require(isinstance(envelope, dict), f"OOMOL {path}: invalid envelope")
        require(envelope.get("success") is True and "data" in envelope, f"OOMOL {path}: unsuccessful envelope")
        return envelope

    async def action(self, action_id, arguments, *, binding=None, alias=None):
        metadata = (await self.request("GET", f"/v1/actions/{action_id}"))["data"]
        require(metadata.get("id") == action_id, "Catalog returned a different action")
        require(metadata.get("operationType") == "read", "Live action is no longer declared read-only")
        require(Draft202012Validator(metadata["inputSchema"]).is_valid(arguments), "Action input schema changed")
        body = {"input": arguments, **(binding or {})}
        prefix = "/v1/saas" if binding else "/v1"
        envelope = await self.request(
            "POST", f"{prefix}/actions/{action_id}", body=body, project=bool(binding), alias=alias
        )
        output = envelope["data"]
        require(Draft202012Validator(metadata["outputSchema"]).is_valid(output), "Action output schema mismatch")
        require(bool(envelope.get("meta", {}).get("executionId")), "Missing upstream execution receipt")
        logger.info(
            "OOMOL read-only action succeeded: action=%s execution=%s", action_id, envelope["meta"]["executionId"]
        )
        return output

    async def connect(self, service, route, credential):
        user_id = "live_" + uuid4().hex
        alias = "live_" + uuid4().hex
        logger.info("OOMOL synchronous setup started: service=%s user=%s alias=%s", service, user_id, alias)
        try:
            account = (
                await self.request(
                    "POST",
                    f"/v1/saas/connected-accounts/{route}",
                    body={"userId": user_id, "service": service, "alias": alias, **credential},
                    project=True,
                )
            )["data"]
            require(account.get("service") == service, "Connected account service mismatch")
            require(account.get("externalUserId") == user_id, "Connected account owner mismatch")
            require(account.get("alias") == alias, "Connected account alias mismatch")
            require(account.get("status") == "active" and account.get("available") is True, "Account is not usable")
            account_id = account.get("connectedAccountId") or account.get("id")
            require(isinstance(account_id, str) and bool(account_id), "Missing connected account ID")
            require(not account.get("authorizationUrl"), "Synchronous connection unexpectedly requires OAuth")
            logger.info("OOMOL synchronous account ready: service=%s account=%s", service, account_id)
            return {"userId": user_id, "service": service, "connectedAccountId": account_id}
        finally:
            # Project SDK has no disconnect operation, including after a lost create response.
            logger.info(
                "Remove any remote test account in OOMOL Project Connected accounts: service=%s alias=%s",
                service,
                alias,
            )
