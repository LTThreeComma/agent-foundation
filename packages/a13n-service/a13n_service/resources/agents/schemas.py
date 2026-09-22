"""Frozen agent configuration used by the hosted execution path."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from a13n_service.infra.ids import ObjectId

Label = Annotated[str, StringConstraints(max_length=128)]


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_id: ObjectId
    instructions: str = Field(default="", max_length=65536)
    max_requests: int = Field(default=100, ge=1, le=1000)
    compaction_trigger_tokens: int | None = Field(default=None, ge=1, le=10000000)


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
