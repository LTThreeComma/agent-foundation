"""Secret requests, requirements and metadata; no representation ever carries a value."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

SecretKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
SecretValue = Annotated[SecretStr, Field(min_length=1, max_length=16384)]
# `workspace` secrets serve every principal of the workspace; `user` secrets are private to one principal.
type SecretScope = Literal["workspace", "user"]


class SecretRequirement(BaseModel):
    """A secret an agent revision needs at execution; `user` resolves to the run principal's own secret."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: SecretKey
    scope: SecretScope = "workspace"


class SecretCreate(BaseModel):
    """`user` creates a secret private to the caller."""

    model_config = ConfigDict(extra="forbid")
    key: SecretKey
    value: SecretValue
    scope: SecretScope = "workspace"


class SecretUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: SecretValue


class Secret(BaseModel):
    id: str
    workspace_id: str
    key: str
    scope: SecretScope
    principal_id: str | None
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class SecretPage(BaseModel):
    items: list[Secret]
    next_cursor: str | None
