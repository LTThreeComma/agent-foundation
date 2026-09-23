"""Idempotent cancellation requests under the canonical thread/run lock order."""

from sqlalchemy import func, select

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs import activity, events
from a13n_service.runs.schemas import RunView
from a13n_service.runs.seal import fail_locked
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.grants import workspace_scope


async def interrupt(storage: Storage, actor: Principal, workspace_id: str, run_id: str) -> RunView:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        thread_id = await session.scalar(
            select(RunRow.thread_id).where(RunRow.id == run_id, RunRow.workspace_id == workspace_id)
        )
        if thread_id is None:
            raise ServiceError("not_found", "Run was not found")
        thread = await session.get(ThreadRow, thread_id, with_for_update=True)
        run = await session.get(RunRow, run_id, with_for_update=True)
        assert thread is not None and run is not None
        if run.status == "cancelled":
            return RunView.model_validate(run)
        if run.status not in {"accepted", "running"}:
            raise ServiceError("conflict", "Sealed run cannot be interrupted")
        if run.cancel_requested_at is not None:
            return RunView.model_validate(run)
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        run.cancel_requested_at = now
        facts = []
        if run.status == "accepted":
            facts = await fail_locked(
                session, thread, run, None, now, status="cancelled", code="cancelled", message="Run was cancelled"
            )
        record(
            session,
            organization_id=run.organization_id,
            workspace_id=workspace_id,
            actor_id=actor.id,
            action="run.interrupt",
            target_kind="run",
            target_id=run.id,
        )
        await session.flush()
        await session.refresh(run)
        result = RunView.model_validate(run)
        if not facts:
            await activity.touch(session, workspace_id, [run.session_id])
        await events.flush(session, facts)
        return result
