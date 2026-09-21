"""Fenced, authorized, atomic acceptance of asynchronous child Runs."""

from __future__ import annotations

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.environments.websocket.admission import OnlineAdmission, OnlineEvidence
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.iam.operation import authorization_operation
from a13n_service.interactions.acceptance import (
    RunAcceptanceError,
)
from a13n_service.interactions.attempts import lock_attempt_authority
from a13n_service.interactions.domain import Run, StrictModel
from a13n_service.interactions.environment_acceptance import add_run_with_environment
from a13n_service.interactions.environment_selection import (
    EnvironmentIntent,
    ExplicitEnvironment,
    RetainedRunEnvironment,
    child_environment_choice,
)
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.objects import (
    RUN_STATE_CONTENT_TYPE,
    RunStateStore,
    StaleStateWriter,
    StoredRunState,
)
from a13n_service.interactions.ports.memory import ExecutionBindings
from a13n_service.interactions.records import thread_record
from a13n_service.interactions.session_scope import SessionScope
from a13n_service.labels import merge_labels
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .authorization import ChildRunAuthorizationError, authorize_parent_child_action
from .domain import ChildRunRelationship, child_relationship_is_visible
from .models import ChildRunRelationshipRecord
from .preparation import (
    PreparedChildRunAcceptance,
    PreparedChildRunResume,
    require_frozen_subagent_edge,
)
from .records import child_run_relationship_record


class ChildRunAcceptanceError(RunAcceptanceError):
    """A child acceptance candidate no longer matches durable authority."""


class ChildRunAcceptanceReceipt(StrictModel):
    relationship: ChildRunRelationship
    session_id: str = Field(min_length=1, max_length=72)
    child_thread_id: str = Field(min_length=1, max_length=72)
    child_run_id: str = Field(min_length=1, max_length=72)


class ChildRunAcceptanceService:
    """Accept one prepared child under the spawning Attempt's live attempt_number."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        *,
        lifecycle: LifecycleWriter,
        bindings: ExecutionBindings,
        coordination: ConnectionCoordination | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._online = OnlineAdmission(sessions, coordination)
        self._states = states
        self._clock = clock
        self._lifecycle = lifecycle
        self._bindings = bindings

    @authorization_operation
    async def accept(
        self,
        prepared: PreparedChildRunAcceptance,
    ) -> ChildRunAcceptanceReceipt:
        session_scope = await self._authorize_parent(prepared)
        parent_state = prepared.parent.state
        await self._publish_initial(prepared)

        async def accept(database: AsyncSession, online: OnlineEvidence) -> None:
            parent = await self._lock_parent(database, prepared, session_scope)
            parent_resource = parent.to_resource()
            edge = next(
                edge
                for edge in parent_state.envelope.effective_agent_config.resolved_subagents
                if edge.name == prepared.relationship.subagent_name
            )
            choice = await child_environment_choice(database, parent=parent_resource, policy=edge.environment)
            session_labels = await database.scalar(
                select(SessionRecord.labels).where(
                    SessionRecord.id == parent.session_id,
                    SessionRecord.organization_id == parent.organization_id,
                )
            )
            if session_labels is None:
                raise ChildRunAcceptanceError("child_run_session_missing", "Parent Session was not found")
            inherited_labels = merge_labels(session_labels)
            child_run = prepared.run.model_copy(update={"labels": inherited_labels})
            database.add(thread_record(prepared.thread.model_copy(update={"labels": inherited_labels})))
            await self._insert_child(
                database,
                online,
                prepared,
                run=child_run,
                workspace_id=session_scope.workspace_id,
                intent=ExplicitEnvironment(choice),
                source_run_id=parent.id,
            )

        try:
            await self._online.commit(accept)
        except IntegrityError as error:
            raise ChildRunAcceptanceError(
                "child_run_acceptance_conflict",
                "Child Run acceptance lost a concurrent mutation",
            ) from error
        return _receipt(prepared.relationship, prepared.run.session_id)

    @authorization_operation
    async def accept_resume(
        self,
        prepared: PreparedChildRunResume,
    ) -> ChildRunAcceptanceReceipt:
        """Accept one linked continuation in the retained child Thread."""

        session_scope = await self._authorize_parent(prepared)
        source_state = prepared.source_state
        await self._publish_initial(prepared)

        async def accept(database: AsyncSession, online: OnlineEvidence) -> None:
            parent = await self._lock_parent(database, prepared, session_scope)
            parent_resource = parent.to_resource()
            child_thread = await database.scalar(
                select(ThreadRecord)
                .where(
                    ThreadRecord.organization_id == prepared.run.organization_id,
                    ThreadRecord.id == prepared.run.thread_id,
                )
                .with_for_update()
            )
            source_run = await database.scalar(
                select(RunRecord)
                .where(
                    RunRecord.organization_id == prepared.run.organization_id,
                    RunRecord.id == prepared.source.run.id,
                )
                .with_for_update()
            )
            source_relationship = await database.scalar(
                select(ChildRunRelationshipRecord)
                .where(
                    ChildRunRelationshipRecord.organization_id == prepared.run.organization_id,
                    ChildRunRelationshipRecord.id == prepared.source.relationship.id,
                )
                .with_for_update()
            )
            source_parent_run = (
                parent
                if parent.id == prepared.source.parent_run.id
                else await database.scalar(
                    select(RunRecord)
                    .where(
                        RunRecord.organization_id == prepared.run.organization_id,
                        RunRecord.id == prepared.source.parent_run.id,
                    )
                    .with_for_update()
                )
            )
            _validate_locked_resume_source(
                prepared,
                current_parent=parent_resource,
                child_thread=child_thread,
                source_run=source_run,
                source_relationship=source_relationship,
                source_parent_run=source_parent_run,
                source_state=source_state,
            )
            assert source_run is not None and source_parent_run is not None
            await _reauthorize_resume_source(
                database,
                parent=parent_resource,
                source_parent=source_parent_run.to_resource(),
                source_child=source_run.to_resource(),
                workspace_id=session_scope.workspace_id,
            )
            assert child_thread is not None
            await self._insert_child(
                database,
                online,
                prepared,
                run=prepared.run.model_copy(update={"labels": merge_labels(source_run.labels)}),
                workspace_id=session_scope.workspace_id,
                intent=RetainedRunEnvironment(source_run.id, source_run.thread_id),
                source_run_id=source_run.id,
            )
            child_thread.version += 1
            child_thread.current_run_id = prepared.run.id
            child_thread.updated_at = assume_utc(self._clock())
            await database.flush()

        try:
            await self._online.commit(accept)
        except IntegrityError as error:
            raise ChildRunAcceptanceError(
                "child_run_resume_conflict",
                "Child Run continuation lost a concurrent mutation",
            ) from error
        return _receipt(prepared.relationship, prepared.run.session_id)

    async def _authorize_parent(
        self,
        prepared: PreparedChildRunAcceptance | PreparedChildRunResume,
    ) -> SessionScope:
        async with short_session(self._sessions) as database:
            scope = SessionScope.from_record(await _require_session(database, prepared.parent.run))
            await _reauthorize(
                database,
                parent=prepared.parent.run,
                child=prepared.run,
                child_definition_id=prepared.child_definition_id,
                workspace_id=scope.workspace_id,
            )
        return scope

    async def _lock_parent(
        self,
        database: AsyncSession,
        prepared: PreparedChildRunAcceptance | PreparedChildRunResume,
        session_scope: SessionScope,
    ) -> RunRecord:
        parent, _, _ = await lock_attempt_authority(
            database, prepared.parent.authority, self._clock, lock_inbox_origins=True
        )
        if (
            parent.organization_id != session_scope.organization_id
            or parent.session_id != session_scope.id
            or parent.to_resource().authority_principal != prepared.parent.run.authority_principal
        ):
            raise ChildRunAcceptanceError("child_run_parent_conflict", "Child Run parent scope changed")
        require_frozen_subagent_edge(
            parent.to_resource(), prepared.parent.state.envelope, prepared.relationship.subagent_name
        )
        await _reauthorize(
            database,
            parent=parent.to_resource(),
            child=prepared.run,
            child_definition_id=prepared.child_definition_id,
            workspace_id=session_scope.workspace_id,
        )
        return parent

    async def _insert_child(
        self,
        database: AsyncSession,
        online: OnlineEvidence,
        prepared: PreparedChildRunAcceptance | PreparedChildRunResume,
        *,
        run: Run,
        workspace_id: str,
        intent: EnvironmentIntent,
        source_run_id: str,
    ) -> None:
        child = await add_run_with_environment(
            database, online=online, run=run, state=prepared.state, workspace_id=workspace_id, intent=intent
        )
        await self._bindings.finalize(database, run, source_run_id=source_run_id)
        await self._lifecycle.append_accepted_run_lifecycle(database, child)
        database.add(child_run_relationship_record(prepared.relationship, organization_id=run.organization_id))

    async def _publish_initial(
        self,
        prepared: PreparedChildRunAcceptance | PreparedChildRunResume,
    ) -> None:
        try:
            await self._states.create(prepared.run.organization_id, prepared.state)
        except StaleStateWriter as error:
            raise ChildRunAcceptanceError(
                "child_run_state_conflict",
                "Child Run state key already contains accepted state",
            ) from error


def _validate_locked_resume_source(
    prepared: PreparedChildRunResume,
    *,
    current_parent: Run,
    child_thread: ThreadRecord | None,
    source_run: RunRecord | None,
    source_relationship: ChildRunRelationshipRecord | None,
    source_parent_run: RunRecord | None,
    source_state: StoredRunState,
) -> None:
    if child_thread is None or source_run is None or source_relationship is None or source_parent_run is None:
        raise ChildRunAcceptanceError(
            "child_run_resume_source_missing",
            "Retained child continuation source was not found",
        )
    source = source_run.to_resource()
    relationship = source_relationship.to_resource()
    source_parent = source_parent_run.to_resource()
    if (
        source.agent_id != prepared.source.run.agent_id
        or source.agent_revision_id != prepared.source.run.agent_revision_id
        or source.effective_agent_config_digest != prepared.source.run.effective_agent_config_digest
        or source.connection_selections != prepared.source.run.connection_selections
    ):
        raise ChildRunAcceptanceError(
            "child_run_resume_config_conflict", "Child continuation changed its retained execution snapshot"
        )
    if (
        child_thread.version != prepared.source.thread.version
        or child_thread.session_id != prepared.run.session_id
        or child_thread.role != "child"
        or child_thread.origin_kind != "child"
        or child_thread.current_run_id != source.id
        or child_thread.head_run_id != source.id
        or relationship.id != prepared.source.relationship.id
        or relationship.parent_run_id != prepared.source.parent_run.id
        or relationship.subagent_name != prepared.relationship.subagent_name
        or relationship.child_thread_id != child_thread.id
        or relationship.child_run_id != source.id
        or source_parent.id != prepared.source.parent_run.id
        or not child_relationship_is_visible(
            relationship,
            origin_parent=source_parent,
            requesting_parent=current_parent,
        )
        or source.status.value != "completed"
        or source.thread_id != child_thread.id
    ):
        raise ChildRunAcceptanceError(
            "child_run_resume_source_conflict",
            "Retained child execution is no longer the selected resumable head",
        )
    sealed = source.sealed_state
    if sealed is None or (
        sealed.digest_sha256,
        sealed.size_bytes,
        sealed.content_type,
        sealed.envelope_schema_version,
        sealed.harness_schema_version,
        sealed.checkpoint_seq,
    ) != (
        source_state.digest_sha256,
        len(source_state.body),
        RUN_STATE_CONTENT_TYPE,
        source_state.envelope.schema_version,
        source_state.envelope.harness_schema_version,
        source_state.envelope.checkpoint_seq,
    ):
        raise ChildRunAcceptanceError(
            "child_run_resume_state_conflict",
            "Retained child source state does not match its sealed Run reference",
        )


async def _reauthorize_resume_source(
    database: AsyncSession,
    *,
    parent: Run,
    source_parent: Run,
    source_child: Run,
    workspace_id: str,
) -> None:
    try:
        await authorize_parent_child_action(
            database,
            parent=parent,
            source_parent_agent_ids=(source_parent.agent_id,),
            child_agent_ids=(source_child.agent_id,),
            workspace_id=workspace_id,
            action=WorkspaceAction.run_continue,
        )
    except ChildRunAuthorizationError as error:
        raise ChildRunAcceptanceError(
            "child_run_authorization_denied",
            "Persisted parent Principal is no longer authorized to continue the retained child Agent",
        ) from error


async def _require_session(database: AsyncSession, parent: Run) -> SessionRecord:
    record = await database.scalar(
        select(SessionRecord).where(
            SessionRecord.organization_id == parent.organization_id,
            SessionRecord.id == parent.session_id,
        )
    )
    if record is None:
        raise ChildRunAcceptanceError("child_run_session_missing", "Parent Session was not found")
    return record


async def _reauthorize(
    database: AsyncSession,
    *,
    parent: Run,
    child: Run,
    child_definition_id: str,
    workspace_id: str,
) -> None:
    actor = AuthenticatedActor(
        principal=parent.authority_principal,
        auth_method="run_authority",
        credential_id=f"run_{parent.id}",
        boundary_workspace_id=workspace_id,
        request_id=parent.id,
    )
    try:
        await authorize_agent(
            database,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=parent.agent_id,
            action=WorkspaceAction.agent_invoke,
        )
        await authorize_agent(
            database,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=child.agent_id,
            action=WorkspaceAction.agent_invoke,
        )
        revision_digest = await database.scalar(
            select(AgentRevisionRecord.content_digest)
            .join(
                AgentRecord,
                (AgentRecord.organization_id == AgentRevisionRecord.organization_id)
                & (AgentRecord.workspace_id == AgentRevisionRecord.workspace_id)
                & (AgentRecord.id == AgentRevisionRecord.agent_id),
            )
            .where(
                AgentRecord.organization_id == parent.organization_id,
                AgentRecord.workspace_id == workspace_id,
                AgentRecord.id == child.agent_id,
                AgentRecord.enabled.is_(True),
                AgentRecord.archived_at.is_(None),
                AgentRevisionRecord.id == child.agent_revision_id,
            )
        )
        if revision_digest is None or child_definition_id != f"agent-config-{revision_digest[:24]}":
            raise ChildRunAcceptanceError(
                "child_run_revision_unavailable",
                "Frozen child Agent revision is no longer executable",
            )
    except AuthorizationError as error:
        raise ChildRunAcceptanceError(
            "child_run_authorization_denied",
            "Persisted parent Principal is no longer authorized to invoke the child Agent",
        ) from error


def _receipt(relationship: ChildRunRelationship, session_id: str) -> ChildRunAcceptanceReceipt:
    return ChildRunAcceptanceReceipt(
        relationship=relationship,
        session_id=session_id,
        child_thread_id=relationship.child_thread_id,
        child_run_id=relationship.child_run_id,
    )


__all__ = [
    "ChildRunAcceptanceError",
    "ChildRunAcceptanceReceipt",
    "ChildRunAcceptanceService",
]
