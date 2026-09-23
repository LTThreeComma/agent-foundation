"""Service catalogue projection: existing auth configurations and non-secret setup only."""

from copy import deepcopy

from a13n_harness.providers.connector.contracts import DiscoveredConnector
from a13n_harness.providers.connector.validation import required_object, required_string
from jsonschema import Draft202012Validator
from pydantic import JsonValue

from a13n_service.infra.errors import ServiceError
from a13n_service.providers.composio import ComposioConfig
from a13n_service.resources.connections.managed_transport import actions


def existing_configurations(connector: DiscoveredConnector) -> DiscoveredConnector:
    schema = deepcopy(connector.setup_schema)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return connector
    selection = properties.get("auth_config_id")
    if not isinstance(selection, dict):
        raise ServiceError("unavailable", "Managed authentication catalogue is invalid")
    choices = selection.get("oneOf")
    if not isinstance(choices, list):
        raise ServiceError("unavailable", "Managed authentication catalogue is invalid")
    filtered: list[JsonValue] = []
    allowed: set[str] = set()
    for choice in choices:
        value = required_object(choice)
        key = required_string(value, "const")
        if not key.startswith("create:"):
            filtered.append(value)
            allowed.add(key)
    selection["oneOf"] = filtered
    if selection.get("default") not in allowed:
        selection.pop("default", None)
    constraints: list[JsonValue] = []
    conditions = schema.get("allOf", [])
    if not isinstance(conditions, list):
        raise ServiceError("unavailable", "Managed setup catalogue is invalid")
    for item in conditions:
        condition = required_object(item)
        restriction = required_object(required_object(required_object(condition["if"])["properties"])["auth_config_id"])
        offered = restriction["enum"]
        if not isinstance(offered, list):
            raise ServiceError("unavailable", "Managed setup catalogue is invalid")
        remaining: list[JsonValue] = [value for value in offered if isinstance(value, str) and value in allowed]
        if remaining:
            restriction["enum"] = remaining
            constraints.append(condition)
    if "allOf" in schema:
        schema["allOf"] = constraints
    return connector.model_copy(
        update={
            "setup_schema": schema if filtered else {"not": {}},
            "credential_schemas": {},
            "unavailable_reason": connector.unavailable_reason
            if filtered
            else "Create an enabled auth config in Composio Dashboard, then refresh applications.",
        }
    )


async def validate(provider, config: ComposioConfig) -> None:
    connector = existing_configurations(await provider.discover_connector(config.app))
    if connector.unavailable_reason:
        raise ServiceError("invalid_argument", "Managed application has no available authentication configuration")
    schema = connector.setup_schema
    # Native discovery verifies the exact pin before accepting an older saved version.
    await actions(provider, config)
    required_object(required_object(schema["properties"])["toolkit_version"])["const"] = config.toolkit_version
    if not Draft202012Validator(schema).is_valid(config.setup()):
        raise ServiceError(
            "invalid_argument", "Managed setup fields do not match the selected authentication configuration"
        )


async def validate_saved(storage, actor, selected, *, keys, policy, catalog) -> None:
    from a13n_service.infra.db import short_session
    from a13n_service.resources.connections.managed import definition
    from a13n_service.resources.connections.managed_transport import open_provider
    from a13n_service.resources.connections.service import get_row
    from a13n_service.tenancy.grants import principal_for, workspace_scope

    async def before(request):
        async with short_session(storage) as session:
            current = await principal_for(session, actor.id, confinement=actor.confinement)
            await workspace_scope(session, current, selected.workspace_id, "write")
            if selected.version:
                connection = await get_row(session, selected.workspace_id, selected.id)
                if connection.version != selected.version:
                    raise ServiceError("conflict", "Connection changed during catalogue validation")

    async with open_provider(
        selected, definition(catalog), keys=keys, policy=policy, before_request=before
    ) as provider:
        await validate(provider, selected.config)
