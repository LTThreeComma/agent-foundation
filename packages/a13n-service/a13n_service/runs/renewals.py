"""Bounded lease renewals: set reads, per-attempt authority, one update and one confirmed commit.

Run locks precede attempt locks. Both skip contention, so a checkpoint or a parent operating on its child
cannot make unrelated attempts wait. Missing rows lose their lease; locked rows remain unconfirmed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from a13n_service.infra.db import Storage, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import AuthorityRevoked, Lease, holds
from a13n_service.runs.schemas import Outcome
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.tenancy.access import Access, principals_for
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope, authorize
from a13n_service.tenancy.tables import WorkspaceRow

BATCH_SIZE = 64


@dataclass(frozen=True, slots=True)
class Renewal:
    status: Literal["renewed", "lost", "skipped"]
    # A renewed lease may be needed only to seal cancellation or revoked authority.
    outcome: Outcome | None = None


async def renew(storage: Storage, access: Access, leases: Sequence[Lease], *, seconds: float) -> dict[str, Renewal]:
    """Results keyed by attempt ID, returned only after commit. Failures confirm no local deadlines."""
    if len(leases) > BATCH_SIZE or len({lease.attempt_id for lease in leases}) != len(leases):
        raise ValueError("A renewal batch must contain at most 64 distinct attempts")
    if not leases:
        return {}
    results = {lease.attempt_id: Renewal("skipped") for lease in leases}
    async with transaction(storage) as session:
        runs = await _runs(session, leases)
        attempts = await _attempts(session, [lease for lease in leases if lease.run_id in runs])
        # Absent lock results may be locked rather than missing. Ordinary ID reads do not wait on row locks.
        missing_runs = {lease.run_id for lease in leases} - runs.keys()
        existing_runs = (
            set(await session.scalars(select(RunRow.id).where(RunRow.id.in_(missing_runs)))) if missing_runs else set()
        )
        missing_attempts = {lease.attempt_id for lease in leases if lease.run_id in runs} - attempts.keys()
        existing_attempts = (
            set(await session.scalars(select(AttemptRow.id).where(AttemptRow.id.in_(missing_attempts))))
            if missing_attempts
            else set()
        )
        current = await now(session)
        valid: dict[str, RunRow] = {}
        for lease in leases:
            run, attempt = runs.get(lease.run_id), attempts.get(lease.attempt_id)
            if run is None:
                if lease.run_id not in existing_runs:
                    results[lease.attempt_id] = Renewal("lost")
            elif attempt is None:
                if lease.attempt_id not in existing_attempts:
                    results[lease.attempt_id] = Renewal("lost")
            elif not holds(lease, run, attempt, current):
                results[lease.attempt_id] = Renewal("lost")
            else:
                valid[lease.attempt_id] = run
        if valid:
            outcomes = await _authority(session, access, list(valid.values()))
            renewed = set(
                await session.scalars(
                    update(AttemptRow)
                    .where(AttemptRow.id.in_(valid), AttemptRow.lease_expires_at > func.clock_timestamp())
                    .values(
                        heartbeat_at=func.clock_timestamp(),
                        lease_expires_at=func.clock_timestamp() + timedelta(seconds=seconds),
                    )
                    .returning(AttemptRow.id)
                    .execution_options(synchronize_session=False)
                )
            )
            for attempt_id, run in valid.items():
                # Authority loading may have taken the lease past expiry. The UPDATE refuses it too.
                results[attempt_id] = Renewal("renewed", outcomes[run.id]) if attempt_id in renewed else Renewal("lost")
    return results


async def _runs(session: AsyncSession, leases: Sequence[Lease]) -> dict[str, RunRow]:
    rows = await session.scalars(
        select(RunRow)
        .options(
            load_only(
                RunRow.id,
                RunRow.status,
                RunRow.current_attempt_id,
                RunRow.cancel_requested_at,
                RunRow.organization_id,
                RunRow.workspace_id,
                RunRow.principal_id,
                RunRow.authority,
            )
        )
        .where(RunRow.id.in_({lease.run_id for lease in leases}))
        .order_by(RunRow.id)
        .with_for_update(skip_locked=True)
    )
    return {row.id: row for row in rows}


async def _attempts(session: AsyncSession, leases: Sequence[Lease]) -> dict[str, AttemptRow]:
    if not leases:
        return {}
    rows = await session.scalars(
        select(AttemptRow)
        .options(
            load_only(
                AttemptRow.id,
                AttemptRow.run_id,
                AttemptRow.status,
                AttemptRow.worker_id,
                AttemptRow.lease_token_hash,
                AttemptRow.lease_expires_at,
            )
        )
        .where(AttemptRow.id.in_({lease.attempt_id for lease in leases}))
        .order_by(AttemptRow.id)
        .with_for_update(skip_locked=True)
    )
    return {row.id: row for row in rows}


async def _authority(session: AsyncSession, access: Access, runs: Sequence[RunRow]) -> dict[str, Outcome | None]:
    outcomes: dict[str, Outcome | None] = {
        run.id: Outcome.cancelled() if run.cancel_requested_at is not None else None for run in runs
    }
    active = [run for run in runs if run.cancel_requested_at is None]
    if not active:
        return outcomes
    workspaces = {
        workspace_id: archived_at
        for workspace_id, archived_at in await session.execute(
            select(WorkspaceRow.id, WorkspaceRow.archived_at).where(
                WorkspaceRow.id.in_({run.workspace_id for run in active})
            )
        )
    }
    identities = [(run.principal_id, WorkspaceScope(run.organization_id, run.workspace_id)) for run in active]
    principals = await principals_for(session, access, identities)
    for run, identity in zip(active, identities, strict=True):
        principal = principals[identity]
        if (
            run.workspace_id not in workspaces
            or workspaces[run.workspace_id] is not None
            or isinstance(principal, ServiceError)
        ):
            outcomes[run.id] = AuthorityRevoked().outcome
            continue
        try:
            authorize(principal, identity[1], "run", authority=ExecutionAuthority.model_validate(run.authority))
        except ServiceError:
            outcomes[run.id] = AuthorityRevoked().outcome
    return outcomes
