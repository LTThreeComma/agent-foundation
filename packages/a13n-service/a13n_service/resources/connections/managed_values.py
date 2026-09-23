"""Detached managed-account operations and authenticated browser completion values."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from a13n_service.resources.connections.service import ResolvedConnection


class ManagedCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorization_id: str = Field(min_length=1, max_length=72)
    generation: int = Field(ge=1)
    session_uri: SecretStr | None = Field(default=None, max_length=8192, repr=False)


class ManagedCompleted(BaseModel):
    return_url: str


@dataclass(frozen=True)
class ManagedClaim:
    authorization_id: str
    generation: int
    operation_id: str
    kind: Literal["setup", "complete", "revoke"]
    principal_id: str
    selected: ResolvedConnection
    identity: str
    deadline: datetime
    bundle: dict = field(repr=False)


def cookie_name(authorization_id: str, generation: int) -> str:
    return f"__Host-a13n_managed_{authorization_id}_{generation}"
