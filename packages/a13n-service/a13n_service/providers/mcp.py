"""Remote MCP transport assembled through the existing Harness capability."""

import hashlib
from collections.abc import Mapping
from typing import Annotated, Any

import httpx2
from a13n_harness import AgentContext
from a13n_harness.mcp import ContextualMCP, MCPHeadersFactory
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from pydantic_ai import RunContext
from pydantic_ai.mcp import CallToolFunc, MCPToolset, ProcessToolCallback, ToolResult
from pydantic_ai.toolsets import AbstractToolset

from a13n_service.providers.tools import (
    CALL_SECONDS,
    INITIALIZATION_SECONDS,
    MAX_TOOLS,
    ToolInfo,
    ToolsetWrapper,
    ToolSourceDefinition,
)

ToolName = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class MCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str = Field(min_length=1, max_length=2048)
    tools: tuple[ToolName, ...] | None = Field(default=None, max_length=MAX_TOOLS)
    recovery_retry_safe_tools: tuple[ToolName, ...] = Field(default=(), max_length=MAX_TOOLS)

    @model_validator(mode="after")
    def recovery_selection(self) -> "MCPConfig":
        if self.recovery_retry_safe_tools and (
            self.tools is None or not set(self.recovery_retry_safe_tools) <= set(self.tools)
        ):
            raise ValueError("Recovery declarations require explicitly selected tools")
        return self

    @field_validator("tools", "recovery_retry_safe_tools")
    @classmethod
    def unique_tools(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Tool names must be unique")
        return value


class RemoteMCP:
    def __init__(self, configuration: MCPConfig, *, source_id: str, client: httpx2.AsyncClient):
        self.config, self.id, self.client = configuration, source_id, client

    def toolset(self, process_tool_call: ProcessToolCallback | None = None) -> MCPToolset[AgentContext]:
        return MCPToolset(
            self.config.url,
            id=self.id,
            process_tool_call=process_tool_call,
            http_client=self.client,
            init_timeout=INITIALIZATION_SECONDS,
            read_timeout=CALL_SECONDS,
            max_retries=0,
            tool_error_behavior="failed",
            prefer_tasks=False,
            cache_tools=True,
            cache_resources=False,
            cache_prompts=False,
            include_instructions=False,
        )

    async def discover(self) -> list[ToolInfo]:
        async with self.toolset() as tools:
            discovered = await tools.list_tools()
            return [
                ToolInfo(name=tool.name, description=tool.description, input_schema=tool.input_schema)
                for tool in discovered
            ]

    def open(self, headers: MCPHeadersFactory, wrapper: ToolsetWrapper, *, run_id: str) -> ContextualMCP:
        async def call(
            ctx: RunContext[AgentContext], call_tool: CallToolFunc, name: str, arguments: dict[str, Any]
        ) -> ToolResult:
            if not ctx.tool_call_id:
                raise ValueError("MCP dispatch requires an original tool call identity")
            operation = hashlib.sha256(f"{run_id}:{self.id}:{ctx.tool_call_id}".encode()).hexdigest()
            return await call_tool(
                name,
                arguments,
                metadata={
                    "a13n.service": {
                        "operation_id": operation,
                        "run_id": run_id,
                        "connection_id": self.id,
                        "tool_call_id": ctx.tool_call_id,
                    }
                },
            )

        def local(resolved: Mapping[str, str]) -> AbstractToolset[AgentContext]:
            self.client.headers.update(resolved)
            return wrapper(self.toolset(call))

        return ContextualMCP(
            self.config.url,
            id=self.id,
            headers_factory=headers,
            local_toolset_factory=local,
        )


DEFINITION = ToolSourceDefinition(
    type="mcp", display_name="Remote MCP", configuration_model=MCPConfig, factory=RemoteMCP
)
