"""Typed JSON and URL input use bounded native preparation."""

import asyncio

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import claim_run
from a13n_service.runs.execute import execute
from a13n_service.runs.input_frames import PreparedInputs
from a13n_service.runs.input_preparation import _encoded_size, _frame_base, _url, prepare, render_json
from a13n_service.runs.schemas import Checkpoint, MessagePayload
from a13n_service.runs.snapshots import object_key
from pydantic_ai.messages import BinaryContent


@pytest.mark.parametrize(
    ("value", "encoded"),
    [
        (None, "null"),
        (False, "false"),
        (0, "0"),
        ("", '""'),
        ([], "[]"),
        ({}, "{}"),
        ({"β": 1, "a": 2}, '{"a":2,"β":1}'),
    ],
)
def test_structured_json_preserves_actual_value(value, encoded):
    payload = MessagePayload.model_validate({"content": [{"type": "json", "value": value}]})
    assert payload.content[0].value == value
    assert render_json(value).content == "Structured JSON:\n" + encoded


@pytest.mark.anyio
async def test_url_preparation_checks_policy_redirect_type_and_byte_limit():
    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        path = request.split(b" ", 2)[1]
        if path == b"/redirect":
            status, headers, body = "302 Found", "Location: /file\r\n", b""
        elif path == b"/html":
            status, headers, body = "200 OK", "Content-Type: text/html\r\n", b"markup"
        elif path == b"/large":
            status, headers, body = "200 OK", "Content-Type: image/png\r\n", b"123456"
        else:
            status, headers, body = "200 OK", "Content-Type: image/png\r\n", b"image"
        writer.write(
            f"HTTP/1.1 {status}\r\n{headers}Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        allowed = EndpointPolicy.from_operator_allowlist(private_cidrs=["127.0.0.0/8"], http_origins=[origin])
        assert (await _url(origin + "/redirect", allowed, timeout=3, max_bytes=100)).data == b"image"
        with pytest.raises(ServiceError):
            await _url(origin + "/file", EndpointPolicy(), timeout=3, max_bytes=100)
        with pytest.raises(ServiceError):
            await _url(origin + "/html", allowed, timeout=3, max_bytes=100)
        with pytest.raises(ServiceError):
            await _url(origin + "/large", allowed, timeout=3, max_bytes=5)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_preparation_budget_stops_url_fetches_and_keeps_batch_atomic(public_service):
    requests: list[bytes] = []
    body = b"x" * 30

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        path = request.split(b" ", 2)[1]
        requests.append(path)
        response_body = b"y" * len(body) if path == b"/second" else body
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\nContent-Length: {len(response_body)}\r\nConnection: close\r\n\r\n".encode()
            + response_body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    try:
        fixture = public_service
        submitted = await fixture.client.post(
            fixture.workspace_path + "/threads",
            headers={"Idempotency-Key": "budget-source"},
            json={
                "kind": "message",
                "agent_id": fixture.agent_id,
                "payload": {"content": [{"type": "text", "text": "source"}]},
            },
        )
        assert submitted.status_code == 201, submitted.text
        claim = await claim_run(
            fixture.app.state.storage, worker_id="budget-test", worker_build="test", lease_seconds=30
        )
        assert claim is not None
        origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        policy = EndpointPolicy.from_operator_allowlist(private_cidrs=["127.0.0.0/8"], http_origins=[origin])

        def payload(*paths: str) -> MessagePayload:
            return MessagePayload.model_validate({"content": [{"type": "url", "url": origin + path} for path in paths]})

        cost = 1 + _encoded_size(BinaryContent(body, media_type="application/pdf"))
        first_base = _frame_base(claim.run_id, "inb_first", 1)
        exact = first_base + cost
        prepared = PreparedInputs(claim.run_id)
        offered = await prepare(
            fixture.app.state.storage,
            fixture.app.state.objects,
            claim,
            (("inb_first", payload("/exact")),),
            prepared,
            policy=policy,
            timeout=3,
            max_bytes=exact,
        )
        assert [item.data for item in offered if isinstance(item, BinaryContent)] == [body]
        assert prepared.expected["inb_first"].count == 1
        assert requests == [b"/exact"]

        requests.clear()
        prepared = PreparedInputs(claim.run_id)
        with pytest.raises(ServiceError) as encoded_over:
            await prepare(
                fixture.app.state.storage,
                fixture.app.state.objects,
                claim,
                (("inb_first", payload("/encoded-over")),),
                prepared,
                policy=policy,
                timeout=3,
                max_bytes=exact - 1,
            )
        assert encoded_over.value.code == "payload_too_large"
        assert len(body) < exact - 1  # Raw bytes fit; native base64 and marker bytes do not.
        assert requests == [b"/encoded-over"]
        assert prepared.expected == {}

        requests.clear()
        prepared = PreparedInputs(claim.run_id)
        with pytest.raises(ServiceError) as over:
            await prepare(
                fixture.app.state.storage,
                fixture.app.state.objects,
                claim,
                (("inb_first", payload("/first", "/second", "/unused")),),
                prepared,
                policy=policy,
                timeout=3,
                max_bytes=_frame_base(claim.run_id, "inb_first", 3) + cost,
            )
        assert over.value.code == "payload_too_large"
        assert requests == [b"/first"]
        assert prepared.expected == {}

        requests.clear()
        prepared = PreparedInputs(claim.run_id)
        with pytest.raises(ServiceError):
            await prepare(
                fixture.app.state.storage,
                fixture.app.state.objects,
                claim,
                (("inb_first", payload("/first")), ("inb_second", payload("/unused"))),
                prepared,
                policy=policy,
                timeout=3,
                max_bytes=exact + _frame_base(claim.run_id, "inb_second", 1),
            )
        assert requests == [b"/first"]
        assert prepared.expected == {}

        requests.clear()
        prepared = PreparedInputs(claim.run_id)
        offered = await prepare(
            fixture.app.state.storage,
            fixture.app.state.objects,
            claim,
            (("inb_first", payload("/first", "/second")),),
            prepared,
            policy=policy,
            timeout=3,
            max_bytes=_frame_base(claim.run_id, "inb_first", 2) + 2 * cost,
        )
        assert requests == [b"/first", b"/second"]
        assert [item.data for item in offered if isinstance(item, BinaryContent)] == [body, b"y" * len(body)]
        assert prepared.expected["inb_first"].count == 2
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_oversize_url_batch_has_no_receipt_or_model_effect(public_service, model_url, monkeypatch):
    requests: list[bytes] = []
    body = b"x" * 30720

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        requests.append(request.split(b" ", 2)[1])
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        try:
            await writer.drain()
        except ConnectionResetError:
            pass
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    try:
        fixture = public_service
        origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        policy = EndpointPolicy.from_operator_allowlist(
            private_cidrs=["127.0.0.0/8"], http_origins=[model_url.removesuffix("/v1"), origin]
        )
        payload = {"content": [{"type": "url", "url": origin + path} for path in ("/first", "/second", "/unused")]}
        submitted = await fixture.client.post(
            fixture.workspace_path + "/threads",
            headers={"Idempotency-Key": "bounded-execution"},
            json={"kind": "message", "agent_id": fixture.agent_id, "payload": payload},
        )
        assert submitted.status_code == 201, submitted.text
        claim = await claim_run(
            fixture.app.state.storage, worker_id="bounded-execution", worker_build="test", lease_seconds=30
        )
        assert claim is not None
        opened = []

        def forbidden_model(*args, **kwargs):
            opened.append(True)
            raise AssertionError("Model must not open for a partly prepared batch")

        monkeypatch.setattr("a13n_service.runs.execute.open_model", forbidden_model)
        settings = fixture.app.state.settings.model_copy(
            update={"objects": fixture.app.state.settings.objects.model_copy(update={"max_bytes": 65536})}
        )
        with pytest.raises(ServiceError) as over:
            await execute(
                fixture.app.state.storage,
                fixture.app.state.objects,
                claim,
                config=settings,
                redis=fixture.app.state.redis,
                catalog=fixture.app.state.model_catalog,
                tool_catalog=fixture.app.state.tool_catalog,
                keys=fixture.app.state.key_ring,
                endpoint_policy=policy,
                admission=fixture.app.state.admission,
            )
        assert over.value.code == "payload_too_large"
        assert requests == [b"/first", b"/second"]
        assert opened == []
        checkpoint_object = await fixture.app.state.objects.read(
            object_key(claim.organization_id, claim.run_id, "state")
        )
        assert checkpoint_object is not None
        assert Checkpoint.model_validate_json(checkpoint_object.content).receipts == ()
        items = await fixture.client.get(f"{fixture.workspace_path}/runs/{claim.run_id}/items")
        assert items.status_code == 200, items.text
        assert items.json()["inputs"][0]["status"] == "assigned"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_url_input_is_prepared_during_a_submitted_run(public_service, model_url):
    requests = 0

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal requests
        await reader.readuntil(b"\r\n\r\n")
        requests += 1
        body = b"frozen URL bytes"
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    try:
        fixture = public_service
        origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        fixture.app.state.endpoint_policy = EndpointPolicy.from_operator_allowlist(
            private_cidrs=["127.0.0.0/8"], http_origins=[model_url.removesuffix("/v1"), origin]
        )
        payload = {"content": [{"type": "url", "url": origin + "/file"}]}
        submitted = await fixture.client.post(
            fixture.workspace_path + "/threads",
            headers={"Idempotency-Key": "url-input"},
            json={"kind": "message", "agent_id": fixture.agent_id, "payload": payload},
        )
        assert submitted.status_code == 201, submitted.text
        claim = await claim_run(
            fixture.app.state.storage, worker_id="url-preparation", worker_build="test", lease_seconds=30
        )
        assert claim is not None
        await execute(
            fixture.app.state.storage,
            fixture.app.state.objects,
            claim,
            config=fixture.app.state.settings,
            redis=fixture.app.state.redis,
            catalog=fixture.app.state.model_catalog,
            tool_catalog=fixture.app.state.tool_catalog,
            keys=fixture.app.state.key_ring,
            endpoint_policy=fixture.app.state.endpoint_policy,
            admission=fixture.app.state.admission,
        )
        items = await fixture.client.get(f"{fixture.workspace_path}/runs/{claim.run_id}/items")
        assert items.status_code == 200, items.text
        assert items.json()["status"] == "completed"
        assert items.json()["inputs"][0]["payload"] == payload
        assert items.json()["inputs"][0]["status"] == "consumed"
        assert requests == 1
    finally:
        server.close()
        await server.wait_closed()
