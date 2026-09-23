"""Service selection for native Composio applications and pinned actions."""

from typing import Annotated

from a13n_harness.providers.connector.composio.configuration import ComposioSetup
from a13n_harness.providers.connector.contracts import ConnectorKey
from pydantic import Field, StringConstraints, field_validator

from a13n_service.providers.tools import MAX_TOOLS

ActionName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]


class ComposioConfig(ComposioSetup):
    app: ConnectorKey
    actions: tuple[ActionName, ...] = Field(min_length=1, max_length=MAX_TOOLS)

    @field_validator("auth_config_id")
    @classmethod
    def existing_auth_config(cls, value: str) -> str:
        if value.startswith("create:"):
            raise ValueError("Select an existing enabled authentication configuration")
        return value

    @field_validator("actions")
    @classmethod
    def unique_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Action names must be unique")
        return value

    def setup(self) -> dict:
        return self.model_dump(mode="json", exclude={"app", "actions"})
