"""Public and durable values owned by Model Management."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import new_object_id

BoundedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
BoundedDescription = Annotated[str, StringConstraints(max_length=2048)]
BoundedModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
BoundedSecretKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$", min_length=1, max_length=128),
]
ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$")]


def new_model_config_id() -> str:
    """Allocate one unpredictable, kind-prefixed ModelConfig identifier."""

    return new_object_id("mdl")


class WorkspaceSecretCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["workspace_secret"] = "workspace_secret"
    secret_id: ObjectId


class InvokingUserSecretCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["invoking_user_secret"] = "invoking_user_secret"
    secret_key: BoundedSecretKey


class NoCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["none"] = "none"


ModelCredential = Annotated[
    WorkspaceSecretCredential | InvokingUserSecretCredential | NoCredential,
    Field(discriminator="source"),
]
_MODEL_CREDENTIAL_ADAPTER = TypeAdapter(ModelCredential)


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_modalities: tuple[str, ...] = Field(default=(), max_length=16)
    context_window_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    tool_calling: bool | None = None
    structured_output: bool | None = None
    reasoning: bool | None = None

    @model_validator(mode="after")
    def validate_modalities(self) -> ModelCapabilities:
        normalized = tuple(dict.fromkeys(item.strip().lower() for item in self.input_modalities if item.strip()))
        if any(len(item) > 32 or not item.replace("_", "").isalnum() for item in normalized):
            raise ValueError("input_modalities contains an invalid value")
        object.__setattr__(self, "input_modalities", normalized)
        return self


class CapabilitySource(StrEnum):
    catalog = "catalog"
    manual_override = "manual_override"


class ModelConfigCreate(BaseModel):
    """Validated create body before provider-specific normalization."""

    model_config = ConfigDict(extra="forbid")

    name: BoundedName
    description: BoundedDescription | None = None
    provider_type: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    model_name: BoundedModelName
    credential: ModelCredential
    provider_config: dict[str, object] = Field(default_factory=dict)
    capabilities: ModelCapabilities | None = None
    enabled: bool = True


class ModelConfigPatch(BaseModel):
    """Partial ModelConfig mutation; omitted fields retain their current value."""

    model_config = ConfigDict(extra="forbid")

    name: BoundedName | None = None
    description: BoundedDescription | None = None
    provider_type: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")] | None = None
    model_name: BoundedModelName | None = None
    credential: ModelCredential | None = None
    provider_config: dict[str, object] | None = None
    capabilities: ModelCapabilities | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_null_for_required_fields(self) -> ModelConfigPatch:
        nullable = {"description", "capabilities"}
        invalid = sorted(
            name for name in self.model_fields_set if name not in nullable and getattr(self, name, None) is None
        )
        if invalid:
            raise ValueError(f"fields cannot be null: {', '.join(invalid)}")
        return self


class ModelConfigResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ObjectId
    workspace_id: ObjectId
    name: str
    description: str | None
    provider_type: str
    model_name: str
    base_url: str | None
    credential: ModelCredential
    provider_config: dict[str, object]
    capabilities: ModelCapabilities
    capability_source: CapabilitySource
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime

    def strong_etag(self) -> str:
        """Return a strong tag over the complete mutable representation."""

        mutable = self.model_dump(
            mode="json",
            include={
                "name",
                "description",
                "provider_type",
                "model_name",
                "base_url",
                "credential",
                "provider_config",
                "capabilities",
                "capability_source",
                "enabled",
                "updated_by",
                "updated_at",
            },
        )
        encoded = json.dumps(mutable, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return f'"{hashlib.sha256(encoded).hexdigest()}"'


class ModelConfigCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ModelConfigResource, ...]
    next_cursor: str | None


class ModelConfigCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: BoundedName
    description: BoundedDescription | None = None
    enabled: bool


class ModelReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: ObjectId
    agent_revision_id: ObjectId
    agent_name: str


class ModelReferenceCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ModelReference, ...]
    next_cursor: str | None


class ModelConnectionTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    elapsed_ms: int = Field(ge=0)
    code: str
    message: str
    may_consume_quota_or_incur_cost: Literal[True] = True


class ModelExecutionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: ObjectId
    provider_type: str
    model_name: str


class ModelExecutionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    model_id: ObjectId
    provider_type: str
    model_name: str
    base_url: str | None
    credential: ModelCredential
    provider_config: dict[str, object]
    adapter_key: str
    adapter_schema_version: str
    adapter_dependency_lock: dict[str, object]
    content_digest_sha256: str

    @classmethod
    def freeze(
        cls,
        model: ModelConfigResource,
        *,
        adapter_key: str,
        adapter_schema_version: str,
        adapter_dependency_lock: dict[str, object],
    ) -> ModelExecutionSnapshot:
        content: dict[str, object] = {
            "schema_version": "1",
            "model_id": model.id,
            "provider_type": model.provider_type,
            "model_name": model.model_name,
            "base_url": model.base_url,
            "credential": _MODEL_CREDENTIAL_ADAPTER.dump_python(model.credential, mode="json"),
            "provider_config": model.provider_config,
            "adapter_key": adapter_key,
            "adapter_schema_version": adapter_schema_version,
            "adapter_dependency_lock": adapter_dependency_lock,
        }
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return cls.model_validate({**content, "content_digest_sha256": hashlib.sha256(encoded).hexdigest()})

    def observation(self) -> ModelExecutionObservation:
        return ModelExecutionObservation(
            model_id=self.model_id,
            provider_type=self.provider_type,
            model_name=self.model_name,
        )

    def verify_content_digest(self) -> None:
        content = self.model_dump(mode="json", exclude={"content_digest_sha256"})
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        if not hmac.compare_digest(self.content_digest_sha256, hashlib.sha256(encoded).hexdigest()):
            raise ValueError("model execution snapshot digest mismatch")
