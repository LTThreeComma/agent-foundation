"""The multipart upload request, the receipt stored beside uploaded bytes and the handle the API returns."""

from typing import Annotated

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from a13n_service.infra.ids import ObjectId

UploadId = Annotated[str, StringConstraints(pattern=r"^upl_[a-f0-9]{64}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
FileName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")
]
ContentType = Annotated[
    str,
    StringConstraints(
        to_lower=True, min_length=3, max_length=127, pattern=r"^[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+$"
    ),
]


class UploadCreate(BaseModel):
    """The multipart form: one file part named `file`."""

    file: UploadFile


class UploadReceipt(BaseModel):
    """Written after the bytes; an upload ID resolves only inside the workspace its receipt names."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UploadId
    organization_id: ObjectId
    workspace_id: ObjectId
    principal_id: ObjectId
    filename: FileName
    content_type: ContentType
    size: int = Field(ge=0)
    digest: Digest


class Upload(BaseModel):
    upload_id: str
    filename: str
    content_type: str
    size: int
    digest: str
