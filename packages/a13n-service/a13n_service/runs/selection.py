"""Detached execution selection and exact parent history loading."""

import asyncio

from a13n_harness import HarnessState

from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.agents.tables import AgentRevisionRow
from a13n_service.runs.attempts import lock_authority
from a13n_service.runs.policy import ResolvedModel, authorize_execution, resolve_model
from a13n_service.runs.schemas import AgentSelection, AttemptClaim, Checkpoint, ExecutionSelection, RunView, SnapshotRef
from a13n_service.runs.snapshots import object_key
from a13n_service.runs.tables import RunRow


async def load(storage: Storage, claim: AttemptClaim) -> tuple[ExecutionSelection, ResolvedModel]:
    async with transaction(storage) as session:
        run, _, _ = await lock_authority(session, claim)
        principal = await authorize_execution(session, run)
        revision = await session.get(AgentRevisionRow, run.agent_revision_id)
        if revision is None or revision.agent_id != run.agent_id:
            raise ServiceError("conflict", "Accepted agent revision is unavailable")
        agent = AgentSelection(
            agent_id=run.agent_id,
            revision_id=revision.id,
            digest=revision.digest,
            config=AgentConfig.model_validate(revision.config),
        )
        parent = await session.get(RunRow, run.parent_run_id) if run.parent_run_id else None
        if parent is not None and (
            parent.workspace_id != run.workspace_id or parent.status not in {"completed", "waiting"}
        ):
            raise ServiceError("conflict", "Accepted parent has no continuation state")
        if run.parent_run_id is not None and parent is None:
            raise ServiceError("conflict", "Accepted parent is unavailable")
        selected = ExecutionSelection.model_validate(
            {
                "run": RunView.model_validate(run),
                "agent": agent,
                "authority": run.authority,
                "options": run.options,
                "parent_checkpoint": parent.sealed_checkpoint if parent else None,
                "parent_waiting": parent.pending if parent is not None and parent.status == "waiting" else None,
            }
        )
        return selected, await resolve_model(session, run, agent.config.model_id, principal)


async def initial_state(
    objects: LocalObjects, claim: AttemptClaim, selected: ExecutionSelection, *, timeout: float
) -> HarnessState:
    parent_id = selected.run.parent_run_id
    if parent_id is None:
        return HarnessState.new()
    if selected.parent_checkpoint is None:
        raise ServiceError("conflict", "Accepted parent has no sealed checkpoint")
    reference = SnapshotRef.model_validate(selected.parent_checkpoint)
    async with asyncio.timeout(timeout):
        value = await objects.read(object_key(claim.organization_id, parent_id, "state"))
    if (
        value is None
        or value.digest != reference.digest
        or value.version != reference.version
        or len(value.content) != reference.size
    ):
        raise ServiceError("unavailable", "Sealed parent checkpoint is unavailable")
    checkpoint = Checkpoint.model_validate_json(value.content)
    if (
        checkpoint.organization_id != claim.organization_id
        or checkpoint.run_id != parent_id
        or checkpoint.sequence != reference.sequence
        or checkpoint.attempt_id != reference.attempt_id
        or checkpoint.format != reference.format
    ):
        raise ServiceError("conflict", "Sealed parent checkpoint identity is invalid")
    return checkpoint.state
