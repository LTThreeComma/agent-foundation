"""Models as the API accepts and returns them; `ModelConfig` is what execution reads."""

from datetime import datetime

from a13n_harness.pricing import ModelPricingEntry
from a13n_harness.spec import HarnessModelCharacteristics
from a13n_harness.toolsets.file_media import NativeInputMediaKind
from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_service.infra.ids import ObjectId


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # The upstream model name the provider serves.
    model_name: str = Field(min_length=1, max_length=256)
    # One of the provider type's calling APIs, listed by `GET /provider-types/model`.
    model_api: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$", max_length=96)
    # Context window and modalities.
    characteristics: HarnessModelCharacteristics = Field(default_factory=HarnessModelCharacteristics)
    max_tokens: int | None = Field(default=None, ge=1, le=1000000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Required: a workspace ID, or explicit null to share the model with every workspace the provider serves.
    workspace_id: ObjectId | None
    provider_id: ObjectId
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)
    # Manual: `config` and optional `pricing`. From the catalogue: `catalog_key`, which supplies both.
    config: ModelConfig | None = None
    pricing: ModelPricingEntry | None = None
    catalog_key: str | None = Field(default=None, min_length=1, max_length=512)
    enabled: bool = True

    @model_validator(mode="after")
    def one_source(self) -> "ModelCreate":
        if self.catalog_key is not None and (self.config is not None or self.pricing is not None):
            raise ValueError("A catalogue model brings its own config and pricing")
        return self


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    config: ModelConfig | None = None
    # Replaced whole when present; `null` removes it; omitted leaves it unchanged.
    pricing: ModelPricingEntry | None = None
    enabled: bool | None = None


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str | None
    provider_id: str
    key: str
    name: str
    description: str
    config: ModelConfig
    pricing: ModelPricingEntry | None
    enabled: bool
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class ModelPage(BaseModel):
    items: list[Model]
    next_cursor: str | None


class CatalogModel(BaseModel):
    """A model the provider type is known to serve, with the values creating it from the catalogue uses."""

    key: str
    model_name: str
    characteristics: HarnessModelCharacteristics
    pricing: ModelPricingEntry | None
    source_url: str


class CatalogPage(BaseModel):
    items: list[CatalogModel]
    next_cursor: str | None


MEDIA_KINDS: tuple[NativeInputMediaKind, ...] = ("image", "video", "audio")


class MediaUnderstandingSelection(BaseModel):
    """The model describing each media kind a model cannot read; a kind without one is unavailable."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    image: ObjectId | None = None
    video: ObjectId | None = None
    audio: ObjectId | None = None

    def selections(self) -> dict[NativeInputMediaKind, str]:
        return {kind: model_id for kind in MEDIA_KINDS if (model_id := getattr(self, kind)) is not None}


class MediaDefaults(MediaUnderstandingSelection):
    """A workspace's media-understanding models for agents that select none for a kind; `id` is the workspace's."""

    id: str
    version: int
