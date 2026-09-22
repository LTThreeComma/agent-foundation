"""One declared Connection scope for revisions, submission, execution and steers."""

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.connections.schemas import ConnectionSelection
from a13n_service.resources.connections.service import ResolvedConnection, authentication_headers, resolve
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Scope


def connection_scope(config: AgentConfig) -> dict[str, ConnectionSelection]:
    return {selection.connection_id: selection for selection in config.connections}


def validate_tools(selected: ResolvedConnection, selection: ConnectionSelection) -> None:
    if selected.config.tools is not None and not set(selection.tools) <= set(selected.config.tools):
        raise ServiceError(
            "disabled", "Agent tools are no longer selected by the Connection", {"connection_id": selected.id}
        )


def check_collisions(selected: ResolvedConnection, headers: Mapping[str, str], keys: KeyRing) -> None:
    if set(headers) & authentication_headers(selected, keys).keys():
        raise ServiceError(
            "invalid_argument", "Caller headers collide with Connection authentication", {"connection_id": selected.id}
        )


async def validate_context(
    session: AsyncSession,
    actor: Principal,
    scope: Scope,
    config: AgentConfig,
    headers: Mapping[str, Mapping[str, str]],
    *,
    keys: KeyRing,
    authority: ExecutionAuthority | None = None,
) -> None:
    available = connection_scope(config)
    if not headers.keys() <= available.keys():
        raise ServiceError("invalid_argument", "Caller context names a Connection outside the selected Agent")
    for connection_id, values in headers.items():
        selected = await resolve(session, actor, scope, connection_id, verb="run", authority=authority)
        if selected.type != "mcp":
            raise ServiceError("invalid_argument", "Caller MCP context requires an MCP Connection")
        validate_tools(selected, available[connection_id])
        check_collisions(selected, values, keys)


def effective_headers(config: AgentConfig, headers: Mapping[str, Mapping[str, str]]) -> dict[str, dict[str, str]]:
    return {connection: dict(headers[connection]) for connection in connection_scope(config) if headers.get(connection)}
