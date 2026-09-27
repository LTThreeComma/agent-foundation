"""The resource runtime and workspace-scoped path resolution for HTTP adapters."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Path, Request

from a13n_service.resources.references import KeyedRow, PathReference, resolve_id
from a13n_service.resources.runtime import Runtime
from a13n_service.tenancy.requests import Workspace


async def current_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


CurrentRuntime = Annotated[Runtime, Depends(current_runtime)]


def resource_id(table: type[KeyedRow], parameter: str) -> Callable[..., Awaitable[str]]:
    """Resolve a resource path exactly once; the route passes only its ID to domain operations."""

    async def resolve(
        runtime: CurrentRuntime,
        workspace: Workspace,
        reference: Annotated[PathReference, Path(alias=parameter)],
    ) -> str:
        return await resolve_id(runtime.storage, workspace.workspace_id, table, reference)

    return resolve
