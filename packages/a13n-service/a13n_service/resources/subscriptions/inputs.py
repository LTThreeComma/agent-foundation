"""Normalize subscription selectors before storing durable webhook filters."""

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.references import Reference, ReferenceBatch
from a13n_service.resources.subscriptions.schemas import (
    FilterFields,
    SubscriptionCreate,
    SubscriptionCreateFields,
    SubscriptionUpdate,
    SubscriptionUpdateFields,
)


class FilterInput(FilterFields):
    agent: Reference | None = None


class SubscriptionCreateInput(SubscriptionCreateFields):
    filter: FilterInput = Field(default_factory=FilterInput)


class SubscriptionUpdateInput(SubscriptionUpdateFields):
    filter: FilterInput | None = None


async def normalize[T: SubscriptionCreate | SubscriptionUpdate](
    session: AsyncSession, workspace_id: str, body: SubscriptionCreateInput | SubscriptionUpdateInput, model: type[T]
) -> T:
    values = body.model_dump(exclude_unset=True)
    if body.filter is not None:
        batch = ReferenceBatch()
        batch.add(AgentRow, body.filter.agent)
        await batch.resolve(session, workspace_id)
        values["filter"] = {
            **body.filter.model_dump(exclude={"agent"}),
            "agent_id": None if body.filter.agent is None else batch.id(AgentRow, body.filter.agent),
        }
    return model.model_validate(values)
