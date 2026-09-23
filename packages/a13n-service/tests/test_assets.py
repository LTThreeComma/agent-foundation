"""Public immutable upload replay, publication cuts, and Asset lifecycle."""

import asyncio

import pytest
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.assets import uploads
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.runs import inputs
from a13n_service.runs.attempts import claim_run
from a13n_service.runs.execute import execute
from a13n_service.runs.input_frames import PreparedInputs
from a13n_service.runs.input_preparation import _encoded_size, _frame_base, prepare
from a13n_service.runs.schemas import MessagePayload
from a13n_service.runs.tables import InboxEntryRow
from pydantic_ai.messages import BinaryContent, TextContent
from sqlalchemy import func, select


async def stage(client, path, key="upload-one", data=b"original", *, name="sample.txt", content_type="text/plain"):
    return await client.post(
        path + "/uploads", headers={"Idempotency-Key": key}, files={"file": (name, data, content_type)}
    )


@pytest.mark.anyio
@pytest.mark.parametrize("model_url", ["live"], indirect=True)
async def test_asset_input_uses_authorized_bytes_and_retirement_blocks_new_selection(public_service):
    fixture = public_service
    staged = await stage(
        fixture.client, fixture.workspace_path, key="input-upload", data=b"frozen bytes", content_type="text/plain"
    )
    assert staged.status_code == 200, staged.text
    created = await fixture.client.post(
        fixture.workspace_path + "/assets",
        json={"upload_id": staged.json()["upload_id"], "name": "input.txt"},
    )
    assert created.status_code == 201, created.text
    asset_id = created.json()["id"]
    payload = {
        "content": [
            {"type": "text", "text": "Read this"},
            {"type": "asset", "asset_id": asset_id},
            {"type": "json", "value": False},
        ]
    }
    submitted = await fixture.client.post(
        fixture.workspace_path + "/threads",
        headers={"Idempotency-Key": "asset-input"},
        json={"kind": "message", "agent_id": fixture.agent_id, "payload": payload},
    )
    assert submitted.status_code == 201, submitted.text
    claim = await claim_run(
        fixture.app.state.storage, worker_id="asset-preparation", worker_build="test", lease_seconds=30
    )
    assert claim is not None
    retired = await fixture.client.delete(
        fixture.workspace_path + "/assets/" + asset_id, headers={"If-Match": created.headers["etag"]}
    )
    assert retired.status_code == 200, retired.text
    prepared = PreparedInputs(claim.run_id)
    offered = await prepare(
        fixture.app.state.storage,
        fixture.app.state.objects,
        claim,
        await inputs.assigned_inputs(fixture.app.state.storage, claim),
        prepared,
        policy=fixture.app.state.endpoint_policy,
        timeout=3,
        max_bytes=1048576,
    )
    assert [type(item) for item in offered] == [TextContent, TextContent, BinaryContent, TextContent]
    assert offered[2].data == b"frozen bytes"
    assert offered[3].content == "Structured JSON:\nfalse"
    assert prepared.expected[submitted.json()["entry"]["id"]].count == 3
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
    refused = await fixture.client.post(
        fixture.workspace_path + "/threads",
        headers={"Idempotency-Key": "retired-asset-input"},
        json={"kind": "message", "agent_id": fixture.agent_id, "payload": payload},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "disabled"
    async with short_session(fixture.app.state.storage) as session:
        before = await session.scalar(select(func.count()).select_from(InboxEntryRow))
    unavailable = await fixture.client.post(
        fixture.workspace_path + "/threads",
        headers={"Idempotency-Key": "environment-path-unavailable"},
        json={
            "kind": "message",
            "agent_id": fixture.agent_id,
            "payload": {"content": [{"type": "environment_path", "mount": "workspace", "path": "file.txt"}]},
        },
    )
    assert unavailable.status_code == 503, unavailable.text
    async with short_session(fixture.app.state.storage) as session:
        assert await session.scalar(select(func.count()).select_from(InboxEntryRow)) == before


@pytest.mark.anyio
async def test_asset_preparation_rejects_later_object_read_at_batch_limit(public_service, monkeypatch):
    fixture = public_service
    body = b"frozen asset bytes"
    staged = await stage(fixture.client, fixture.workspace_path, key="budget-upload", data=body)
    assert staged.status_code == 200, staged.text
    created = await fixture.client.post(
        fixture.workspace_path + "/assets",
        json={"upload_id": staged.json()["upload_id"], "name": "budget.txt"},
    )
    assert created.status_code == 201, created.text
    submitted = await fixture.client.post(
        fixture.workspace_path + "/threads",
        headers={"Idempotency-Key": "budget-asset-source"},
        json={
            "kind": "message",
            "agent_id": fixture.agent_id,
            "payload": {"content": [{"type": "text", "text": "source"}]},
        },
    )
    assert submitted.status_code == 201, submitted.text
    claim = await claim_run(fixture.app.state.storage, worker_id="asset-budget", worker_build="test", lease_seconds=30)
    assert claim is not None
    raw_key = uploads.raw_key(fixture.initialized.organization_id, staged.json()["upload_id"])
    raw_reads = 0
    actual_read = fixture.app.state.objects.read

    async def count_read(key):
        nonlocal raw_reads
        if key == raw_key:
            raw_reads += 1
        return await actual_read(key)

    monkeypatch.setattr(fixture.app.state.objects, "read", count_read)
    asset = {"type": "asset", "asset_id": created.json()["id"]}
    payload = MessagePayload.model_validate({"content": [asset, asset]})
    cost = 1 + _encoded_size(BinaryContent(body, media_type="text/plain"))
    prepared = PreparedInputs(claim.run_id)
    with pytest.raises(ServiceError) as over:
        await prepare(
            fixture.app.state.storage,
            fixture.app.state.objects,
            claim,
            (("inb_asset", payload),),
            prepared,
            policy=fixture.app.state.endpoint_policy,
            timeout=3,
            max_bytes=_frame_base(claim.run_id, "inb_asset", 2) + cost,
        )
    assert over.value.code == "payload_too_large"
    assert raw_reads == 1
    assert prepared.expected == {}


@pytest.mark.anyio
async def test_upload_replay_asset_race_retirement_and_historical_content(public_service):
    fixture = public_service
    client, path = fixture.client, fixture.workspace_path
    first = await stage(client, path)
    assert first.status_code == 200, first.text
    replay = await stage(client, path)
    assert replay.json() == first.json()
    for kwargs in ({"data": b"changed"}, {"name": "other.txt"}, {"content_type": "text/html"}):
        conflict = await stage(client, path, **kwargs)
        assert conflict.status_code == 409, conflict.text
    body = {"upload_id": first.json()["upload_id"], "name": " sample.txt "}
    created = await asyncio.gather(*(client.post(path + "/assets", json=body) for _ in range(4)))
    assert sorted(response.status_code for response in created) == [200, 200, 200, 201]
    assert len({response.json()["id"] for response in created}) == 1
    asset = created[0].json()
    assert asset["name"] == "sample.txt"
    conflict = await client.post(path + "/assets", json={**body, "name": "different"})
    assert conflict.status_code == 409
    retired = await client.delete(path + "/assets/" + asset["id"], headers={"If-Match": created[0].headers["etag"]})
    assert retired.status_code == 200, retired.text
    assert retired.json()["retired_at"] is not None
    assert retired.json()["version"] == 2
    replay = await client.post(path + "/assets", json=body)
    assert replay.status_code == 200 and replay.json() == retired.json()
    content = await client.get(path + "/assets/" + asset["id"] + "/content")
    assert content.status_code == 200 and content.content == b"original"
    async with short_session(fixture.app.state.storage) as session:
        assert await session.scalar(select(func.count()).select_from(AssetRow)) == 1


@pytest.mark.anyio
async def test_receipt_only_cut_conflicting_intent_and_lost_raw_ack(public_service, monkeypatch):
    fixture = public_service
    client, path, objects = fixture.client, fixture.workspace_path, fixture.app.state.objects
    original = objects.create_payload
    cut = True

    async def crash(key, data):
        if not key.endswith(".receipt.json") and cut:
            raise OSError("process stopped after intent publication")
        return await original(key, data)

    monkeypatch.setattr(objects, "create_payload", crash)
    partial = await stage(client, path)
    assert partial.status_code == 503, partial.text
    conflict = await stage(client, path, data=b"other")
    assert conflict.status_code == 409, conflict.text
    cut = False
    complete = await stage(client, path)
    assert complete.status_code == 200, complete.text
    upload_id = complete.json()["upload_id"]
    key = uploads.raw_key(fixture.initialized.organization_id, upload_id)
    assert (await objects.read(key)).content == b"original"

    async def lost_ack(key, data):
        await original(key, data)
        raise OSError("durable write acknowledgement lost")

    monkeypatch.setattr(objects, "create_payload", lost_ack)
    second = await stage(client, path, key="second")
    assert second.status_code == 200, second.text
    assert (await stage(client, path, key="second")).json() == second.json()
    assert second.json()["upload_id"] != upload_id


@pytest.mark.anyio
async def test_missing_bytes_and_authorization_before_object_access(public_service, monkeypatch):
    fixture = public_service
    client, path, objects = fixture.client, fixture.workspace_path, fixture.app.state.objects
    response = await stage(client, path)
    upload_id = response.json()["upload_id"]
    raw_key = uploads.raw_key(fixture.initialized.organization_id, upload_id)
    original = objects.read

    async def missing(key):
        return None if key == raw_key else await original(key)

    monkeypatch.setattr(objects, "read", missing)
    response = await client.post(path + "/assets", json={"upload_id": upload_id, "name": "sample"})
    assert response.status_code == 503, response.text
    touched = []

    async def forbidden_read(key):
        touched.append(key)
        raise AssertionError("unauthorized object lookup")

    monkeypatch.setattr(objects, "read", forbidden_read)
    client.cookies.clear()
    response = await stage(client, path)
    assert response.status_code == 401
    response = await client.post(path + "/assets", json={"upload_id": upload_id, "name": "sample"})
    assert response.status_code == 401
    assert not touched


@pytest.mark.anyio
async def test_asset_commit_lost_ack_and_no_sql_during_object_io(public_service, monkeypatch):
    from contextlib import asynccontextmanager

    from a13n_service.resources.assets import service
    from sqlalchemy import event

    fixture = public_service
    storage, objects = fixture.app.state.storage, fixture.app.state.objects
    leases = {}
    observed = []

    def checkout(connection, record, proxy):
        leases[id(record)] = asyncio.current_task()

    def checkin(connection, record):
        leases.pop(id(record), None)

    event.listen(storage.engine.sync_engine, "checkout", checkout)
    event.listen(storage.engine.sync_engine, "checkin", checkin)
    try:
        for name in ("read", "create_payload"):
            original = getattr(objects, name)

            def checking(operation):
                async def checked(*args, **kwargs):
                    assert asyncio.current_task() not in leases.values()
                    observed.append(args[0])
                    return await operation(*args, **kwargs)

                return checked

            monkeypatch.setattr(objects, name, checking(original))
        staged = await stage(fixture.client, fixture.workspace_path)
        assert staged.status_code == 200
        original_transaction = service.transaction
        fail_ack = True

        @asynccontextmanager
        async def lost_ack(storage):
            nonlocal fail_ack
            async with original_transaction(storage) as session:
                yield session
            if fail_ack:
                fail_ack = False
                raise OSError("connection lost after durable commit")

        monkeypatch.setattr(service, "transaction", lost_ack)
        body = {"upload_id": staged.json()["upload_id"], "name": "sample"}
        result = await fixture.client.post(fixture.workspace_path + "/assets", json=body)
        assert result.status_code == 200, result.text
        repeat = await fixture.client.post(fixture.workspace_path + "/assets", json=body)
        assert result.json() == repeat.json()
        assert observed
        async with short_session(storage) as session:
            assert await session.scalar(select(func.count()).select_from(AssetRow)) == 1
    finally:
        event.remove(storage.engine.sync_engine, "checkout", checkout)
        event.remove(storage.engine.sync_engine, "checkin", checkin)


@pytest.mark.anyio
async def test_competing_upload_intents_and_size_rate_limits(public_service):
    fixture = public_service
    client, path = fixture.client, fixture.workspace_path
    results = await asyncio.gather(stage(client, path, data=b"first"), stage(client, path, data=b"second"))
    assert sorted(result.status_code for result in results) == [200, 409]
    winner = next(result for result in results if result.status_code == 200)
    upload_id = winner.json()["upload_id"]
    raw = await fixture.app.state.objects.read(uploads.raw_key(fixture.initialized.organization_id, upload_id))
    assert raw.digest == winner.json()["digest"]
    config = fixture.app.state.settings
    fixture.app.state.settings = config.model_copy(
        update={"uploads": config.uploads.model_copy(update={"max_bytes": 4})}
    )
    too_big = await stage(client, path, key="large", data=b"12345")
    assert too_big.status_code == 413
    fixture.app.state.settings = config.model_copy(update={"uploads": config.uploads.model_copy(update={"limit": 1})})
    limited = await stage(client, path)
    assert limited.status_code == 429


@pytest.mark.anyio
async def test_scope_isolation_revocation_and_sql_immutable_guards(public_service, monkeypatch):
    from a13n_service.infra.db import transaction
    from a13n_service.tenancy.tables import GrantRow, WorkspaceRow
    from sqlalchemy import delete, text
    from sqlalchemy.exc import DBAPIError

    fixture = public_service
    storage, client, path = fixture.app.state.storage, fixture.client, fixture.workspace_path
    first = await stage(client, path)
    async with transaction(storage) as session:
        session.add(
            WorkspaceRow(
                id="ws_0123456789abcdef0123",
                organization_id=fixture.initialized.organization_id,
                key="second",
                name="Second",
            )
        )
    second = await stage(client, "/api/v1/workspaces/ws_0123456789abcdef0123")
    assert second.status_code == 200
    assert first.json()["upload_id"] != second.json()["upload_id"]
    wrong_scope = await client.post(
        "/api/v1/workspaces/ws_0123456789abcdef0123/assets",
        json={"upload_id": first.json()["upload_id"], "name": "sample"},
    )
    assert wrong_scope.status_code == 404
    body = {"upload_id": first.json()["upload_id"], "name": "sample"}
    created = await client.post(path + "/assets", json=body)
    asset_id = created.json()["id"]
    for statement in ("UPDATE assets SET name='changed' WHERE id=:id", "DELETE FROM assets WHERE id=:id"):
        with pytest.raises(DBAPIError):
            async with transaction(storage) as session:
                await session.execute(text(statement), {"id": asset_id})
    async with transaction(storage) as session:
        await session.execute(delete(GrantRow))
    touched = []

    async def no_storage(*args):
        touched.append(args)
        raise AssertionError("revoked authority reached storage")

    monkeypatch.setattr(fixture.app.state.objects, "read", no_storage)
    monkeypatch.setattr(fixture.app.state.objects, "create_payload", no_storage)
    assert (await stage(client, path)).status_code == 403
    assert (await client.post(path + "/assets", json=body)).status_code == 403
    assert not touched


@pytest.mark.anyio
async def test_corrupt_upload_refuses_materialization(public_service):
    fixture = public_service
    result = await stage(fixture.client, fixture.workspace_path)
    upload_id = result.json()["upload_id"]
    objects = fixture.app.state.objects
    key = uploads.raw_key(fixture.initialized.organization_id, upload_id)
    # Fault injection into the disposable local backend's durable envelope.
    objects._path(key).write_bytes(b"corrupt")
    response = await fixture.client.post(
        fixture.workspace_path + "/assets", json={"upload_id": upload_id, "name": "sample"}
    )
    assert response.status_code == 503
    async with short_session(fixture.app.state.storage) as session:
        assert await session.scalar(select(func.count()).select_from(AssetRow)) == 0


@pytest.mark.anyio
async def test_upload_key_is_principal_scoped_but_handle_is_workspace_authorized(public_service):
    from a13n_service.infra.db import transaction
    from a13n_service.tenancy.grants import principal_for
    from a13n_service.tenancy.tables import GrantRow, PrincipalRow

    fixture = public_service
    first = await stage(fixture.client, fixture.workspace_path)
    principal_id = "usr_0123456789abcdef0123"
    async with transaction(fixture.app.state.storage) as session:
        session.add(PrincipalRow(id=principal_id, kind="user", name="Second", email="second@example.com"))
        await session.flush()
        session.add(
            GrantRow(
                id="gr_0123456789abcdef0123",
                organization_id=fixture.initialized.organization_id,
                workspace_id=fixture.initialized.workspace_id,
                principal_id=principal_id,
                role="builder",
                created_by_id=fixture.initialized.principal_id,
            )
        )
    async with short_session(fixture.app.state.storage) as session:
        actor = await principal_for(session, principal_id)
    result = await uploads.stage(
        fixture.app.state.storage,
        fixture.app.state.objects,
        actor,
        fixture.initialized.workspace_id,
        key="upload-one",
        filename="sample.txt",
        content_type="text/plain",
        content=b"original",
        max_bytes=1024,
        timeout=5,
    )
    assert result.upload_id != first.json()["upload_id"]
    # A writer in the same Workspace may materialize another writer's upload.
    scope = await uploads.scope_for(fixture.app.state.storage, actor, fixture.initialized.workspace_id, "write")
    receipt, content = await uploads.load(fixture.app.state.objects, scope, first.json()["upload_id"], timeout=5)
    assert receipt.principal_id != actor.id and content == b"original"
