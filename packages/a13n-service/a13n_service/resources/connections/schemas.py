"""Public Connection values; credentials are accepted but never returned."""

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from a13n_service.infra.ids import ObjectId
from a13n_service.providers.composio import ComposioConfig
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


class OAuthClientCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_secret: SecretStr = Field(min_length=1, max_length=8192)


class ManagedCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(min_length=1, max_length=8192)


type Credential = BearerCredential | HeadersCredential | OAuthClientCredential | ManagedCredential
type ConnectionConfig = MCPConfig | ComposioConfig


def parse_config(provider_type: str, value: dict) -> ConnectionConfig:
    if provider_type == "composio":
        return ComposioConfig.model_validate(value)
    return MCPConfig.model_validate(value)


def selected_tools(config: ConnectionConfig) -> tuple[str, ...] | None:
    return config.actions if isinstance(config, ComposioConfig) else config.tools


def recovery_tools(config: ConnectionConfig) -> tuple[str, ...]:
    return () if isinstance(config, ComposioConfig) else config.recovery_retry_safe_tools


type ConnectionAuthentication = Literal["none", "bearer", "headers", "oauth", "managed"]


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["mcp", "composio"]
    name: str = Field(min_length=1, max_length=128)
    config: ConnectionConfig
    auth: ConnectionAuthentication = "none"
    credential: Credential | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def provider_configuration(self) -> "ConnectionCreate":
        if (self.type == "composio") != isinstance(self.config, ComposioConfig):
            raise ValueError("Connection configuration must match its provider")
        if (self.type == "composio") != (self.auth == "managed"):
            raise ValueError("Composio requires managed authentication")
        return self


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    config: ConnectionConfig | None = None
    auth: ConnectionAuthentication | None = None
    credential: Credential | None = Field(default=None, repr=False)
    enabled: bool | None = None


class ConnectionView(BaseModel):
    id: str
    organization_id: str
    workspace_id: str
    type: str
    name: str
    config: ConnectionConfig
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
