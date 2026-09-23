"""Frozen agent configuration used by the hosted execution path."""

from __future__ import annotations

from typing import Annotated

from a13n_harness.tools.client import ClientToolDefinition, ClientToolsetDefinition, ClientToolsSpec
from a13n_harness.tools.identity import ToolIdentity, source_tool_id
from a13n_harness.tools.permissions import ToolPermissions
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from a13n_service.infra.ids import ObjectId
from a13n_service.resources.connections.schemas import ConnectionSelection

Label = Annotated[str, StringConstraints(max_length=128)]
CLIENT_TOOLSET_ID = "a13n-service-client"


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_id: ObjectId
    instructions: str = Field(default="", max_length=65536)
    max_requests: int = Field(default=100, ge=1, le=1000)
    compaction_trigger_tokens: int | None = Field(default=None, ge=1, le=10000000)
    connections: tuple[ConnectionSelection, ...] = Field(default=(), max_length=32)
    user_questions: bool = False
    client_tools: tuple[ClientToolDefinition, ...] = Field(default=(), max_length=128)
    tool_permissions: ToolPermissions = Field(default_factory=ToolPermissions)
    # Referenced, not pinned: read when the primary sandbox is created or started, never during execution.
    environment_template_id: ObjectId | None = None

    @model_validator(mode="after")
    def interaction_configuration(self) -> AgentConfig:
        if "review" in {self.tool_permissions.default, *self.tool_permissions.rules.values()}:
            raise ValueError("Tool review requires a reviewer configuration and is not available yet")
        external = []
        if self.user_questions:
            external.append(ToolIdentity(source_tool_id("a13n-user-interaction-tools", "ask_user_question")))
        if self.client_tools:
            spec = ClientToolsSpec(
                default_toolsets=(ClientToolsetDefinition(toolset_id=CLIENT_TOOLSET_ID, tools=self.client_tools),)
            )
            if len(spec.model_dump_json().encode()) > 262144:
                raise ValueError("Client tool declarations exceed their aggregate byte limit")
            if any(tool.name == "ask_user_question" for tool in self.client_tools):
                raise ValueError("The question tool name is reserved")
            external.extend(ToolIdentity(source_tool_id(CLIENT_TOOLSET_ID, tool.name)) for tool in self.client_tools)
        if any(self.tool_permissions.resolve(identity) == "ask" for identity in external):
            raise ValueError("Questions and client tools cannot require local tool approval")
        return self

    @field_validator("connections")
    @classmethod
    def unique_connections(cls, value: tuple[ConnectionSelection, ...]) -> tuple[ConnectionSelection, ...]:
        if len({item.connection_id for item in value}) != len(value):
            raise ValueError("Connection selections must be unique")
        return value


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=8192)
    labels: dict[Label, Label] = Field(default_factory=dict, max_length=32)
    config: AgentConfig


class RevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: AgentConfig
    note: str | None = Field(default=None, max_length=2048)
    make_default: bool = True


class AgentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str
    key: str
    name: str
    description: str
    labels: dict[str, str]
    default_revision_id: str | None
    source: str
    version: int


class RevisionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_id: str
    number: int
    config: AgentConfig
    digest: str
    note: str | None


class AgentPage(BaseModel):
    items: list[AgentView]
    next_cursor: str | None


class RevisionPage(BaseModel):
    items: list[RevisionView]
    next_cursor: str | None
