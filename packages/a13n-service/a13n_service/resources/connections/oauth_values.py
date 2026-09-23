"""Public status and detached values for principal-owned OAuth operations."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_url: str = Field(min_length=1, max_length=2048)


class AuthorizationView(BaseModel):
    id: str | None
    connection_id: str
    status: Literal["not_authorized", "pending", "active", "revoked", "reauthorization_required"]
    expires_at: datetime | None
    generation: int
    operation_kind: Literal["exchange", "refresh", "setup", "complete", "revoke"] | None
    failure: dict[str, str] | None


class AuthorizationStart(BaseModel):
    authorization: AuthorizationView
    redirect_url: str


@dataclass(frozen=True)
class BrowserFlow:
    response: AuthorizationStart
    cookie_name: str
    cookie_value: str = field(repr=False)


@dataclass(frozen=True)
class OAuthClaim:
    authorization_id: str
    connection_id: str
    workspace_id: str
    principal_id: str
    generation: int
    operation_id: str
    kind: Literal["exchange", "refresh"]
    identity: str
    bundle: dict = field(repr=False)
    client_secret: str | None = field(repr=False)
    deadline: datetime


@dataclass(frozen=True)
class OAuthAccess:
    authorization_id: str
    generation: int
    token: str = field(repr=False)
