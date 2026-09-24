"""The one worker predicate: every execution-dependent write proves it holds the current, unexpired lease."""

import asyncio
import hmac
import math
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import Storage, lock, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.schemas import Outcome
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow
from a13n_service.tenancy.access import Access, principal_for, require_active_workspace
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, authorize


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


@dataclass
class AttemptControl:
    """Signals from the attempt's supervisor to its execution, checked at safe boundaries and before dispatch."""

    # Set when the run must stop dispatching and seal `outcome`: it was interrupted, or its principal lost the
    # authority to run it.
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    outcome: Outcome = field(default_factory=Outcome.cancelled)
    # The worker is draining: yield the run at the next safe boundary.
    handoff: asyncio.Event = field(default_factory=asyncio.Event)
    # Event-loop time when the lease runs out unless renewed, measured before the claim or renewal was sent.
    deadline: float = math.inf
    # Stop dispatching with this much lease time left if no renewal has been confirmed.
    renewal_margin: float = 0.0

    def stop(self, outcome: Outcome) -> None:
        if not self.stopped.is_set():
            self.outcome = outcome
            self.stopped.set()

    def expiring(self, margin: float) -> bool:
        """Whether the lease runs out within `margin` seconds unless a renewal is confirmed first."""
        return asyncio.get_running_loop().time() + margin >= self.deadline

    def renewal_missed(self) -> bool:
        """The lease can run out before another renewal is confirmed, so no new work may start under it."""
        return self.expiring(self.renewal_margin)


class LeaseLost(Exception):
    """The attempt no longer holds its lease, so nothing it does may be written. Deliberately not a
    `ServiceError`: no handler of refusals may turn it into an outcome, and it never reaches an API caller."""

    def __init__(self) -> None:
        super().__init__("Worker lease is no longer current")


class AuthorityRevoked(Exception):
    """The run's principal may no longer run it, so the run fails with `outcome`. Like `LeaseLost`, deliberately
    not a `ServiceError`, so no handler of refusals turns it into another outcome."""

    def __init__(self) -> None:
        super().__init__("The run's principal can no longer run it")
        self.outcome = Outcome.failed("authority_revoked", str(self))


async def authorize_execution(session: AsyncSession, access: Access, run: RunRow) -> Principal:
    """The run's principal, while its current status and grants still allow the frozen authority to run the run
    in a workspace that is not archived. Any refusal but `unavailable` is `AuthorityRevoked`."""
    scope = WorkspaceScope(run.organization_id, run.workspace_id)
    try:
        await require_active_workspace(session, run.workspace_id)
        principal = await principal_for(session, access, run.principal_id, confinement=scope)
        authorize(principal, scope, "run", authority=ExecutionAuthority.model_validate(run.authority))
    except ServiceError as error:
        if error.code == "unavailable":
            raise
        raise AuthorityRevoked() from error
    return principal


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


async def prove(storage: Storage, lease: Lease) -> None:
    """Raise `LeaseLost` unless the lease is still current, for work that must not continue without it."""
    async with transaction(storage) as session:
        await lock_lease(session, lease)
