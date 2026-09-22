"""The single fresh-database-clock worker fence, claim and lease renewal."""

import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.runs import events
from a13n_service.runs.schemas import AttemptClaim
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow
from a13n_service.tenancy.authenticate import secret_hash


class LeaseLost(ServiceError):
    def __init__(self):
        super().__init__("conflict", "Worker lease is no longer current")


async def require_authority(
    session: AsyncSession, claim: AttemptClaim, run: RunRow, attempt: AttemptRow | None
) -> datetime:
    """Caller holds run and attempt locks; time is read after all lock waits."""
    now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    if (
        run.id != claim.run_id
        or run.thread_id != claim.thread_id
        or run.organization_id != claim.organization_id
        or run.workspace_id != claim.workspace_id
        or run.status != "running"
        or run.current_attempt_id != claim.attempt_id
        or attempt is None
        or attempt.id != claim.attempt_id
        or attempt.run_id != run.id
        or attempt.status not in {"leased", "running"}
        or attempt.worker_id != claim.worker_id
        or not hmac.compare_digest(attempt.lease_token_hash, secret_hash(claim.token))
        or attempt.lease_expires_at <= now
    ):
        raise LeaseLost()
    return now


async def lock_authority(session: AsyncSession, claim: AttemptClaim) -> tuple[RunRow, AttemptRow, datetime]:
    run = await session.get(RunRow, claim.run_id, with_for_update=True)
    if run is None:
        raise LeaseLost()
    attempt = await session.get(AttemptRow, claim.attempt_id, with_for_update=True)
    now = await require_authority(session, claim, run, attempt)
    assert attempt is not None
    return run, attempt, now


async def claim_run(storage: Storage, *, worker_id: str, worker_build: str, lease_seconds: int) -> AttemptClaim | None:
    """The worker must reserve a local slot before invoking this short claim."""
    async with transaction(storage) as session:
        run = await session.scalar(
            select(RunRow)
            .join(ThreadRow, RunRow.thread_id == ThreadRow.id)
            .where(
                RunRow.status == "accepted",
                RunRow.available_at <= func.clock_timestamp(),
                RunRow.cancel_requested_at.is_(None),
                RunRow.attempts < RunRow.max_attempts,
                ThreadRow.archived_at.is_(None),
            )
            .order_by(RunRow.available_at, RunRow.id)
            .with_for_update(of=RunRow, skip_locked=True)
            .limit(1)
        )
        if run is None:
            return None
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        previous_id = await session.scalar(
            select(AttemptRow.id).where(AttemptRow.run_id == run.id).order_by(AttemptRow.number.desc()).limit(1)
        )
        token = secrets.token_urlsafe(32)
        attempt = AttemptRow(
            id=new_object_id("rat"),
            organization_id=run.organization_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            number=run.attempts + 1,
            status="leased",
            start_reason="recovery" if previous_id else "initial",
            replaces_attempt_id=previous_id,
            worker_id=worker_id,
            worker_build=worker_build,
            lease_token_hash=secret_hash(token),
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            lifecycle_seq=0,
        )
        session.add(attempt)
        run.status = "running"
        run.current_attempt_id = attempt.id
        run.attempts = attempt.number
        if run.started_at is None:
            run.started_at = now
        facts = [events.stage(run, attempt=attempt), events.stage(run)]
        result = AttemptClaim(
            run_id=run.id,
            attempt_id=attempt.id,
            thread_id=run.thread_id,
            organization_id=run.organization_id,
            workspace_id=run.workspace_id,
            number=attempt.number,
            worker_id=worker_id,
            token=token,
            lease_expires_at=attempt.lease_expires_at,
        )
        await events.flush(session, facts)
        return result


async def heartbeat(storage: Storage, claim: AttemptClaim, *, lease_seconds: int) -> datetime:
    async with transaction(storage) as session:
        _, attempt, now = await lock_authority(session, claim)
        attempt.heartbeat_at = now
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        return attempt.lease_expires_at


async def start(storage: Storage, claim: AttemptClaim, *, harness_run_id: str) -> None:
    async with transaction(storage) as session:
        run, attempt, now = await lock_authority(session, claim)
        if attempt.status == "running":
            if attempt.harness_run_id != harness_run_id:
                raise ServiceError("conflict", "Attempt already owns another Harness run")
            return
        attempt.status = "running"
        attempt.started_at = now
        attempt.harness_run_id = harness_run_id
        await events.flush(session, [events.stage(run, attempt=attempt)])


async def check(storage: Storage, claim: AttemptClaim) -> None:
    async with transaction(storage) as session:
        await lock_authority(session, claim)
