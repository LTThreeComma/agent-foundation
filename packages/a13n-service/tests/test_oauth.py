"""Principal OAuth flows against a real counted, rotating HTTP authorization server."""

import base64
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.infra.crypto import KeyRing
from a13n_service.settings import OAuth
from pydantic import SecretStr

from dev.fixtures.database import db_http_probe as db_http_probe
from dev.fixtures.process import fixture_process

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db_http_probe")]


@pytest.fixture
def oauth_url(tmp_path):
    with fixture_process("dev.fixtures.oauth", arguments=("--database", str(tmp_path / "oauth.sqlite"))) as url:
        yield url


async def configured(svc, oauth_url, model_url, *, method="none"):
    app = svc.app
    app.state.key_ring = KeyRing(
        active_key_id="test", keys={"test": SecretStr(base64.b64encode(bytes(range(32))).decode())}
    )
    app.state.endpoint_policy = EndpointPolicy.from_operator_allowlist(
        private_cidrs=["127.0.0.0/8"], http_origins=[oauth_url, model_url.removesuffix("/v1")]
    )
    app.state.settings = app.state.settings.model_copy(
        update={
            "oauth": OAuth(
                callback_url="https://service.test/api/v1/oauth/callback",
                return_urls=("https://service.test/connections",),
                operation_seconds=3,
            )
        }
    )
    response = await svc.client.post(
        svc.workspace_path + "/connections",
        json={
            "type": "mcp",
            "name": "Private tools",
            "auth": "oauth",
            "credential": None if method == "none" else {"client_secret": "fixture-client-secret"},
            "config": {
                "url": oauth_url + "/tools/none/mcp",
                "tools": ["increment"],
                "oauth": {
                    "issuer": oauth_url,
                    "client_id": "fixture-client",
                    "scopes": ["tools"],
                    "token_endpoint_auth_method": method,
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    return svc.workspace_path + "/connections/" + response.json()["id"]


async def authorize(svc, peer, path, *, principal="alice"):
    started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
    assert started.status_code == 200, started.text
    params = {key: values[0] for key, values in parse_qs(urlparse(started.json()["redirect_url"]).query).items()}
    response = await peer.get("/consent", params=params | {"principal": principal, "decision": "allow"})
    callback = response.headers["location"]
    completed = await svc.client.get(callback)
    assert completed.status_code == 303, completed.text
    assert completed.headers["location"] == "https://service.test/connections"
    assert "state=" not in completed.headers["location"]
    return callback


@pytest.mark.parametrize("method", ["none", "client_secret_basic", "client_secret_post"])
async def test_public_oauth_authorize_discover_revoke(public_service, oauth_url, model_url, method):
    svc = public_service
    path = await configured(svc, oauth_url, model_url, method=method)
    async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
        callback = await authorize(svc, peer, path)
        state = await svc.client.get(path + "/authorization")
        assert state.json()["status"] == "active", state.text
        tested = await svc.client.post(path + "/test")
        assert tested.status_code == 200, tested.text
        counted = (await peer.get("/fixture/oauth-state")).json()
        assert len(counted["requests"]) == 1 and counted["requests"][0]["consumed"] == 1
        assert counted["access"] and all(call["principal"] == "alice" for call in counted["access"])
        assert (await svc.client.get(callback)).status_code == 400
        revoked = await svc.client.post(path + "/revoke")
        assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
        assert (await svc.client.post(path + "/test")).status_code == 422
        assert len((await peer.get("/fixture/oauth-state")).json()["requests"]) == 1


@pytest.mark.parametrize(
    "outcome",
    ["success", "lost_response", "revoke", "reauthorize", "endpoint", "client", "issuer", "scopes", "credential"],
)
async def test_concurrent_rotating_refresh_is_claimed_and_fenced(
    public_service, oauth_url, model_url, outcome, tmp_path
):
    import asyncio

    from a13n_service.infra.db import short_session
    from a13n_service.resources.connections.tables import ConnectionAuthorizationRow
    from sqlalchemy import select

    svc = public_service
    path = await configured(
        svc, oauth_url, model_url, method="client_secret_basic" if outcome == "credential" else "none"
    )
    async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
        await peer.post("/fixture/oauth-faults", json={"expires_in": 1})
        await authorize(svc, peer, path)
        initial = (await svc.client.get(path + "/authorization")).json()
        await asyncio.sleep(1.05)  # Expiry itself is the boundary being tested.
        faults = {"hold": "refresh_token"}
        if outcome == "lost_response":
            faults["lost_response"] = "refresh_token"
        await peer.post("/fixture/oauth-faults", json=faults)
        first = asyncio.create_task(svc.client.post(path + "/test"))
        second = asyncio.create_task(svc.client.post(path + "/test"))
        try:
            async with asyncio.timeout(3):
                while True:
                    counted = (await peer.get("/fixture/oauth-state")).json()
                    if len(counted["requests"]) == 2:
                        break
                    await asyncio.sleep(0.025)
            assert counted["requests"][1]["kind"] == "refresh_token"
            assert counted["requests"][1]["consumed"] == 1 and counted["requests"][1]["returned"] == 0
            import json

            from dev.fixtures.database import transactions

            observed = await transactions(svc.app.state.storage)
            (tmp_path / "token-barrier-transactions.json").write_text(json.dumps(observed, default=str, indent=2))
            async with short_session(svc.app.state.storage) as session:
                row = await session.scalar(
                    select(ConnectionAuthorizationRow).where(ConnectionAuthorizationRow.id == initial["id"])
                )
                assert row.generation == initial["generation"] + 1 and row.operation_kind == "refresh"
                operation, generation = row.operation_id, row.generation
            if outcome == "revoke":
                assert (await svc.client.post(path + "/revoke")).status_code == 200
            elif outcome == "reauthorize":
                started = await svc.client.post(
                    path + "/authorize", json={"return_url": "https://service.test/connections"}
                )
                assert started.status_code == 200, started.text
            elif outcome in {"endpoint", "client", "issuer", "scopes", "credential"}:
                resource = await svc.client.get(path)
                config = resource.json()["config"]
                if outcome == "endpoint":
                    config["url"] += "/changed"
                elif outcome == "client":
                    config["oauth"]["client_id"] = "different-client"
                elif outcome == "issuer":
                    config["oauth"]["issuer"] += "/other"
                elif outcome == "scopes":
                    config["oauth"]["scopes"] = ["different-scope"]
                changes = {"config": config}
                if outcome == "credential":
                    changes["credential"] = {"client_secret": "replacement-client-secret"}
                changed = await svc.client.patch(path, headers={"If-Match": resource.headers["etag"]}, json=changes)
                assert changed.status_code == 200, changed.text
            await peer.post("/fixture/oauth-release")
            responses = await asyncio.gather(first, second)
            if outcome == "success":
                assert [response.status_code for response in responses] == [200, 200], [
                    response.text for response in responses
                ]
            else:
                assert all(response.status_code >= 400 for response in responses)
            final = (await svc.client.get(path + "/authorization")).json()
            if outcome == "success":
                assert final["status"] == "active" and final["generation"] == generation
            elif outcome == "revoke":
                assert final["status"] == "revoked" and final["generation"] > generation
            elif outcome == "reauthorize":
                assert final["status"] == "pending" and final["generation"] > generation
            else:
                assert final["status"] == "reauthorization_required"
            if outcome == "lost_response":
                async with short_session(svc.app.state.storage) as session:
                    row = await session.get(ConnectionAuthorizationRow, initial["id"])
                    assert row.operation_id == operation and row.generation == generation and row.credential is None
                assert (await svc.client.post(path + "/test")).status_code == 422
            counted = (await peer.get("/fixture/oauth-state")).json()
            assert len(counted["requests"]) == 2
            assert counted["families"][0]["generation"] == 2 and counted["families"][0]["revoked"] == 0
            import json

            (tmp_path / "g054-refresh.json").write_text(
                json.dumps(
                    {
                        "outcome": outcome,
                        "claimed_operation": operation,
                        "claimed_generation": generation,
                        "final_authorization": final,
                        "peer": counted,
                        "caller_statuses": [response.status_code for response in responses],
                    },
                    indent=2,
                )
            )
        finally:
            for task in (first, second):
                if not task.done():
                    task.cancel()
            await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.parametrize("model_url", ["live"], indirect=True)
@pytest.mark.parametrize("kill_refresh", [False, True])
async def test_production_worker_oauth_and_crashed_refresh_owner(
    public_service, oauth_url, model_url, tmp_path, kill_refresh
):
    import asyncio
    import json
    import os
    import socket
    import sys

    from a13n_service.infra.db import short_session
    from a13n_service.resources.connections.tables import ConnectionAuthorizationRow
    from sqlalchemy import select

    svc = public_service
    path = await configured(svc, oauth_url, model_url)
    connection_id = path.rsplit("/", 1)[1]
    agent_path = svc.workspace_path + "/agents/" + svc.agent_id
    agent = await svc.client.get(agent_path)
    revision = await svc.client.get(agent_path + "/revisions/" + agent.json()["default_revision_id"])
    revised = await svc.client.post(
        agent_path + "/revisions",
        headers={"If-Match": agent.headers["etag"]},
        json={
            "config": revision.json()["config"]
            | {"connections": [{"connection_id": connection_id, "tools": ["increment"]}]}
        },
    )
    assert revised.status_code == 201, revised.text
    async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
        if kill_refresh:
            await peer.post("/fixture/oauth-faults", json={"expires_in": 1})
        await authorize(svc, peer, path)
        if kill_refresh:
            await asyncio.sleep(1.05)
            await peer.post("/fixture/oauth-faults", json={"hold": "refresh_token"})
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
http_origins = [{json.dumps(oauth_url)}, {json.dumps(model_url.removesuffix("/v1"))}]
[encryption]
active_key_id = "test"
[encryption.keys]
test = {json.dumps(base64.b64encode(bytes(range(32))).decode())}
[worker]
slots = 1
lease_seconds = 3
scan_seconds = 0.1
[oauth]
operation_seconds = 3
""")
        configuration.chmod(0o600)
        submitted = await svc.client.post(
            svc.workspace_path + "/threads",
            headers={"Idempotency-Key": "oauth-worker"},
            json={
                "kind": "message",
                "agent_id": svc.agent_id,
                "payload": {"content": [{"type": "text", "text": "[service-mcp:increment]"}]},
            },
        )
        assert submitted.status_code == 201, submitted.text
        run_id = submitted.json()["run"]["id"]
        with (tmp_path / "worker.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "from a13n_service.cli import main; main()",
                "--config",
                str(configuration),
                "run",
                "--role",
                "worker",
                env={name: value for name, value in os.environ.items() if not name.startswith("A13N_")},
                stdout=log,
                stderr=log,
            )
            try:
                if kill_refresh:
                    async with asyncio.timeout(20):
                        while True:
                            counted = (await peer.get("/fixture/oauth-state")).json()
                            if len(counted["requests"]) == 2:
                                break
                            assert process.returncode is None, (tmp_path / "worker.log").read_text()
                            await asyncio.sleep(0.025)
                    assert counted["requests"][1]["consumed"] == 1 and counted["requests"][1]["returned"] == 0
                    async with short_session(svc.app.state.storage) as session:
                        row = await session.scalar(
                            select(ConnectionAuthorizationRow).where(
                                ConnectionAuthorizationRow.connection_id == connection_id
                            )
                        )
                        operation, generation = row.operation_id, row.generation
                        assert operation is not None and row.status == "active"
                    process.kill()
                    await process.wait()
                    assert process.returncode == -9
                    # The application's real control maintenance owns deadline recovery.
                    async with asyncio.timeout(8):
                        while True:
                            status = (await svc.client.get(path + "/authorization")).json()
                            if status["status"] == "reauthorization_required":
                                break
                            await asyncio.sleep(0.05)
                    assert status["failure"]["reason"] == "operation_deadline_unknown"
                    await peer.post("/fixture/oauth-release")
                    refused = await svc.client.post(path + "/test")
                    assert refused.status_code == 422, refused.text
                    async with short_session(svc.app.state.storage) as session:
                        row = await session.get(ConnectionAuthorizationRow, status["id"])
                        assert row.operation_id == operation and row.generation == generation and row.credential is None
                    final = (await peer.get("/fixture/oauth-state")).json()
                    assert len(final["requests"]) == 2 and final["families"][0]["revoked"] == 0
                    (tmp_path / "g054-deadline.json").write_text(
                        json.dumps(
                            {
                                "signal": process.returncode,
                                "operation_id": operation,
                                "generation": generation,
                                "status": status,
                                "peer": final,
                            },
                            indent=2,
                        )
                    )
                else:
                    async with asyncio.timeout(25):
                        while True:
                            result = await svc.client.get(svc.workspace_path + f"/runs/{run_id}/items")
                            if result.json()["status"] in {"completed", "failed"}:
                                break
                            assert process.returncode is None, (tmp_path / "worker.log").read_text()
                            await asyncio.sleep(0.05)
                    assert result.json()["status"] == "completed", (result.text, (tmp_path / "worker.log").read_text())
                    assert "service proof" in result.text
                    counted = (await peer.get("/tools/fixture/state")).json()
                    assert counted["effects"] == 1
                    calls = (await peer.get("/fixture/oauth-state")).json()
                    assert calls["access"] and all(call["principal"] == "alice" for call in calls["access"])
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        async with asyncio.timeout(8):
                            await process.wait()
                    except TimeoutError:
                        process.kill()
                        await process.wait()


@pytest.mark.parametrize("faults", [{"no_refresh_token": True}, {"omit_grants": True}, {"no_expiry": True}])
async def test_optional_metadata_and_token_values(public_service, oauth_url, model_url, faults):
    import asyncio

    svc = public_service
    path = await configured(svc, oauth_url, model_url)
    async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
        await peer.post("/fixture/oauth-faults", json=faults | {"expires_in": 1})
        await authorize(svc, peer, path)
        status = (await svc.client.get(path + "/authorization")).json()
        assert status["status"] == "active"
        await asyncio.sleep(1.05)
        result = await svc.client.post(path + "/test")
        # No expiry is not fabricated; the peer's later refusal requires reconnect without a replay.
        assert result.status_code == 422, result.text
        status = (await svc.client.get(path + "/authorization")).json()
        assert status["status"] == "reauthorization_required"
        assert len((await peer.get("/fixture/oauth-state")).json()["requests"]) == 1


async def test_bound_callback_parallel_flows_and_single_exchange(public_service, oauth_url, model_url):
    import asyncio

    import httpx

    svc = public_service
    first_path = await configured(svc, oauth_url, model_url)
    second_path = await configured(svc, oauth_url, model_url)
    async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
        callbacks = []
        for path in (first_path, second_path):
            start = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
            assert start.status_code == 200, start.text
            params = {key: value[0] for key, value in parse_qs(urlparse(start.json()["redirect_url"]).query).items()}
            consent = await peer.get("/consent", params=params | {"decision": "allow", "principal": "alice"})
            callbacks.append(consent.headers["location"])
        bindings = [name for name in svc.client.cookies if name.startswith("__Host-a13n_oauth_")]
        assert len(bindings) == 2
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=svc.app), base_url="https://service.test"
        ) as stranger:
            wrong_browser = await stranger.get(callbacks[0])
            assert wrong_browser.status_code == 403 and "code=" not in wrong_browser.text
        wrong_issuer = await svc.client.get(callbacks[0].replace("iss=", "iss=wrong"))
        assert wrong_issuer.status_code == 400
        assert (await peer.get("/fixture/oauth-state")).json()["requests"] == []
        await peer.post("/fixture/oauth-faults", json={"hold": "authorization_code"})
        # Freeze each request's original browser cookie header before any response clears it.
        headers = {"cookie": "; ".join(f"{name}={svc.client.cookies.get(name)}" for name in bindings)}
        pending = [asyncio.create_task(svc.client.get(callbacks[0], headers=headers)) for _ in range(2)]
        try:
            async with asyncio.timeout(3):
                while len((await peer.get("/fixture/oauth-state")).json()["requests"]) < 1:
                    await asyncio.sleep(0.025)
            await peer.post("/fixture/oauth-release")
            responses = await asyncio.gather(*pending)
            assert [response.status_code for response in responses] == [303, 303], [
                response.text for response in responses
            ]
            assert len((await peer.get("/fixture/oauth-state")).json()["requests"]) == 1
            await peer.post("/fixture/oauth-faults", json={})
            assert (await svc.client.get(callbacks[1])).status_code == 303
            assert (await svc.client.get(callbacks[0], headers=headers)).status_code == 400
            assert len((await peer.get("/fixture/oauth-state")).json()["requests"]) == 2
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.parametrize("case", ["deny", "wrong_pkce", "expired", "oversized", "lost_response", "wrong_return"])
async def test_callback_failures_are_terminal_without_reexchange(public_service, oauth_url, model_url, case):
    from a13n_service.infra.db import transaction
    from a13n_service.resources.connections.tables import ConnectionAuthorizationRow
    from sqlalchemy import func, update

    svc = public_service
    path = await configured(svc, oauth_url, model_url)
    async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
        start = await svc.client.post(
            path + "/authorize",
            json={
                "return_url": "https://attacker.test/" if case == "wrong_return" else "https://service.test/connections"
            },
        )
        if case == "wrong_return":
            assert start.status_code == 400
            assert (await peer.get("/fixture/oauth-state")).json()["requests"] == []
            return
        assert start.status_code == 200, start.text
        params = {key: value[0] for key, value in parse_qs(urlparse(start.json()["redirect_url"]).query).items()}
        if case == "wrong_pkce":
            params["code_challenge"] = "wrong-challenge"
        consent = await peer.get(
            "/consent", params=params | {"principal": "alice", "decision": "deny" if case == "deny" else "allow"}
        )
        if case == "expired":
            async with transaction(svc.app.state.storage) as session:
                await session.execute(
                    update(ConnectionAuthorizationRow)
                    .where(ConnectionAuthorizationRow.id == start.json()["authorization"]["id"])
                    .values(expires_at=func.clock_timestamp())
                )
        if case in {"oversized", "lost_response"}:
            await peer.post("/fixture/oauth-faults", json={case: "authorization_code"})
        callback = consent.headers["location"]
        response = await svc.client.get(callback)
        assert response.status_code == (400 if case == "expired" else 303), response.text
        current = (await svc.client.get(path + "/authorization")).json()
        if case != "expired":
            assert current["status"] == "reauthorization_required"
        assert (await svc.client.get(callback)).status_code == 400
        counted = (await peer.get("/fixture/oauth-state")).json()
        assert len(counted["requests"]) == (0 if case in {"deny", "expired"} else 1)


async def test_two_principals_and_key_confinement(public_service, oauth_url, model_url):
    from types import SimpleNamespace

    import httpx
    from a13n_service.infra.db import transaction
    from a13n_service.infra.ids import new_object_id
    from a13n_service.tenancy.tables import GrantRow, PasswordRow, PrincipalRow, WorkspaceRow
    from argon2 import PasswordHasher

    svc = public_service
    path = await configured(svc, oauth_url, model_url)
    other_id, other_workspace = new_object_id("usr"), new_object_id("ws")
    async with transaction(svc.app.state.storage) as session:
        session.add(PrincipalRow(id=other_id, kind="user", email="other@example.com", name="Other", status="active"))
        session.add(
            WorkspaceRow(
                id=other_workspace, organization_id=svc.initialized.organization_id, key="foreign", name="Foreign"
            )
        )
        await session.flush()
        session.add(PasswordRow(principal_id=other_id, hash=PasswordHasher().hash("other-password")))
        session.add(
            GrantRow(
                id=new_object_id("rb"),
                organization_id=svc.initialized.organization_id,
                workspace_id=svc.initialized.workspace_id,
                principal_id=other_id,
                role="runner",
                created_by_id=svc.initialized.principal_id,
            )
        )
    async with (
        httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=svc.app), base_url="https://service.test") as other,
    ):
        await authorize(svc, peer, path, principal="alice")
        logged = await other.post(
            "/api/v1/auth/login", json={"email": "other@example.com", "password": "other-password"}
        )
        assert logged.status_code == 200, logged.text
        other.headers["x-csrf-token"] = logged.json()["csrf_token"]
        assert (await other.get(path + "/authorization")).json()["status"] == "not_authorized"
        assert (await other.get(path + "/tools")).status_code == 422
        assert len((await peer.get("/fixture/oauth-state")).json()["requests"]) == 1
        await authorize(SimpleNamespace(client=other), peer, path, principal="bob")
        assert (await other.get(path + "/tools")).status_code == 200
        assert (await svc.client.post(path + "/test")).status_code == 200
        counted = (await peer.get("/fixture/oauth-state")).json()
        assert {call["principal"] for call in counted["access"]} == {"alice", "bob"}
        assert len({call["token_hash"] for call in counted["access"]}) == 2
        foreign_path = path.replace(svc.initialized.workspace_id, other_workspace)
        assert (await other.get(foreign_path + "/authorization")).status_code in {403, 404}
        key = await svc.client.post(
            "/api/v1/users/me/keys", json={"workspace_id": svc.initialized.workspace_id, "name": "OAuth owner"}
        )
        assert key.status_code == 201, key.text
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=svc.app),
            base_url="https://service.test",
            headers={"authorization": "Bearer " + key.json()["secret"]},
        ) as confined:
            assert (await confined.get(foreign_path + "/authorization")).status_code == 403
            assert (
                await confined.post(
                    foreign_path + "/authorize", json={"return_url": "https://service.test/connections"}
                )
            ).status_code == 403
            await authorize(SimpleNamespace(client=confined), peer, path, principal="alice-key")
            assert (await confined.get(path + "/tools")).status_code == 200
        assert (await other.get(path + "/authorization")).json()["status"] == "active"
        assert (await svc.client.post(path + "/revoke")).status_code == 200
        assert (await other.get(path + "/tools")).status_code == 200
        audit = await svc.client.get(svc.workspace_path + "/audit-events")
        assert audit.status_code == 200
        assert (
            "access_token" not in audit.text and "refresh_token" not in audit.text and "code_verifier" not in audit.text
        )


@pytest.mark.parametrize(
    "case", ["wrong_issuer", "no_pkce", "omit_auth_methods", "empty_grants", "empty_auth_methods", "endpoint_denied"]
)
async def test_discovery_refuses_untrusted_or_unsupported_metadata(public_service, oauth_url, model_url, case):
    svc = public_service
    path = await configured(svc, oauth_url, model_url)
    async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
        if case == "endpoint_denied":
            svc.app.state.endpoint_policy = EndpointPolicy.from_operator_allowlist()
        else:
            await peer.post("/fixture/oauth-faults", json={case: True})
        response = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
        assert response.status_code in {400, 503}, response.text
        assert (await svc.client.get(path + "/authorization")).json()["status"] == "not_authorized"
        assert (await peer.get("/fixture/oauth-state")).json()["requests"] == []


async def test_cancelled_exchange_records_unknown_and_cleans_up(public_service, oauth_url, model_url, db_http_probe):
    import asyncio

    svc = public_service
    path = await configured(svc, oauth_url, model_url)
    async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
        started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
        params = {key: value[0] for key, value in parse_qs(urlparse(started.json()["redirect_url"]).query).items()}
        consent = await peer.get("/consent", params=params | {"principal": "alice", "decision": "allow"})
        await peer.post("/fixture/oauth-faults", json={"hold": "authorization_code"})
        before = set(asyncio.all_tasks())
        task = asyncio.create_task(svc.client.get(consent.headers["location"]))
        try:
            async with asyncio.timeout(3):
                while not (await peer.get("/fixture/oauth-state")).json()["requests"]:
                    await asyncio.sleep(0.025)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            status = (await svc.client.get(path + "/authorization")).json()
            assert status["status"] == "reauthorization_required"
            assert status["failure"]["reason"] == "unknown_after_dispatch"
            await peer.post("/fixture/oauth-release")
            assert (await svc.client.get(consent.headers["location"])).status_code == 400
            assert len((await peer.get("/fixture/oauth-state")).json()["requests"]) == 1
            assert not [pending for pending in asyncio.all_tasks() - before if not pending.done()]
            db_http_probe.assert_finished_tasks_released()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_oauth_private_material_is_encrypted_and_absent_from_public_reads(
    public_service, oauth_url, model_url, caplog
):
    import json

    from a13n_service.infra.audit import AuditEventRow
    from a13n_service.infra.db import short_session
    from a13n_service.resources.connections.oauth_state import reveal
    from a13n_service.resources.connections.tables import ConnectionAuthorizationRow, ConnectionRow
    from sqlalchemy import select

    svc = public_service
    path = await configured(svc, oauth_url, model_url, method="client_secret_basic")
    started = await svc.client.post(path + "/authorize", json={"return_url": "https://service.test/connections"})
    params = {key: values[0] for key, values in parse_qs(urlparse(started.json()["redirect_url"]).query).items()}
    async with short_session(svc.app.state.storage) as session:
        row = await session.get(ConnectionAuthorizationRow, started.json()["authorization"]["id"])
        private = reveal(row, svc.app.state.key_ring)
        verifier = private["verifier"]
        assert verifier not in json.dumps(row.credential)
        assert params["state"] not in row.oauth_state_hash
    async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
        consent = await peer.get("/consent", params=params | {"principal": "alice", "decision": "allow"})
        callback = await svc.client.get(consent.headers["location"])
        assert callback.status_code == 303
        tested = await svc.client.post(path + "/test")
        assert tested.status_code == 200, tested.text
    async with short_session(svc.app.state.storage) as session:
        row = await session.get(ConnectionAuthorizationRow, started.json()["authorization"]["id"])
        private = reveal(row, svc.app.state.key_ring)
        secrets = [
            verifier,
            private["access_token"],
            private["refresh_token"],
            "fixture-client-secret",
            params["state"],
        ]
        connection = await session.get(ConnectionRow, row.connection_id)
        audits = list(await session.scalars(select(AuditEventRow)))
        persisted = json.dumps(
            {
                "authorization": row.credential,
                "connection": connection.credential,
                "audits": [
                    {column.name: str(getattr(event, column.name)) for column in AuditEventRow.__table__.columns}
                    for event in audits
                ],
            }
        )
    public = "".join(
        [
            (await svc.client.get(path)).text,
            (await svc.client.get(path + "/authorization")).text,
            tested.text,
            callback.text,
            str(callback.headers),
            caplog.text,
            persisted,
        ]
    )
    assert all(secret not in public for secret in secrets)


async def test_http_ownership_probe_rejects_current_and_inherited_sql_scope(public_service, oauth_url, db_http_probe):
    import asyncio

    from a13n_service.infra.db import transaction
    from sqlalchemy import text

    db_http_probe.expected_blocks = 2
    async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
        async with transaction(public_service.app.state.storage) as session:
            await session.execute(text("SELECT 1"))
            with pytest.raises(AssertionError, match="SQL lease crossed"):
                await peer.get("/fixture/oauth-state")
            with pytest.raises(AssertionError, match="SQL lease crossed"):
                await asyncio.create_task(peer.get("/fixture/oauth-state"))
        assert (await peer.get("/fixture/oauth-state")).status_code == 200

    ready, release = asyncio.Event(), asyncio.Event()

    async def unrelated_control_sql():
        async with transaction(public_service.app.state.storage) as session:
            await session.execute(text("SELECT 1"))
            ready.set()
            await release.wait()

    unrelated = asyncio.create_task(unrelated_control_sql(), name="unrelated-control-proof")
    try:
        await ready.wait()
        async with httpx2.AsyncClient(base_url=oauth_url, trust_env=False) as peer:
            assert (await peer.get("/fixture/oauth-state")).status_code == 200
        assert db_http_probe.requests[-1]["active_leases"]
        assert not db_http_probe.requests[-1]["held_leases"]
    finally:
        release.set()
        await unrelated
