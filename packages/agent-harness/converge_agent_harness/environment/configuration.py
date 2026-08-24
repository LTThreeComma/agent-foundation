"""Configuration for the optional dynamic Environment projection."""

from pydantic import BaseModel, ConfigDict, Field


class DynamicEnvironmentConfiguration(BaseModel):
    """Definition-selected tool surfaces and finite run-local projection limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file_tools: bool = True
    shell_tools: bool = True
    process_tools: bool = True
    port_tools: bool = False
    max_topology_bindings: int = Field(gt=0, le=1024)
    max_topology_bytes: int = Field(ge=256, le=1024 * 1024)
    max_reference_entries: int = Field(gt=0, le=100_000)


__all__ = ["DynamicEnvironmentConfiguration"]
