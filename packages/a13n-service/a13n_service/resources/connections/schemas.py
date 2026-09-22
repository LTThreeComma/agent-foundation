"""Public Connection values; credentials are accepted but never returned."""

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)

from a13n_service.infra.ids import ObjectId
from a13n_service.providers.mcp import MCPConfig, ToolName
from a13n_service.providers.tools import MAX_TOOLS, ToolInfo
from a13n_service.resources.connections.headers import normalize_headers


class BearerCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("token")
    @classmethod
    def valid_token(cls, value: SecretStr) -> SecretStr:
        normalize_headers({"authorization": "Bearer " + value.get_secret_value()}, authentication=True)
        return value


class HeadersCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headers: dict[str, SecretStr] = Field(min_length=1, max_length=32, repr=False)

    @field_validator("headers")
    @classmethod
    def valid_headers(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        normalized = normalize_headers(
            {name: secret.get_secret_value() for name, secret in value.items()}, authentication=True
        )
        return {name: SecretStr(secret) for name, secret in normalized.items()}


type Credential = BearerCredential | HeadersCredential

type ConnectionAuthentication = Literal["none", "bearer", "headers"]


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["mcp"]
    name: str = Field(min_length=1, max_length=128)
    config: MCPConfig
    auth: ConnectionAuthentication = "none"
    credential: Credential | None = Field(default=None, repr=False)


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    config: MCPConfig | None = None
    auth: ConnectionAuthentication | None = None
    credential: Credential | None = Field(default=None, repr=False)
    enabled: bool | None = None


class ConnectionView(BaseModel):
    id: str
    organization_id: str
    workspace_id: str
    type: str
    name: str
    config: MCPConfig
    auth: ConnectionAuthentication
    credential_configured: bool
    enabled: bool
    version: int


class ConnectionPage(BaseModel):
    items: list[ConnectionView]
    next_cursor: str | None


class ConnectionTest(BaseModel):
    connection_id: str
    version: int
    tools: list[ToolInfo]


class ConnectionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connection_id: ObjectId
    tools: tuple[ToolName, ...] = Field(min_length=1, max_length=MAX_TOOLS)

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Selected tools must be unique")
        return value
