"""Bounded idle-thread advancement; rejected sources cannot starve later work."""

from sqlalchemy import exists, func, select
from sqlalchemy.orm import aliased

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, transaction
from a13n_service.runs import events
from a13n_service.runs.acceptance import accept
from a13n_service.runs.policy import AdmissionPolicy
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow


async def advance_one(storage: Storage, *, max_attempts: int, keys: KeyRing, policy: AdmissionPolicy | None) -> bool:
    latest = aliased(RunRow)
    async with transaction(storage) as session:
        thread = await session.scalar(
            select(ThreadRow)
            .where(
                ThreadRow.current_run_id.is_(None),
                ThreadRow.archived_at.is_(None),
                exists(
                    select(InboxEntryRow.id).where(
                        InboxEntryRow.thread_id == ThreadRow.id, InboxEntryRow.status == "pending"
                    )
                ),
                ~exists(
                    select(latest.id).where(
                        latest.id == ThreadRow.last_run_id, latest.status.in_(("failed", "cancelled"))
                    )
                ),
            )
            .order_by(ThreadRow.updated_at, ThreadRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if thread is None:
            return False
        _, facts = await accept(session, thread, None, max_attempts=max_attempts, keys=keys, policy=policy)
        thread.updated_at = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        await events.flush(session, facts)
        return True
