"""Bounded public model configuration with no transport or credential overrides."""

from typing import Literal

from a13n_harness.pricing import ModelPricingEntry
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.infra.ids import ObjectId


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_name: str = Field(min_length=1, max_length=256)
    model_api: Literal["openai.responses", "openai.chat_completions"]
    context_window: int = Field(ge=1, le=10000000)
    max_tokens: int | None = Field(default=None, ge=1, le=1000000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: ObjectId | None = None
    type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    config: dict[str, JsonValue]
    credential: dict[str, JsonValue] | None = Field(default=None, repr=False)


class ProviderView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str | None
    type: str
    name: str
    config: dict[str, JsonValue]
    credential_configured: bool
    enabled: bool
    version: int


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: ObjectId | None = None
    provider_id: ObjectId
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    name: str = Field(min_length=1, max_length=128)
    config: ModelConfig
    pricing: ModelPricingEntry | None = None


class ModelView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str | None
    provider_id: str
    key: str
    name: str
    config: ModelConfig
    pricing: ModelPricingEntry | None
    enabled: bool
    version: int
