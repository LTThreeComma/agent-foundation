"""Batch renewals over real PostgreSQL: bounded SQL, independent results, contention and atomic commit."""

import asyncio
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass, field, replace
from datetime import timedelta

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs import renewals
from a13n_service.runs.attempts import Lease
from a13n_service.runs.claim import claim
from a13n_service.runs.renewals import Renewal, renew
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.tenancy.access import RoleGrant
from a13n_service.tenancy.authorize import Principal, WorkspaceScope
from a13n_service.tenancy.tables import GrantRow, WorkspaceRow
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.anyio


async def _claimed(service, scripted_model, runs_kit, count: int) -> list[Lease]:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    for _ in range(count):
        await runs_kit.start_thread(service, agent, "hi")
    leases = await claim(service.runtime, worker_id="test", worker_build="test", limit=count)
    assert len(leases) == count
    return leases


@dataclass
class _SQL:
    statements: list[str] = field(default_factory=list)
    commits: int = 0


@contextmanager
def _sql(runtime: Runtime) -> Iterator[_SQL]:
    """Cursor executions and commits of this task only, excluding fixture setup and background sweeps.

    BEGIN/COMMIT, pool probes and server-side trigger queries are not cursor executions. Reject executemany
    so one captured update really is a set operation, not an arbitrary number of parameter groups.
    """
    captured, owner = _SQL(), asyncio.current_task()

    def execute(connection, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        if asyncio.current_task() is owner:
            assert not executemany
            captured.statements.append(statement)

    def commit(connection) -> None:  # type: ignore[no-untyped-def]
        if asyncio.current_task() is owner:
            captured.commits += 1

    engine = runtime.storage.engine.sync_engine
    event.listen(engine, "before_cursor_execute", execute)
    event.listen(engine, "commit", commit)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", execute)
        event.remove(engine, "commit", commit)


async def _expiries(runtime: Runtime) -> dict:
    async with short_session(runtime.storage) as session:
        return {
            key: expiry for key, expiry in await session.execute(select(AttemptRow.id, AttemptRow.lease_expires_at))
        }


async def test_one_or_sixty_four_attempts_use_the_same_seven_statements(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    leases = await _claimed(service, scripted_model, runs_kit, 64)
    runtime = service.runtime
    for count in (1, 4, 64):
        before = await _expiries(runtime)
        with _sql(runtime) as sql:
            results = await renew(runtime.storage, runtime.access, leases[:count], seconds=30)
        assert results == {lease.attempt_id: Renewal("renewed") for lease in leases[:count]}
        assert len(sql.statements) == 7, sql.statements
        assert sum(statement.startswith("UPDATE") for statement in sql.statements) == 1
        assert sql.commits == 1
        after = await _expiries(runtime)
        assert all(after[lease.attempt_id] > before[lease.attempt_id] for lease in leases[:count])
        assert all(after[lease.attempt_id] == before[lease.attempt_id] for lease in leases[count:])
    with _sql(runtime) as sql:
        assert await renew(runtime.storage, runtime.access, [], seconds=30) == {}
        with pytest.raises(ValueError, match="64 distinct"):
            await renew(
                runtime.storage, runtime.access, [*leases, replace(leases[0], attempt_id="rat_extra")], seconds=30
            )
        with pytest.raises(ValueError, match="64 distinct"):
            await renew(runtime.storage, runtime.access, [leases[0], leases[0]], seconds=30)
    assert not sql.statements and sql.commits == 0


async def test_lost_cancelled_and_locked_attempts_do_not_block_valid_renewals(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    healthy, cancelled, expired, bad_token, bad_worker, run_locked, attempt_locked = await _claimed(
        service, scripted_model, runs_kit, 7
    )
    runtime = service.runtime
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(RunRow).where(RunRow.id == cancelled.run_id).values(cancel_requested_at=func.clock_timestamp())
        )
        await session.execute(
            update(AttemptRow).where(AttemptRow.id == expired.attempt_id).values(lease_expires_at=AttemptRow.created_at)
        )
    leases = [
        healthy,
        cancelled,
        expired,
        replace(bad_token, token="wrong"),
        replace(bad_worker, worker_id="another"),
        run_locked,
        attempt_locked,
        replace(healthy, run_id="run_missing", attempt_id="rat_missing"),
        replace(healthy, attempt_id="rat_missing_attempt"),
    ]
    before = await _expiries(runtime)
    async with transaction(runtime.storage) as blocker:
        await blocker.execute(select(RunRow.id).where(RunRow.id == run_locked.run_id).with_for_update())
        await blocker.execute(select(AttemptRow.id).where(AttemptRow.id == attempt_locked.attempt_id).with_for_update())
        async with asyncio.timeout(2):
            results = await renew(runtime.storage, runtime.access, leases, seconds=60)
    assert results[healthy.attempt_id] == Renewal("renewed")
    cancellation = results[cancelled.attempt_id]
    assert (
        cancellation.status == "renewed"
        and cancellation.outcome is not None
        and cancellation.outcome.status == "cancelled"
    )
    for lease in (expired, bad_token, bad_worker, *leases[-2:]):
        assert results[lease.attempt_id] == Renewal("lost")
    for lease in (run_locked, attempt_locked):
        assert results[lease.attempt_id] == Renewal("skipped")
    after = await _expiries(runtime)
    assert {key for key in before if after[key] > before[key]} == {healthy.attempt_id, cancelled.attempt_id}
    # The skipped rows become eligible again once their locks are released.
    assert set((await renew(runtime.storage, runtime.access, [run_locked, attempt_locked], seconds=60)).values()) == {
        Renewal("renewed")
    }


async def test_authority_is_current_per_principal_and_extra_sources_are_deduplicated(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    for _ in range(2):
        await runs_kit.start_thread(service, agent, "admin")
    account = await service.client.post(f"{service.workspace}/service-accounts", json={"name": "bot"})
    assert account.status_code == 201, account.text
    bot = account.json()["id"]
    key = await service.client.post(f"{service.workspace}/service-accounts/{bot}/keys", json={"name": "key"})
    started = await service.client.post(
        f"{service.workspace}/threads",
        json=runs_kit.message(agent, "bot"),
        headers={"authorization": "Bearer " + key.json()["secret"], "idempotency-key": "bot-run"},
    )
    assert started.status_code == 201, started.text
    bot_run = started.json()["run"]["id"]
    runtime = service.runtime
    leases = await claim(runtime, worker_id="test", worker_build="test", limit=3)
    assert len(leases) == 3
    async with transaction(runtime.storage) as session:
        await session.execute(delete(GrantRow).where(GrantRow.principal_id == bot))
    observed: list[Principal] = []

    class Directory:
        async def grants_for(self, principal: Principal) -> Sequence[RoleGrant]:
            observed.append(principal)
            return []

    access = replace(runtime.access, sources=(Directory(),))
    results = await renew(runtime.storage, access, leases, seconds=30)
    assert len(observed) == 2
    assert {principal.id for principal in observed} == {bot, service.tenant.principal_id}
    assert all(
        principal.confinement is not None and principal.confinement.workspace_id == service.tenant.workspace_id
        for principal in observed
    )
    for lease in leases:
        result = results[lease.attempt_id]
        assert result.status == "renewed"
        if lease.run_id == bot_run:
            assert result.outcome is not None and result.outcome.failure is not None
            assert result.outcome.failure.code == "authority_revoked"
        else:
            assert result.outcome is None
    assert all(principal.kind == ("service_account" if principal.id == bot else "user") for principal in observed)


async def test_a_failure_at_commit_rolls_back_the_whole_batch(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    leases = await _claimed(service, scripted_model, runs_kit, 2)
    runtime = service.runtime
    # A fixture-owned deferred constraint fails after UPDATE RETURNING, at the actual commit boundary.
    async with transaction(runtime.storage) as session:
        await session.execute(
            text("""
            CREATE FUNCTION reject_test_renewal() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'test commit refusal'; END $$;
            CREATE CONSTRAINT TRIGGER reject_test_renewal AFTER UPDATE ON run_attempts
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_test_renewal();
        """)
        )
    before = await _expiries(runtime)
    with pytest.raises(DBAPIError, match="test commit refusal"):
        await renew(runtime.storage, runtime.access, leases, seconds=60)
    assert await _expiries(runtime) == before


async def test_a_lease_expiring_during_authority_loading_does_not_abort_its_peer(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    expiring, healthy = await _claimed(service, scripted_model, runs_kit, 2)
    runtime = service.runtime
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(AttemptRow)
            .where(AttemptRow.id == expiring.attempt_id)
            .values(lease_expires_at=func.clock_timestamp() + timedelta(seconds=1))
        )
    authority = renewals._authority

    async def delayed(session, access, runs):  # type: ignore[no-untyped-def]
        assert {run.id for run in runs} == {expiring.run_id, healthy.run_id}
        await asyncio.sleep(1.1)
        return await authority(session, access, runs)

    monkeypatch.setattr(renewals, "_authority", delayed)
    results = await renew(runtime.storage, runtime.access, [expiring, healthy], seconds=30)
    assert results == {expiring.attempt_id: Renewal("lost"), healthy.attempt_id: Renewal("renewed")}


async def test_an_unavailable_authority_source_confirms_no_renewals(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    leases = await _claimed(service, scripted_model, runs_kit, 2)
    runtime = service.runtime

    class Unavailable:
        async def grants_for(self, principal: Principal) -> Sequence[RoleGrant]:
            raise ServiceError("unavailable", "Directory cache is stale")

    before = await _expiries(runtime)
    with pytest.raises(ServiceError, match="Directory cache is stale"):
        await renew(runtime.storage, replace(runtime.access, sources=(Unavailable(),)), leases, seconds=60)
    assert await _expiries(runtime) == before


async def test_one_principal_keeps_separate_workspace_authority_in_a_batch(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    model_id = await runs_kit.create_model(service, scripted_model)
    created = await service.client.post(f"{service.organization}/workspaces", json={"key": "second", "name": "Second"})
    assert created.status_code == 201, created.text
    second_id = created.json()["id"]
    second = copy(service)
    second.workspace = f"/api/v1/workspaces/{second_id}"
    for target in (service, second):
        agent = await runs_kit.add_agent(target, "helper", model_id)
        await runs_kit.start_thread(target, agent, "hi")
    runtime = service.runtime
    leases = await claim(runtime, worker_id="test", worker_build="test", limit=2)
    assert len(leases) == 2
    observed: list[WorkspaceScope] = []
    denied: set[str] = set()

    class Directory:
        async def grants_for(self, principal: Principal) -> Sequence[RoleGrant]:
            assert principal.confinement is not None
            observed.append(principal.confinement)
            if principal.confinement.workspace_id in denied:
                raise ServiceError("forbidden", "Workspace access revoked")
            return []

    access = replace(runtime.access, sources=(Directory(),))
    results = await renew(runtime.storage, access, leases, seconds=30)
    assert all(result == Renewal("renewed") for result in results.values())
    assert {scope.workspace_id for scope in observed} == {service.tenant.workspace_id, second_id}
    assert len(observed) == 2
    denied.add(second_id)
    results = await renew(runtime.storage, access, leases, seconds=30)
    for lease in leases:
        result = results[lease.attempt_id]
        assert result.status == "renewed"
        assert (result.outcome is not None) == (lease.workspace_id == second_id)
    denied.clear()
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(WorkspaceRow).where(WorkspaceRow.id == second_id).values(archived_at=func.clock_timestamp())
        )
    results = await renew(runtime.storage, access, leases, seconds=30)
    for lease in leases:
        result = results[lease.attempt_id]
        assert result.status == "renewed"
        if lease.workspace_id == second_id:
            assert result.outcome is not None and result.outcome.failure is not None
            assert result.outcome.failure.code == "authority_revoked"
        else:
            assert result.outcome is None
