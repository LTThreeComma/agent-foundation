"""Real native Connector directory HTTP with Service-owned endpoint/response bounds."""

import httpx2
import pytest
from a13n_harness.providers.connector.builtins import COMPOSIO
from a13n_harness.providers.connector.contracts import ConnectorProviderError
from a13n_harness.providers.connector.http import ConnectorHttpClient
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http

from dev.fixtures.composio_client import composio_url as composio_url
from dev.fixtures.composio_client import configured_managed, enroll_managed

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("fault", [None, "cursor_cycle", "wrong_tool_version", "rate_limited", "oversized"])
async def test_native_catalogue_uses_bounded_versioned_http(composio_url, fault):
    policy = EndpointPolicy.from_operator_allowlist(private_cidrs=["127.0.0.0/8"], http_origins=[composio_url])
    async with httpx2.AsyncClient(trust_env=False) as control:
        await control.post(composio_url + "/fixture/control", json={fault: True} if fault else {})
        async with open_http(policy, timeout=5, max_bytes=262144) as client:
            http = ConnectorHttpClient(client, policy, response_max_bytes=262144, timeout_seconds=5)
            async with COMPOSIO.open({}, {"api_key": "fixture-project-key"}, http=http) as provider:
                if fault in {"cursor_cycle", "rate_limited", "oversized"}:
                    with pytest.raises((ConnectorProviderError, ServiceError)):
                        await provider.discover_connectors()
                else:
                    apps = await provider.discover_connectors()
                    assert len(apps) == 1 and apps[0].key == "github"
                    if fault:
                        with pytest.raises(ConnectorProviderError, match="incompatible_tool_version"):
                            await provider.tool_catalog("github").discover_tools(cursor=None)
                    else:
                        page = await provider.tool_catalog("github").discover_tools(cursor=None)
                        assert page.provider_version == "20260923_00"
                        assert [item.key for item in page.items] == ["GITHUB_CREATE_ISSUE"]
        requests = (await control.get(composio_url + "/fixture/state")).json()["requests"]
        assert all(item["method"] == "GET" for item in requests)
        if fault in {"rate_limited", "oversized"}:
            assert len(requests) == 1  # A read failure is surfaced, never hidden by automatic retries.
        if fault == "cursor_cycle":
            assert len(requests) == 2


async def test_saved_version_survives_current_metadata_advancement(composio_url):
    from a13n_harness.providers.connector.contracts import ConnectionBinding, SetupContext

    policy = EndpointPolicy.from_operator_allowlist(private_cidrs=["127.0.0.0/8"], http_origins=[composio_url])
    async with httpx2.AsyncClient(trust_env=False) as control:
        await control.post(
            composio_url + "/fixture/control", json={"current_version": "20260924_00", "sparse_pages": True}
        )
        async with open_http(policy, timeout=5, max_bytes=262144) as client:
            http = ConnectorHttpClient(client, policy, response_max_bytes=262144, timeout_seconds=5)
            async with COMPOSIO.open({}, {"api_key": "fixture-project-key"}, http=http) as provider:
                selected = provider.tool_catalog("github", provider_version="20260923_00")
                first = await selected.discover_tools(cursor=None)
                second = await selected.discover_tools(cursor=first.next_cursor)
                assert first.provider_version == second.provider_version == "20260923_00"
                assert [item.key for item in first.items + second.items] == ["GITHUB_CREATE_ISSUE", "GITHUB_GET_ISSUE"]
                started = await provider.start_setup(
                    setup={"auth_config_id": "ac_fixture", "toolkit_version": "20260923_00"},
                    context=SetupContext(
                        connector_key="github",
                        external_user_correlation="usr_fixture",
                        callback_url="https://console.example.test/verify",
                    ),
                )
                assert started.external_ref is not None
                await provider.complete_setup(
                    session_uri="fixture://" + started.external_ref,
                    context=SetupContext(connector_key="github", external_user_correlation="usr_fixture"),
                    expected_external_ref=started.external_ref,
                )
                connected = provider.connect(
                    ConnectionBinding(
                        external_ref=started.external_ref,
                        connector_key="github",
                        external_user_correlation="usr_fixture",
                    )
                )
                outcome = await connected.execute_tool(
                    tool_key="GITHUB_CREATE_ISSUE",
                    provider_version="20260923_00",
                    arguments={"title": "Pinned action"},
                    request_id="op_fixture",
                )
                assert outcome.kind == "succeeded"
                assert outcome.result == {
                    "successful": True,
                    "data": {"version": "20260923_00", "slug": "GITHUB_CREATE_ISSUE"},
                }
                requests = (await control.get(composio_url + "/fixture/state")).json()["requests"]
                assert all(
                    item["query"]["toolkit_versions[github]"] == "20260923_00"
                    for item in requests
                    if item["path"] == "/api/v3.1/tools"
                )
                assert all(
                    item["query"]["version"] == "20260923_00"
                    for item in requests
                    if item["path"].startswith("/api/v3.1/tools/GITHUB_")
                )
                current = await provider.tool_catalog("github").discover_tools(cursor=None)
                assert current.provider_version == "20260924_00"


@pytest.mark.parametrize(
    "version,fault",
    [
        ("latest", {}),
        ("20260923_00", {"unavailable_version": "20260923_00"}),
        ("20260923_00", {"wrong_tool_version": True, "sparse_pages": True}),
    ],
)
async def test_explicit_version_never_falls_back(composio_url, version, fault):
    policy = EndpointPolicy.from_operator_allowlist(private_cidrs=["127.0.0.0/8"], http_origins=[composio_url])
    async with httpx2.AsyncClient(trust_env=False) as control:
        await control.post(composio_url + "/fixture/control", json={"current_version": "20260924_00", **fault})
        async with open_http(policy, timeout=5, max_bytes=262144) as client:
            http = ConnectorHttpClient(client, policy, response_max_bytes=262144, timeout_seconds=5)
            async with COMPOSIO.open({}, {"api_key": "fixture-project-key"}, http=http) as provider:
                with pytest.raises(ConnectorProviderError):
                    await provider.tool_catalog("github", provider_version=version).discover_tools(cursor=None)
        requests = (await control.get(composio_url + "/fixture/state")).json()["requests"]
        assert not any("/toolkits/" in item["path"] for item in requests)
        if version == "latest":
            assert requests == []


async def test_public_managed_enrollment_completion_and_local_first_revoke(public_service, composio_url):
    svc = public_service
    path = await configured_managed(svc, composio_url)
    account_id, selector = await enroll_managed(svc, path)
    discovered = await svc.client.post(path + "/test")
    assert discovered.status_code == 200, discovered.text
    assert [tool["name"] for tool in discovered.json()["tools"]] == ["GITHUB_CREATE_ISSUE"]
    repeated = await svc.client.post(
        path + "/authorization/complete",
        json={
            **selector,
            "session_uri": "fixture://" + account_id,
        },
    )
    assert repeated.status_code == 409, repeated.text
    revoked = await svc.client.post(path + "/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked" and revoked.json()["failure"] is None
    async with httpx2.AsyncClient(trust_env=False) as peer:
        requests = (await peer.get(composio_url + "/fixture/state")).json()["requests"]
    writes = [item["path"] for item in requests if item["method"] == "POST"]
    assert writes == [
        "/api/v3.1/connected_accounts/link",
        "/api/v3.1/connected_accounts/complete_auth",
        "/api/v3.1/connected_accounts/" + account_id + "/revoke",
    ]


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
@pytest.mark.parametrize("public_service", [{"scan_seconds": 0.2}], indirect=True)
@pytest.mark.parametrize("kill_after_effect", [False, True])
async def test_real_worker_dispatches_pinned_native_action(
    public_service, composio_url, model_url, tmp_path, kill_after_effect
):
    import asyncio
    import base64
    import json
    import os
    import socket
    import sys

    svc = public_service
    path = await configured_managed(svc, composio_url)
    account_id, _ = await enroll_managed(svc, path)
    connection_id = path.rsplit("/", 1)[-1]
    agent_path = svc.workspace_path + "/agents/" + svc.agent_id
    agent = await svc.client.get(agent_path)
    revision = await svc.client.get(agent_path + "/revisions/" + agent.json()["default_revision_id"])
    revised = await svc.client.post(
        agent_path + "/revisions",
        headers={"If-Match": agent.headers["etag"]},
        json={
            "config": revision.json()["config"]
            | {"connections": [{"connection_id": connection_id, "tools": ["GITHUB_CREATE_ISSUE"]}]}
        },
    )
    assert revised.status_code == 201, revised.text
    settings = svc.app.state.settings
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    configuration = tmp_path / "worker.toml"
    configuration.write_text(f"""[server]
port = {port}
shutdown_timeout = 2
[database]
url = {json.dumps(settings.database.url.get_secret_value())}
auto_migrate = false
[redis]
url = {json.dumps(settings.redis.url.get_secret_value())}
[objects]
root = {json.dumps(str(settings.objects.root))}
[providers]
private_cidrs = ["127.0.0.0/8"]
http_origins = [{json.dumps(composio_url)}, {json.dumps(model_url.removesuffix("/v1"))}]
[encryption]
active_key_id = "test"
[encryption.keys]
test = {json.dumps(base64.b64encode(bytes(range(32))).decode())}
""")
    submitted = await svc.client.post(
        svc.workspace_path + "/threads",
        headers={"Idempotency-Key": "native-action"},
        json={
            "kind": "message",
            "agent_id": svc.agent_id,
            "payload": {"content": [{"type": "text", "text": "[service-composio]"}]},
        },
    )
    assert submitted.status_code == 201, submitted.text
    run_id = submitted.json()["run"]["id"]
    if kill_after_effect:
        async with httpx2.AsyncClient(trust_env=False) as peer:
            await peer.post(composio_url + "/fixture/control", json={"hold_action": True})
    with (tmp_path / "worker.log").open("wb") as log:

        async def launch():
            return await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "dev.fixtures.composio_service",
                "--config",
                str(configuration),
                "--endpoint",
                composio_url,
                env={name: value for name, value in os.environ.items() if not name.startswith("A13N_")},
                stdout=log,
                stderr=log,
            )

        process = await launch()
        try:
            if kill_after_effect:
                from a13n_service.runs.schemas import Checkpoint
                from a13n_service.runs.snapshots import object_key
                from pydantic_ai.messages import ToolCallPart, ToolReturnPart

                async with httpx2.AsyncClient(trust_env=False) as peer, asyncio.timeout(25):
                    while True:
                        counted = (await peer.get(composio_url + "/fixture/state")).json()
                        if any(item["kind"] == "action" for item in counted["effects"]):
                            break
                        assert process.returncode is None, (tmp_path / "worker.log").read_text()
                        await asyncio.sleep(0.025)
                    saved = await svc.app.state.objects.read(
                        object_key(svc.initialized.organization_id, run_id, "state")
                    )
                    assert saved is not None
                    checkpoint = Checkpoint.model_validate_json(saved.content)
                    calls = [
                        part
                        for message in checkpoint.state.message_history
                        for part in message.parts
                        if isinstance(part, ToolCallPart)
                    ]
                    assert len(calls) == 1
                    assert not any(
                        isinstance(part, ToolReturnPart)
                        for message in checkpoint.state.message_history
                        for part in message.parts
                    )
                    process.kill()
                    await process.wait()
                    assert process.returncode == -9
                    await peer.post(composio_url + "/fixture/release-action")
                process = await launch()
            async with asyncio.timeout(60):
                while True:
                    result = await svc.client.get(svc.workspace_path + f"/runs/{run_id}/items")
                    if result.json()["status"] in {"completed", "failed"}:
                        break
                    assert process.returncode is None, (tmp_path / "worker.log").read_text()
                    await asyncio.sleep(0.05)
            assert result.json()["status"] == "completed", result.text
            if kill_after_effect:
                saved = await svc.app.state.objects.read(object_key(svc.initialized.organization_id, run_id, "state"))
                assert saved is not None
                checkpoint = Checkpoint.model_validate_json(saved.content)
                returned = [
                    part
                    for message in checkpoint.state.message_history
                    for part in message.parts
                    if isinstance(part, ToolReturnPart)
                ]
                assert len(returned) == 1 and returned[0].outcome == "failed"
                assert "may have partially or fully completed" in str(returned[0].content)
            else:
                assert "20260923_00" in result.text, result.text
            assert "fixture-project-key" not in result.text and account_id not in result.text
            async with httpx2.AsyncClient(trust_env=False) as peer:
                effects = (await peer.get(composio_url + "/fixture/state")).json()["effects"]
            assert [item for item in effects if item["kind"] == "action"] == [
                {"kind": "action", "account_id": account_id, "version": "20260923_00"}
            ]
        finally:
            if process.returncode is None:
                process.terminate()
            await process.wait()


@pytest.mark.parametrize(
    "fault",
    [
        "drop_setup",
        "drop_complete",
        "drop_inspect_after_complete",
        "wrong_config_after_complete",
        "wrong_completion_account",
        "reject_complete",
        "wrong_owner",
        "wrong_auth_config",
    ],
)
async def test_managed_uncertainty_and_binding_never_replay(public_service, composio_url, fault):
    svc = public_service
    path = await configured_managed(svc, composio_url)
    async with httpx2.AsyncClient(base_url=composio_url, trust_env=False) as peer:
        if fault == "drop_setup":
            await peer.post("/fixture/control", json={fault: True})
        started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
        if fault == "drop_setup":
            assert started.status_code == 503, started.text
        else:
            assert started.status_code == 200, started.text
            value = started.json()
            account_id = value["redirect_url"].rsplit("/", 1)[-1]
            body = {
                "authorization_id": value["authorization"]["id"],
                "generation": value["authorization"]["generation"],
                "session_uri": "fixture://" + account_id,
            }
            await peer.post("/fixture/control", json={fault: True})
            completed = await svc.client.post(path + "/authorization/complete", json=body)
            assert completed.status_code in {409, 503}, completed.text
        status = (await svc.client.get(path + "/authorization")).json()
        if fault in {"drop_complete", "drop_inspect_after_complete"}:
            assert status["status"] == "pending" and status["failure"] == {"reason": "unknown_after_dispatch"}
            await peer.post("/fixture/control", json={})
            # A known claimed completion can inspect its exact saved account, never resend this new URI.
            reconciled = await svc.client.post(
                path + "/authorization/complete", json={**body, "session_uri": "fixture://wrong"}
            )
            assert reconciled.status_code == 200, reconciled.text
            assert (await svc.client.get(path + "/authorization")).json()["status"] == "active"
        else:
            assert status["status"] == "reauthorization_required", status
            if fault != "drop_setup":
                refused = await svc.client.post(path + "/authorization/complete", json=body)
                assert refused.status_code == 409, refused.text
        requests = (await peer.get("/fixture/state")).json()["requests"]
        posts = [item["path"] for item in requests if item["method"] == "POST"]
        assert posts.count("/api/v3.1/connected_accounts/link") == 1
        assert posts.count("/api/v3.1/connected_accounts/complete_auth") == (
            1
            if fault
            in {
                "drop_complete",
                "drop_inspect_after_complete",
                "reject_complete",
                "wrong_config_after_complete",
                "wrong_completion_account",
            }
            else 0
        )


@pytest.mark.parametrize("fault", ["drop_revoke", "unsupported_revoke", "disabled"])
async def test_revoke_keeps_local_access_disabled_without_remote_retries(public_service, composio_url, fault):
    from a13n_service.infra.db import short_session
    from a13n_service.resources.connections.tables import ConnectionAuthorizationRow

    svc = public_service
    path = await configured_managed(svc, composio_url)
    _, selector = await enroll_managed(svc, path)
    async with httpx2.AsyncClient(base_url=composio_url, trust_env=False) as peer:
        await peer.post("/fixture/control", json={fault: True})
        if fault == "disabled":
            current = await svc.client.get(path)
            changed = await svc.client.patch(
                path, headers={"If-Match": current.headers["etag"]}, json={"enabled": False}
            )
            assert changed.status_code == 200, changed.text
        revoked = await svc.client.post(path + "/revoke")
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["status"] == "revoked"
        if fault != "disabled":
            assert revoked.json()["failure"] == {
                "reason": "unknown_after_dispatch" if fault == "drop_revoke" else "remote_revoke_unsupported"
            }
        assert (await svc.client.post(path + "/test")).status_code != 200
        assert (await svc.client.post(path + "/revoke")).status_code == 200
        async with short_session(svc.app.state.storage) as session:
            row = await session.get(ConnectionAuthorizationRow, selector["authorization_id"])
            assert row.credential is None and row.status == "revoked"
        requests = (await peer.get("/fixture/state")).json()["requests"]
        assert sum(item["path"].endswith("/revoke") for item in requests) == (0 if fault == "disabled" else 1)


@pytest.mark.parametrize(
    "case", ["missing_cookie", "new_session", "generation", "no_verifier", "config_change", "expired"]
)
async def test_completion_refuses_stale_or_unbound_browser_before_redemption(public_service, composio_url, case):
    from datetime import UTC, datetime, timedelta

    from a13n_service.infra.db import transaction
    from a13n_service.resources.connections.tables import ConnectionAuthorizationRow

    svc = public_service
    path = await configured_managed(svc, composio_url)
    started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
    assert started.status_code == 200, started.text
    value = started.json()
    body = {
        "authorization_id": value["authorization"]["id"],
        "generation": value["authorization"]["generation"],
        "session_uri": "fixture://" + value["redirect_url"].rsplit("/", 1)[-1],
    }
    if case == "missing_cookie":
        for cookie in list(svc.client.cookies.jar):
            if cookie.name.startswith("__Host-a13n_managed_"):
                svc.client.cookies.delete(cookie.name, domain=cookie.domain, path=cookie.path)
    elif case == "new_session":
        logged = await svc.client.post(
            "/api/v1/auth/login", json={"email": "user@example.com", "password": "test-password"}
        )
        svc.client.headers["x-csrf-token"] = logged.json()["csrf_token"]
    elif case == "generation":
        body["generation"] += 1
    elif case == "no_verifier":
        body.pop("session_uri")
    elif case == "config_change":
        current = await svc.client.get(path)
        changed = await svc.client.patch(
            path, headers={"If-Match": current.headers["etag"]}, json={"credential": {"api_key": "rotated-project-key"}}
        )
        assert changed.status_code == 200, changed.text
    elif case == "expired":
        async with transaction(svc.app.state.storage) as session:
            row = await session.get(ConnectionAuthorizationRow, body["authorization_id"])
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    result = await svc.client.post(path + "/authorization/complete", json=body)
    assert result.status_code in {400, 403, 409, 422}, result.text
    async with httpx2.AsyncClient(base_url=composio_url, trust_env=False) as peer:
        requests = (await peer.get("/fixture/state")).json()["requests"]
    assert not any(item["path"].endswith("/complete_auth") for item in requests)


async def test_concurrent_completion_and_parallel_connections_keep_exact_accounts(public_service, composio_url):
    import asyncio

    svc = public_service
    first = await configured_managed(svc, composio_url)
    second = await configured_managed(svc, composio_url)
    bodies = []
    for path in (first, second):
        started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
        value = started.json()
        bodies.append(
            {
                "authorization_id": value["authorization"]["id"],
                "generation": value["authorization"]["generation"],
                "session_uri": "fixture://" + value["redirect_url"].rsplit("/", 1)[-1],
            }
        )
    # Two completions in one tab/cookie jar still cannot spend a generation twice.
    results = await asyncio.gather(
        *(svc.client.post(first + "/authorization/complete", json=bodies[0]) for _ in range(2))
    )
    assert sorted(result.status_code for result in results) in ([200, 200], [200, 409])
    assert (await svc.client.post(second + "/authorization/complete", json=bodies[1])).status_code == 200
    async with httpx2.AsyncClient(base_url=composio_url, trust_env=False) as peer:
        effects = (await peer.get("/fixture/state")).json()["effects"]
    assert len({item["account_id"] for item in effects if item["kind"] == "complete"}) == 2
    assert sum(item["kind"] == "complete" for item in effects) == 2


async def test_public_saved_catalogue_pins_version_and_excludes_project_configuration_creation(
    public_service, composio_url
):
    svc = public_service
    path = await configured_managed(svc, composio_url)
    async with httpx2.AsyncClient(base_url=composio_url, trust_env=False) as peer:
        await peer.post("/fixture/control", json={"current_version": "20260924_00", "sparse_pages": True})
        catalogue = await svc.client.post(
            svc.workspace_path + "/connection-catalog/composio",
            json={"connection_id": path.rsplit("/", 1)[-1], "app": "github", "toolkit_version": "20260923_00"},
        )
        assert catalogue.status_code == 200, catalogue.text
        assert catalogue.json()["toolkit_version"] == "20260923_00"
        assert "create:" not in catalogue.text and "fixture-project-key" not in catalogue.text
        assert catalogue.json()["apps"][0]["setup_schema"]["properties"]["toolkit_version"]["const"] == "20260923_00"
        current = await svc.client.get(path)
        rejected = await svc.client.patch(
            path,
            headers={"If-Match": current.headers["etag"]},
            json={"config": {**current.json()["config"], "auth_config_id": "create:OAUTH2"}},
        )
        assert rejected.status_code == 400, rejected.text
        await enroll_managed(svc, path)
        assert (await svc.client.post(path + "/test")).status_code == 200
        requests = (await peer.get("/fixture/state")).json()["requests"]
        assert all(
            item["query"]["toolkit_versions[github]"] == "20260923_00"
            for item in requests
            if item["path"] == "/api/v3.1/tools"
        )
        assert not any(item["method"] == "POST" and "auth_configs" in item["path"] for item in requests)


@pytest.mark.parametrize("scheme", ["OAUTH2", "API_KEY"])
async def test_hosted_confirmation_requires_the_correct_method(public_service, composio_url, scheme):
    svc = public_service
    async with httpx2.AsyncClient(base_url=composio_url, trust_env=False) as peer:
        await peer.post(
            "/fixture/control", json={"scheme": scheme, "verifier_url": "https://service.test/managed/verify"}
        )
        path = await configured_managed(svc, composio_url)
        started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
        assert started.status_code == 200, started.text
        value = started.json()
        account = value["redirect_url"].rsplit("/", 1)[-1]
        body = {"authorization_id": value["authorization"]["id"], "generation": value["authorization"]["generation"]}
        if scheme == "OAUTH2":
            await peer.post("/fixture/activate/" + account)
        else:
            consent = await peer.post("/connect/" + account)
            assert consent.status_code == 303
            assert consent.headers["location"] == (
                "https://service.test/managed/verify?status=success&connected_account_id=" + account
            )
            assert (await svc.client.get(path + "/authorization")).json()["status"] == "pending"
        completed = await svc.client.post(path + "/authorization/complete", json=body)
        assert completed.status_code == (400 if scheme == "OAUTH2" else 200), completed.text
        status = (await svc.client.get(path + "/authorization")).json()
        assert status["status"] == ("pending" if scheme == "OAUTH2" else "active")
        requests = (await peer.get("/fixture/state")).json()["requests"]
        assert not any(item["path"].endswith("/complete_auth") for item in requests)


@pytest.mark.parametrize("change", ["revoke", "reauthorize", "credential"])
async def test_late_completion_cannot_publish_after_identity_change(public_service, composio_url, change):
    import asyncio

    svc = public_service
    path = await configured_managed(svc, composio_url)
    started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
    value = started.json()
    body = {
        "authorization_id": value["authorization"]["id"],
        "generation": value["authorization"]["generation"],
        "session_uri": "fixture://" + value["redirect_url"].rsplit("/", 1)[-1],
    }
    async with httpx2.AsyncClient(base_url=composio_url, trust_env=False) as peer:
        await peer.post("/fixture/control", json={"hold_complete": True})
        operation = asyncio.create_task(svc.client.post(path + "/authorization/complete", json=body))
        try:
            async with asyncio.timeout(4):
                while not any(
                    item["kind"] == "complete" for item in (await peer.get("/fixture/state")).json()["effects"]
                ):
                    await asyncio.sleep(0.01)
            if change == "revoke":
                assert (await svc.client.post(path + "/revoke")).status_code == 200
            elif change == "reauthorize":
                assert (
                    await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
                ).status_code == 200
            else:
                current = await svc.client.get(path)
                assert (
                    await svc.client.patch(
                        path,
                        headers={"If-Match": current.headers["etag"]},
                        json={"credential": {"api_key": "rotated-project-key"}},
                    )
                ).status_code == 200
        finally:
            await peer.post("/fixture/release-completion")
            result = await operation
        assert result.status_code != 200, result.text
        status = (await svc.client.get(path + "/authorization")).json()
        assert status["status"] != "active"
        assert status["generation"] > body["generation"]
        requests = (await peer.get("/fixture/state")).json()["requests"]
        assert sum(item["path"].endswith("/complete_auth") for item in requests) == 1
