"""Real SQL counts and revocation between bounded public/worker observations."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace

import pytest
from a13n_harness.model_calls import ModelCall
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs import selection
from a13n_service.runs.attempts import claim_run, start
from a13n_service.runs.policy import CallCheck
from a13n_service.tenancy import routes
from a13n_service.tenancy.tables import GrantRow
from sqlalchemy import event, update

pytestmark = pytest.mark.anyio


@contextmanager
def statements(storage):
    captured = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    event.listen(storage.engine.sync_engine, "before_cursor_execute", capture)
    try:
        yield captured
    finally:
        event.remove(storage.engine.sync_engine, "before_cursor_execute", capture)


def one_authority_read(sql):
    assert sum("FROM grants" in query for query in sql) == 1, sql
    assert sum("FROM principals" in query for query in sql) == 1, sql
    assert not any("FROM principals" in query and "FOR " in query for query in sql), sql


async def submit(service, key):
    return await service.client.post(
        service.workspace_path + "/threads",
        headers={"Idempotency-Key": key},
        json={
            "kind": "message",
            "agent_id": service.agent_id,
            "payload": {"content": [{"type": "text", "text": "Hello"}]},
        },
    )


async def test_public_operation_reuses_authority_and_next_operation_observes_revocation(public_service, monkeypatch):
    service, storage = public_service, public_service.app.state.storage
    observed, resume = asyncio.Event(), asyncio.Event()
    original = routes.authenticate

    async def pause_after_observation(*args, **kwargs):
        credential = await original(*args, **kwargs)
        observed.set()
        await resume.wait()
        return credential

    with monkeypatch.context() as patch:
        patch.setattr(routes, "authenticate", pause_after_observation)
        with statements(storage) as sql:
            request = asyncio.create_task(submit(service, "observed-before-revoke"))
            try:
                await asyncio.wait_for(observed.wait(), 3)
                # Revocation commits while this request is paused; no IAM read lock blocks it.
                async with asyncio.timeout(3), transaction(storage) as session:
                    await session.execute(
                        update(GrantRow)
                        .where(GrantRow.principal_id == service.initialized.principal_id)
                        .values(role="viewer")
                    )
            finally:
                resume.set()
            accepted = await request
        assert accepted.status_code == 201, accepted.text
        one_authority_read(sql)
        assert sum("FROM workspaces" in query for query in sql) == 1
        assert not any("FOR SHARE" in query for query in sql)
    with statements(storage) as sql:
        denied = await submit(service, "after-revoke")
    assert denied.status_code == 403, denied.text
    one_authority_read(sql)
    assert storage.engine.pool.checkedout() == 0


async def test_worker_each_selection_refresh_and_call_shares_current_authority(public_service):
    service, storage = public_service, public_service.app.state.storage
    assert (await submit(service, "worker-observation")).status_code == 201
    claim = await claim_run(storage, worker_id="observe", worker_build="test", lease_seconds=30)
    assert claim is not None
    await start(storage, claim, harness_run_id="run_native")
    with statements(storage) as sql:
        _, model = await selection.load(storage, claim)
    one_authority_read(sql)
    check = CallCheck(storage, claim, model, None)
    call = ModelCall(
        call_id="call_first",
        harness_run_id="run_native",
        model_run_id=None,
        agent_instance_id=claim.attempt_id,
        parent_agent_instance_id=None,
        delegation_id=None,
        model_id=None,
        model_name=model.config.model_name,
        provider_name=model.provider_type,
        source="agent",
        tool_id=None,
        tool_call_id=None,
    )
    for operation in (check.refresh, lambda: check.check(call)):
        with statements(storage) as sql:
            await operation()
        one_authority_read(sql)
        assert sum("FROM models" in query for query in sql) == 1
        assert sum("FROM model_providers" in query for query in sql) == 1
    async with transaction(storage) as session:
        await session.execute(
            update(GrantRow).where(GrantRow.principal_id == service.initialized.principal_id).values(role="viewer")
        )
    for operation in (check.refresh, lambda: check.check(replace(call, call_id="call_revoked"))):
        with statements(storage) as sql, pytest.raises(ServiceError) as denied:
            await operation()
        assert denied.value.code == "forbidden"
        one_authority_read(sql)
    assert "call_revoked" not in check.calls
