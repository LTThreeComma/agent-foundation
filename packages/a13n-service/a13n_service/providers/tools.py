"""Typed tool-source definitions reuse the Harness provider catalog."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Protocol

import httpx2
from a13n_harness import AgentContext
from a13n_harness.mcp import MCPHeadersFactory
from a13n_harness.providers.connector import ConnectorProviderDefinition
from a13n_harness.providers.definition import ProviderDefinition
from pydantic import BaseModel, JsonValue
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset

MAX_TOOLS = 128
INITIALIZATION_SECONDS = 10
CALL_SECONDS = 30
RESPONSE_BYTES = 262144


class ToolInfo(BaseModel):
    name: str
    description: str | None
    input_schema: dict[str, JsonValue]
    permission_id: str | None = None


class ToolsetWrapper(Protocol):
    def __call__(self, tools: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]: ...


class ToolSource(Protocol):
    async def discover(self) -> list[ToolInfo]: ...

    def open(
        self, headers: MCPHeadersFactory, wrapper: ToolsetWrapper, *, run_id: str
    ) -> AbstractCapability[AgentContext]: ...


class ToolSourceFactory[C: BaseModel](Protocol):
    def __call__(self, configuration: C, *, source_id: str, client: httpx2.AsyncClient) -> ToolSource: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolSourceDefinition[C: BaseModel](ProviderDefinition[C, BaseModel]):
    DOMAIN: ClassVar[str] = "tool"
    factory: ToolSourceFactory[C]

    def bind(self, configuration: Mapping[str, JsonValue], *, source_id: str, client: httpx2.AsyncClient) -> ToolSource:
        return self.factory(self.configuration_model.model_validate(configuration), source_id=source_id, client=client)


type ConnectionProvider = ToolSourceDefinition | ConnectorProviderDefinition
