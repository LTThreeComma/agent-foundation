"""Bounded upload intents and immutable Asset representations."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from a13n_service.infra.ids import ObjectId

AssetName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")
]
ContentType = Annotated[
    str,
    StringConstraints(
        to_lower=True, min_length=3, max_length=127, pattern=r"^[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+$"
    ),
]
UploadId = Annotated[str, StringConstraints(pattern=r"^upl_[a-f0-9]{64}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class UploadIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["a13n.service.upload.v1"] = "a13n.service.upload.v1"
    id: UploadId
    organization_id: ObjectId
    workspace_id: ObjectId
    principal_id: ObjectId
    filename: AssetName
    content_type: ContentType
    size: int = Field(ge=0)
    digest: Digest


class UploadReceipt(UploadIntent):
    request_digest: Digest


class UploadView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    upload_id: str
    filename: str
    content_type: str
    size: int
    digest: str


class AssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    upload_id: UploadId
    name: AssetName


class AssetView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    name: str
    content_type: str
    size: int
    digest: str
    source: dict[str, JsonValue] | None
    retired_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class AssetPage(BaseModel):
    items: list[AssetView]
    next_cursor: str | None
