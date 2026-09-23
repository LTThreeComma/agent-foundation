"""Workspace-authorized native application and action catalogue discovery."""

import asyncio
from typing import Annotated

from a13n_harness.providers.connector.contracts import ConnectorKey, DiscoveredConnector
from a13n_harness.providers.connector.validation import required_object, required_string
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.tools import ToolInfo
from a13n_service.resources.connections.managed import definition
from a13n_service.resources.connections.managed_catalog import existing_configurations
from a13n_service.resources.connections.managed_transport import open_project, project_credential, tool_definitions
from a13n_service.resources.connections.schemas import ManagedCredential
from a13n_service.resources.connections.service import get_row, resolve
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.grants import principal_for, workspace_scope
from a13n_service.tenancy.routes import current_principal

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/connection-catalog", tags=["connections"])


class CatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: ManagedCredential | None = Field(default=None, repr=False)
    connection_id: str | None = Field(default=None, min_length=1, max_length=72)
    app: ConnectorKey | None = None
    toolkit_version: str | None = Field(default=None, pattern=r"^[0-9]{8}_[0-9]{2}$")

    @model_validator(mode="after")
    def credential_source(self) -> "CatalogRequest":
        if (self.credential is None) == (self.connection_id is None):
            raise ValueError("Supply a saved Connection or a project credential")
        return self


class CatalogView(BaseModel):
    apps: list[DiscoveredConnector]
    tools: list[ToolInfo]
    toolkit_version: str | None


@router.post("/composio", response_model=CatalogView)
async def composio_catalog(
    request: Request,
    response: Response,
    workspace_id: str,
    body: CatalogRequest,
    actor: Annotated[Principal, Depends(current_principal)],
) -> CatalogView:
    storage = request.app.state.storage
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        selected = await resolve(session, actor, scope, body.connection_id, verb="read") if body.connection_id else None
        if selected:
            credential = project_credential(selected, request.app.state.key_ring)
        else:
            assert body.credential is not None
            credential = {"api_key": body.credential.api_key.get_secret_value()}

    async def before(outbound):
        async with short_session(storage) as session:
            current = await principal_for(session, actor.id, confinement=actor.confinement)
            assert scope.workspace_id is not None
            await workspace_scope(session, current, scope.workspace_id, "write")
            if selected:
                row = await get_row(session, selected.workspace_id, selected.id)
                if row.version != selected.version or not row.enabled:
                    raise ServiceError("conflict", "Connection changed during catalogue discovery")

    response.headers["Cache-Control"] = "no-store"
    async with (
        asyncio.timeout(30),
        open_project(
            credential,
            definition(request.app.state.tool_catalog),
            policy=request.app.state.endpoint_policy,
            before_request=before,
        ) as provider,
    ):
        if body.app is None:
            apps = [existing_configurations(item) for item in await provider.discover_connectors()]
            return CatalogView(apps=apps, tools=[], toolkit_version=None)
        app = existing_configurations(await provider.discover_connector(body.app))
        if app.unavailable_reason:
            raise ServiceError(
                "invalid_argument", "Create an enabled authentication configuration in Composio Dashboard"
            )
        version = body.toolkit_version or required_string(
            required_object(required_object(app.setup_schema["properties"])["toolkit_version"]), "const"
        )
        tools = await tool_definitions(provider, body.app, version)
        required_object(required_object(app.setup_schema["properties"])["toolkit_version"])["const"] = version
        return CatalogView(
            apps=[app],
            toolkit_version=version,
            tools=[
                ToolInfo(name=tool.key, description=tool.description, input_schema=tool.input_schema) for tool in tools
            ],
        )


async def provider_error_response(request: Request, error: Exception):
    from a13n_service.infra.http import service_error_response

    return await service_error_response(
        request, ServiceError("unavailable", "Managed provider request could not be completed")
    )
