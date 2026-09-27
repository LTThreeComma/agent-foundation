"""Secret requests, requirements and metadata; no representation ever carries a value."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

SecretKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
SecretValue = Annotated[SecretStr, Field(min_length=1, max_length=16384)]


class SecretRequirement(BaseModel):
    """A tool audience resolved by key in the run's workspace whenever it is used."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: SecretKey


class SecretCreate(BaseModel):
    """A secret shared by authorized runs in its workspace."""

    model_config = ConfigDict(extra="forbid")
    key: SecretKey
    value: SecretValue


class SecretUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: SecretValue


class Secret(BaseModel):
    id: str
    workspace_id: str
    key: str
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class SecretPage(BaseModel):
    items: list[Secret]
    next_cursor: str | None
