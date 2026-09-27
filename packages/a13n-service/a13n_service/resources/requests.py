"""The resource runtime and workspace-scoped path resolution for HTTP adapters."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Path, Request

from a13n_service.resources.references import IdReference, KeyedRow, PathReference, parse_reference, resolve_id
from a13n_service.resources.runtime import Runtime
from a13n_service.tenancy.requests import Actor, Workspace


async def current_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


CurrentRuntime = Annotated[Runtime, Depends(current_runtime)]


def resource_id(table: type[KeyedRow], parameter: str) -> Callable[..., Awaitable[str]]:
    """Resolve keys at the boundary; domain operations already look up and authorize canonical IDs."""

    async def resolve(
        runtime: CurrentRuntime,
        actor: Actor,
        workspace: Workspace,
        reference: Annotated[PathReference, Path(alias=parameter)],
    ) -> str:
        parsed = parse_reference(reference)
        if isinstance(parsed, IdReference):
            return parsed.id
        return await resolve_id(runtime.storage, actor, workspace.workspace_id, table, reference)

    return resolve
