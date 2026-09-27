"""External agent configuration selections; execution consumes the ID-only schemas after resolution."""

from typing import Literal, Self

from a13n_harness.capabilities.tool_review import ToolReviewPolicy
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai.usage import UsageLimits

from a13n_service.infra.ids import ObjectId
from a13n_service.resources.agents.schemas import (
    AgentCreate,
    AgentModelCharacteristics,
    AgentRevisionCreate,
    BoundedKey,
    ConfigFields,
    DelegationContextPolicy,
    ModelFields,
    ModelSettings,
    OverrideFields,
    SubagentFields,
)
from a13n_service.resources.memories.inputs import MemoryMountsInput
from a13n_service.resources.models.inputs import MediaSelectionInput
from a13n_service.resources.references import IdReference, KeyReference, Reference


class ModelById(ModelFields, IdReference):
    pass


class ModelByKey(ModelFields, KeyReference):
    pass


type ModelInput = ModelById | ModelByKey


class SkillById(IdReference):
    revision_id: ObjectId | None = None


class SkillByKey(KeyReference):
    revision_id: ObjectId | None = None


type SkillInput = SkillById | SkillByKey


class ChildEnvironmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["none", "shared", "dedicated"] = "shared"
    template: Reference | None = None

    @model_validator(mode="after")
    def template_for_dedicated(self) -> Self:
        if (self.mode == "dedicated") != (self.template is not None):
            raise ValueError("Exactly dedicated child environments name a template")
        return self


class SubagentInput(SubagentFields):
    agent: Reference
    revision_id: ObjectId | None = None
    environment: ChildEnvironmentInput = Field(default_factory=ChildEnvironmentInput)


class ReviewerInput(ToolReviewPolicy):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: Reference
    instruction: str | None = Field(default=None, max_length=32768)
    shell_instruction: str | None = Field(default=None, max_length=32768)
    model_settings: ModelSettings | None = None
    timeout_seconds: float = Field(default=120, gt=0, le=120)
    on_error: Literal["deny", "approval_required", "allow"] = "approval_required"


class ConfigInput(ConfigFields):
    model: ModelInput
    skills: tuple[SkillInput, ...] = Field(default=(), max_length=512)
    subagents: dict[BoundedKey, SubagentInput] = Field(default_factory=dict, max_length=128)
    reviewer: ReviewerInput | None = None
    media_understanding: MediaSelectionInput = Field(default_factory=MediaSelectionInput)
    default_environment_template: Reference | None = None
    memory_mounts: MemoryMountsInput = ()


class ModelOverrideFields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    settings: ModelSettings | None = None
    characteristics: AgentModelCharacteristics | None = None


class ModelOverrideById(ModelOverrideFields, IdReference):
    pass


class ModelOverrideByKey(ModelOverrideFields, KeyReference):
    pass


type ModelOverrideInput = ModelOverrideById | ModelOverrideByKey | ModelOverrideFields


class SubagentOverrideInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent: Reference | None = None
    revision_id: ObjectId | None = None
    description: str | None = Field(default=None, max_length=4096)
    context: DelegationContextPolicy | None = None
    usage_limits: UsageLimits | None = None
    environment: ChildEnvironmentInput | None = None


class OverrideInput(OverrideFields):
    reviewer: ReviewerInput | None = None
    media_understanding: MediaSelectionInput | None = None
    model: ModelOverrideInput | None = None
    skills: tuple[SkillInput, ...] | None = Field(default=None, max_length=512)
    subagents: dict[BoundedKey, SubagentOverrideInput | None] | None = Field(default=None, max_length=128)


AgentCreateInput = AgentCreate[ConfigInput]
AgentRevisionCreateInput = AgentRevisionCreate[ConfigInput]


class AgentValidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: ConfigInput
    agent: Reference | None = None
