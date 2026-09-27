"""Agent HTTP input normalization; domain operations receive canonical ID-only configurations."""

from typing import Annotated

from fastapi import Depends

from a13n_service.infra.db import short_session
from a13n_service.resources.agents.inputs import AgentCreateInput, AgentRevisionCreateInput, AgentValidateInput
from a13n_service.resources.agents.resolve_inputs import collect_config, config_ids, resolve_config
from a13n_service.resources.agents.schemas import AgentCreate, AgentRevisionCreate, AgentValidate
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.references import ReferenceBatch
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Workspace


async def create_body(body: AgentCreateInput, runtime: CurrentRuntime, workspace: Workspace) -> AgentCreate:
    async with short_session(runtime.storage) as session:
        config = await resolve_config(session, workspace.workspace_id, body.config)
    return AgentCreate(**body.model_dump(exclude={"config"}), config=config)


async def revision_body(
    body: AgentRevisionCreateInput, runtime: CurrentRuntime, workspace: Workspace
) -> AgentRevisionCreate:
    async with short_session(runtime.storage) as session:
        config = await resolve_config(session, workspace.workspace_id, body.config)
    return AgentRevisionCreate(**body.model_dump(exclude={"config"}), config=config)


async def validate_body(body: AgentValidateInput, runtime: CurrentRuntime, workspace: Workspace) -> AgentValidate:
    batch = ReferenceBatch()
    collect_config(batch, body.config)
    batch.add(AgentRow, body.agent)
    async with short_session(runtime.storage) as session:
        await batch.resolve(session, workspace.workspace_id)
    return AgentValidate(
        config=config_ids(batch, body.config), agent_id=None if body.agent is None else batch.id(AgentRow, body.agent)
    )


CreateBody = Annotated[AgentCreate, Depends(create_body)]
RevisionBody = Annotated[AgentRevisionCreate, Depends(revision_body)]
ValidateBody = Annotated[AgentValidate, Depends(validate_body)]
