"""State-first child admission preparation for one live parent Attempt."""

from __future__ import annotations

from collections.abc import Callable

from a13n_harness.capabilities import SubagentDelegationPlan
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.interactions.domain import (
    new_run_id,
    new_thread_id,
)
from a13n_service.interactions.objects import RunStateStore
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import new_child_run_relationship_id
from .execution_store import RetainedChildExecution
from .preparation import (
    ParentRunSource,
    PreparedChildRunAcceptance,
    PreparedChildRunResume,
    prepare_child_resume,
    prepare_child_run,
)


class ChildRunAdmissionPreparer:
    """Prepare new children from accepted snapshots and resumes from their retained source."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        *,
        thread_id_factory: Callable[[], str] = new_thread_id,
        run_id_factory: Callable[[], str] = new_run_id,
        relationship_id_factory: Callable[[], str] = new_child_run_relationship_id,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._states = states
        self._thread_id_factory = thread_id_factory
        self._run_id_factory = run_id_factory
        self._relationship_id_factory = relationship_id_factory
        self._clock = clock

    async def prepare_delegate(
        self,
        authority: AttemptContext,
        plan: SubagentDelegationPlan,
        delegated_input: str,
    ) -> PreparedChildRunAcceptance:
        return prepare_child_run(
            parent=await self._parent(authority),
            plan=plan,
            delegated_input=delegated_input,
            child_thread_id=self._thread_id_factory(),
            child_run_id=self._run_id_factory(),
            relationship_id=self._relationship_id_factory(),
            created_at=assume_utc(self._clock()),
        )

    async def prepare_resume(
        self,
        authority: AttemptContext,
        source: RetainedChildExecution,
        plan: SubagentDelegationPlan,
        delegated_input: str,
    ) -> PreparedChildRunResume:
        parent = await self._parent(authority)
        source_state = await self._states.read_run(source.run)
        return prepare_child_resume(
            parent=parent,
            plan=plan,
            delegated_input=delegated_input,
            source=source,
            source_state=source_state,
            child_run_id=self._run_id_factory(),
            relationship_id=self._relationship_id_factory(),
            created_at=assume_utc(self._clock()),
        )

    async def _parent(self, authority: AttemptContext) -> ParentRunSource:
        async with short_session(self._sessions) as database:
            parent, _, _ = await read_attempt_authority(database, authority, self._clock)
            parent_resource = parent.to_resource()
        stored = await self._states.read_run(parent_resource)
        return ParentRunSource(authority, parent_resource, stored)


__all__ = [
    "ChildRunAdmissionPreparer",
]
