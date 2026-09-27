"""Caller selections normalized after command replay lookup, before any domain work."""

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.resources.agents.inputs import OverrideInput
from a13n_service.resources.agents.resolve_inputs import collect_config, override_ids
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.memories.inputs import MemoryMountsInput, mount_ids
from a13n_service.resources.memories.tables import MemoryRow
from a13n_service.resources.references import PathReference, Reference, ReferenceBatch, resolved_model
from a13n_service.runs.schemas import (
    EntryUpdate,
    ForkFields,
    Message,
    MessageFields,
    NewThreadFields,
    RunOptions,
    SessionFilters,
)

OptionsInput = RunOptions[OverrideInput]
EntryUpdateInput = EntryUpdate[OptionsInput]


class MessageInput(MessageFields):
    agent: Reference
    options: OptionsInput = Field(default_factory=OptionsInput)


class NewThreadInput(MessageInput, NewThreadFields):
    memories: MemoryMountsInput = ()


class ForkInput(MessageInput, ForkFields):
    memories: MemoryMountsInput = ()


def options_ids(batch: ReferenceBatch, options: OptionsInput) -> RunOptions:
    return RunOptions(
        **options.model_dump(exclude={"overrides"}),
        overrides=None if options.overrides is None else override_ids(batch, options.overrides),
    )


async def resolve_message[M: Message](
    session: AsyncSession,
    workspace_id: str,
    body: MessageInput,
    model: type[M],
) -> M:
    batch = ReferenceBatch()
    batch.add(AgentRow, body.agent)
    if body.options.overrides is not None:
        collect_config(batch, body.options.overrides)
    if isinstance(body, NewThreadInput | ForkInput):
        for mount in body.memories:
            batch.add(MemoryRow, mount.memory)
    await batch.resolve(session, workspace_id)
    values = body.model_dump(exclude={"agent", "options", "memories"})
    values["agent_id"] = batch.id(AgentRow, body.agent)
    values["options"] = options_ids(batch, body.options)
    if isinstance(body, NewThreadInput | ForkInput):
        values["memories"] = [mount_ids(batch, mount) for mount in body.memories]
    return resolved_model(model, values)


async def resolve_edit(session: AsyncSession, workspace_id: str, body: EntryUpdateInput) -> EntryUpdate:
    batch = ReferenceBatch()
    if body.options is not None and body.options.overrides is not None:
        collect_config(batch, body.options.overrides)
    await batch.resolve(session, workspace_id)
    values = body.model_dump(exclude={"options"}, exclude_unset=True)
    if "options" in body.model_fields_set:
        values["options"] = None if body.options is None else options_ids(batch, body.options)
    return resolved_model(EntryUpdate, values)


class SessionQueryInput(SessionFilters):
    agent: PathReference | None = None
