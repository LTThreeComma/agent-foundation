"""The one worker predicate: every execution-dependent write proves it holds the current, unexpired lease."""

import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import Storage, lock, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow


@dataclass(frozen=True, slots=True)
class Lease:
    """A worker's claim on one attempt. The token exists only in the worker's memory; the row stores its hash."""

    run_id: str
    attempt_id: str
    thread_id: str
    organization_id: str
    workspace_id: str
    number: int
    worker_id: str
    token: str = field(repr=False)


class LeaseLost(ServiceError):
    def __init__(self) -> None:
        super().__init__("conflict", "Worker lease is no longer current", {"reason": "lease_lost"})


def holds(lease: Lease, run: RunRow, attempt: AttemptRow, current: datetime) -> bool:
    """`current` must be read after the run and attempt locks were acquired: transaction time can be stale."""
    return (
        run.id == lease.run_id
        and run.status == "running"
        and run.current_attempt_id == attempt.id == lease.attempt_id
        and attempt.run_id == run.id
        and attempt.status in {"leased", "running"}
        and attempt.worker_id == lease.worker_id
        and hmac.compare_digest(attempt.lease_token_hash, secret_hash(lease.token))
        and attempt.lease_expires_at > current
    )


async def lock_lease(session: AsyncSession, lease: Lease) -> tuple[RunRow, AttemptRow, datetime]:
    """Lock run → attempt and prove the lease; claim and heartbeat use this suffix of the lock order."""
    run = await lock(session, RunRow, lease.run_id)
    attempt = await lock(session, AttemptRow, lease.attempt_id)
    current = await now(session)
    if run is None or attempt is None or not holds(lease, run, attempt, current):
        raise LeaseLost()
    return run, attempt, current


async def lock_thread_lease(session: AsyncSession, lease: Lease) -> tuple[ThreadRow, RunRow, AttemptRow, datetime]:
    """Thread → run → attempt, for writes that also change the thread or its inbox."""
    thread = await lock(session, ThreadRow, lease.thread_id)
    if thread is None:
        raise LeaseLost()
    run, attempt, current = await lock_lease(session, lease)
    return thread, run, attempt, current


async def renew(storage: Storage, lease: Lease, *, seconds: float) -> datetime:
    """Extend from database time. An expired lease is never revived, even before the sweep closes it."""
    async with transaction(storage) as session:
        _, attempt, current = await lock_lease(session, lease)
        attempt.heartbeat_at = current
        attempt.lease_expires_at = current + timedelta(seconds=seconds)
        return attempt.lease_expires_at


async def check(storage: Storage, lease: Lease) -> RunRow:
    """A read-only proof used by polling tasks; the returned row carries cancellation state."""
    async with transaction(storage) as session:
        run, _, _ = await lock_lease(session, lease)
        return run
