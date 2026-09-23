"""Claim: turning due accepted runs into leased attempts. PostgreSQL decides ownership; wakeups only hurry it.

Claim locks run → attempt and never the thread; archive and interrupt take thread → run, so the run lock
arbitrates between them. Every attempt but a handoff's successor is charged to the run's `max_attempts`: a
planned handoff is not a failure, and each one needs its worker to drain, which then claims nothing more.
"""

import secrets
from datetime import timedelta

from sqlalchemy import select

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import now, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.runs import checkpoints
from a13n_service.runs.attempts import Lease, lock_lease
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.runs.webhooks import notify_subscribers


async def claim(runtime: Runtime, *, worker_id: str, worker_build: str, limit: int) -> list[Lease]:
    """Lease up to `limit` due accepted runs, skipping rows other workers hold."""
    leases: list[Lease] = []
    async with transaction(runtime.storage) as session:
        current = await now(session)
        runs = (
            await session.scalars(
                select(RunRow)
                .where(RunRow.status == "accepted", RunRow.available_at <= current, checkpoints.claimable())
                .order_by(RunRow.available_at, RunRow.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for run in runs:
            previous = await session.scalar(
                select(AttemptRow).where(AttemptRow.run_id == run.id).order_by(AttemptRow.number.desc()).limit(1)
            )
            handoff = previous is not None and previous.status == "yielded"
            token = secrets.token_urlsafe(32)
            attempt = AttemptRow(
                id=new_object_id("rat"),
                organization_id=run.organization_id,
                workspace_id=run.workspace_id,
                run_id=run.id,
                number=previous.number + 1 if previous is not None else 1,
                status="leased",
                start_reason="initial" if previous is None else ("handoff" if handoff else "recovery"),
                replaces_attempt_id=previous.id if previous is not None else None,
                worker_id=worker_id,
                worker_build=worker_build,
                lease_token_hash=secret_hash(token),
                lease_expires_at=current + timedelta(seconds=runtime.settings.worker.lease_seconds),
                heartbeat_at=current,
            )
            session.add(attempt)
            if not handoff:
                run.attempts += 1
            run.status, run.current_attempt_id = "running", attempt.id
            run.started_at = run.started_at or current
            await session.flush()
            await notify_subscribers(
                session, runtime, run, ["run.running", "run_attempt.leased"], at=current, attempt=attempt
            )
            leases.append(
                Lease(
                    run_id=run.id,
                    attempt_id=attempt.id,
                    thread_id=run.thread_id,
                    organization_id=run.organization_id,
                    workspace_id=run.workspace_id,
                    number=attempt.number,
                    worker_id=worker_id,
                    token=token,
                )
            )
    return leases


async def start(runtime: Runtime, lease: Lease, *, harness_run_id: str) -> None:
    """The attempt is executing: record the Harness run that owns its traces and usage."""
    async with transaction(runtime.storage) as session:
        run, attempt, current = await lock_lease(session, lease)
        attempt.status, attempt.started_at, attempt.harness_run_id = "running", current, harness_run_id
        await session.flush()
        await notify_subscribers(session, runtime, run, ["run_attempt.running"], at=current, attempt=attempt)
