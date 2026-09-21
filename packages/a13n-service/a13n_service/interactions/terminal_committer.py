"""Production adapters for fenced Harness terminal decisions."""

from __future__ import annotations

from datetime import timedelta

from a13n_harness import SafeFailure
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session

from .attempts import AttemptAuthorityError, AttemptContext, AttemptDisposition, AttemptExecutionService, AttemptOutcome
from .domain import RunStatus
from .models import RunAttemptRecord, RunRecord, ThreadRecord
from .objects import StoredRunState
from .outcomes import RunOutcomeService, VerifiedRunOutcome


class DatabaseAttemptCommitter:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        outcomes: RunOutcomeService,
        execution: AttemptExecutionService,
    ) -> None:
        self._sessions = sessions
        self._outcomes = outcomes
        self._execution = execution

    async def verify_state_outcome(self, authority: AttemptContext, state: StoredRunState) -> VerifiedRunOutcome:
        return await self._outcomes.verify_state_outcome(authority, state)

    async def commit_verified_state_outcome(
        self, authority: AttemptContext, verified: VerifiedRunOutcome
    ) -> AttemptOutcome:
        receipt = await self._outcomes.commit_verified_state_outcome(authority, verified)
        assert receipt.attempt_version is not None
        disposition = (
            AttemptDisposition.continuing
            if receipt.run_status is RunStatus.running
            else AttemptDisposition(receipt.run_status.value)
        )
        return AttemptOutcome(disposition, receipt.run_version, receipt.attempt_version, receipt.thread_version)

    async def commit_failure(self, authority: AttemptContext, failure: SafeFailure) -> AttemptOutcome:
        retryable = failure.code in {
            "attempt_dependency_unavailable",
            "model_provider_unavailable",
            "model_request_failed",
            "timeout",
            "object_store_unavailable",
            "environment_unavailable",
            "skill_materialization_unavailable",
            "skill_materialization_stale",
        }
        return await self._execution.fail(authority, failure, retryable=retryable, retry_after=timedelta(seconds=1))

    async def reconcile_cancelled(self, authority: AttemptContext) -> AttemptOutcome:
        async with short_session(self._sessions) as session:
            versions = (
                await session.execute(
                    select(RunRecord.version, RunAttemptRecord.version, ThreadRecord.version)
                    .join(RunAttemptRecord, RunAttemptRecord.run_id == RunRecord.id)
                    .join(ThreadRecord, ThreadRecord.id == RunRecord.thread_id)
                    .where(
                        RunRecord.organization_id == authority.organization_id,
                        RunRecord.id == authority.run_id,
                        RunRecord.status == RunStatus.cancelled.value,
                        RunAttemptRecord.id == authority.run_attempt_id,
                        ThreadRecord.id == authority.thread_id,
                    )
                )
            ).one_or_none()
        if versions is None:
            raise AttemptAuthorityError("Local cancellation does not prove durable cancellation")
        return AttemptOutcome(AttemptDisposition.cancelled, *versions)
