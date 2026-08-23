"""A safe, pre-entry-inert Environment provider factory backed by Direct Local."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from converge_agent_harness import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalEnvironmentProviderBinding,
    DirectLocalRootConfiguration,
    EnvironmentProviderBinding,
    EnvironmentProviderFactory,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

PROVIDER_KEY = "example.workspace"


class WorkspaceEnvironmentConfiguration(BaseModel):
    """The JSON configuration accepted by the example provider factory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: str = Field(min_length=1, max_length=4096)
    environment_id: str = Field(min_length=1, max_length=128)
    read_only: bool = True

    @field_validator("root")
    @classmethod
    def _valid_root(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("root cannot contain NUL")
        return value


class WorkspaceEnvironmentProviderFactory(EnvironmentProviderFactory):
    """Create fresh Direct Local bindings without entering or allocating them."""

    @classmethod
    def provider_key(cls) -> str:
        return PROVIDER_KEY

    def create_provider_binding(
        self,
        configuration: Mapping[str, JsonValue],
    ) -> EnvironmentProviderBinding:
        try:
            parsed = WorkspaceEnvironmentConfiguration.model_validate(dict(configuration), strict=True)
        except ValidationError as exc:
            # The catalog replaces this cause with a stable, sanitized public error.
            raise ValueError("Invalid example.workspace configuration.") from exc

        return DirectLocalEnvironmentProviderBinding(
            DirectLocalEnvironmentConfiguration(
                environment_id=parsed.environment_id,
                root=DirectLocalRootConfiguration(
                    path=Path(parsed.root),
                    ownership="caller_owned",
                    read_only=parsed.read_only,
                ),
            )
        )
