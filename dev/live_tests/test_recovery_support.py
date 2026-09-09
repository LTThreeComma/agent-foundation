"""Prove the fault controller measures the requested boundary, offline."""

import asyncio
import json
import sys
from contextlib import ExitStack
from types import SimpleNamespace

import anyio
import pytest
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from .recovery_faults import FaultPlan, Faults, _transaction_faults, installed_faults
from .recovery_lab import ArmedFault


@pytest.mark.anyio
async def test_one_fault_hit_is_claimed_by_exactly_one_os_process(tmp_path):
    plan = ArmedFault(
        tmp_path / "one.json", FaultPlan(point="selected", role="worker", where={"run_id": "run_selected"}, pause=False)
    )
    script = """import anyio,json,sys
from pathlib import Path
from dev.live_tests.recovery_faults import Faults
async def main():
    faults = Faults(Path(sys.argv[1]), 'worker')
    ticket = await faults.take('selected', {'run_ids': ['run_selected', 'run_successor']})
    print(json.dumps(None if ticket is None else ticket.observed))
anyio.run(main)
"""
    results = await asyncio.gather(
        *(anyio.run_process([sys.executable, "-c", script, str(tmp_path)]) for _ in range(4))
    )
    hits = [json.loads(result.stdout) for result in results if json.loads(result.stdout) is not None]
    assert len(hits) == 1
    assert await plan.reached() == hits[0]
    assert len(list(tmp_path.glob("*.claimed"))) == 1


@pytest.mark.anyio
async def test_fault_selector_does_not_consume_other_roles_or_run_ids(tmp_path):
    plan = ArmedFault(
        tmp_path / "scope.json",
        FaultPlan(point="checkpoint", where={"run_id": "run_wanted", "kind": "completed"}, hits=2),
    )
    worker, control = Faults(tmp_path, "worker"), Faults(tmp_path, "control")
    wanted = {"run_id": "run_wanted", "kind": "completed"}
    assert await control.take("checkpoint", wanted) is None
    assert await worker.take("checkpoint", {**wanted, "run_id": "run_other"}) is None
    assert await worker.take("checkpoint", {**wanted, "kind": "progress"}) is None
    first, second = await worker.take("checkpoint", wanted), await worker.take("checkpoint", wanted)
    assert first.path != second.path
    assert await worker.take("checkpoint", wanted) is None
    plan.release(index=1)
    with anyio.fail_after(1):
        await second.perform()
    with anyio.move_on_after(0.05) as blocked:
        await first.perform()
    assert blocked.cancel_called
    plan.release()
    await first.perform()


@pytest.mark.anyio
@pytest.mark.parametrize("point,action,persisted", [("before", "rollback", False), ("after", "timeout", True)])
async def test_transaction_barrier_distinguishes_rollback_from_lost_commit_ack(tmp_path, point, action, persisted):
    settings = Settings(_env_file=None, database_backend="sqlite", database_sqlite_path=tmp_path / "authority.db")
    engine = create_sql_engine(settings.database_config())
    sessions = create_session_factory(engine)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE proof (id INTEGER PRIMARY KEY)"))
        module = SimpleNamespace(transaction=transaction)
        ArmedFault(tmp_path / "commit.json", FaultPlan(point="proof.commit." + point, action=action, pause=False))
        with ExitStack() as stack:
            _transaction_faults(stack, Faults(tmp_path, "worker"), module, "proof")
            with pytest.raises(OperationalError if action == "rollback" else TimeoutError):
                async with module.transaction(sessions) as session:
                    await session.execute(text("INSERT INTO proof VALUES (1)"))
        async with short_session(sessions) as session:
            assert await session.scalar(text("SELECT count(*) FROM proof")) == int(persisted)
    finally:
        await engine.dispose()


def test_test_host_instrumentation_is_scoped_and_restores_real_operations(tmp_path):
    from a13n_service.interactions.objects import RunStateStore
    from a13n_service.storage.object_store import S3ObjectStore

    original_put, original_create = S3ObjectStore.put, RunStateStore.create
    with installed_faults({"recovery_faults": str(tmp_path)}, "worker"):
        assert S3ObjectStore.put is not original_put
        assert RunStateStore.create is not original_create
    assert S3ObjectStore.put is original_put
    assert RunStateStore.create is original_create
