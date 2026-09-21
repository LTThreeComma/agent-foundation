"""Agent-owned integration with Connectivity selection resolution."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.selection_domain import ConnectionRunSelection
from a13n_service.connectivity.selection_resolution import (
    ConnectivitySelectionError,
    ConnectivitySelectionResolver,
)
from a13n_service.iam import AuthenticatedActor

from .domain import ConnectionToolSelection
from .errors import agent_revision_not_executable


class _ConnectivityConfig(Protocol):
    @property
    def connection_tools(self) -> tuple[ConnectionToolSelection, ...]: ...


async def prepare_invocation_connectivity(
    resolver: ConnectivitySelectionResolver,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    config: _ConnectivityConfig,
    session: AsyncSession | None = None,
) -> tuple[ConnectionRunSelection, ...]:
    try:
        return await resolver.prepare(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connection_tools=config.connection_tools,
            session=session,
        )
    except ConnectivitySelectionError as error:
        raise agent_revision_not_executable(error.code) from error
