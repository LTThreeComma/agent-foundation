"""External agent configuration selections; execution consumes the ID-only schemas after resolution."""

from pydantic import Field

from a13n_service.infra.ids import ObjectId
from a13n_service.resources.agents.schemas import (
    AgentCreate,
    AgentModel,
    AgentReviewer,
    AgentRevisionCreate,
    AgentValidate,
    BoundedKey,
    ConfigFields,
    ModelFields,
    ModelOverride,
    ModelOverrideFields,
    OverrideFields,
    SkillSelection,
    SubagentOverride,
    SubagentSelection,
)
from a13n_service.resources.memories.inputs import MemoryMountsInput
from a13n_service.resources.models.inputs import MediaSelectionInput
from a13n_service.resources.references import KeyReference, Reference


class ModelByKey(ModelFields, KeyReference):
    pass


type ModelInput = AgentModel | ModelByKey


class SkillByKey(KeyReference):
    revision_id: ObjectId | None = None


type SkillInput = SkillSelection | SkillByKey
ReviewerInput = AgentReviewer[Reference]
SubagentInput = SubagentSelection[Reference]


class ConfigInput(ConfigFields):
    model: ModelInput
    skills: tuple[SkillInput, ...] = Field(default=(), max_length=512)
    subagents: dict[BoundedKey, SubagentInput] = Field(default_factory=dict, max_length=128)
    reviewer: ReviewerInput | None = None
    media_understanding: MediaSelectionInput = Field(default_factory=MediaSelectionInput)
    default_environment_template: Reference | None = None
    memory_mounts: MemoryMountsInput = ()


class ModelOverrideByKey(ModelOverrideFields, KeyReference):
    pass


type ModelOverrideInput = ModelOverride | ModelOverrideByKey
SubagentOverrideInput = SubagentOverride[Reference]


class OverrideInput(OverrideFields):
    reviewer: ReviewerInput | None = None
    media_understanding: MediaSelectionInput | None = None
    model: ModelOverrideInput | None = None
    skills: tuple[SkillInput, ...] | None = Field(default=None, max_length=512)
    subagents: dict[BoundedKey, SubagentOverrideInput | None] | None = Field(default=None, max_length=128)


AgentCreateInput = AgentCreate[ConfigInput]
AgentRevisionCreateInput = AgentRevisionCreate[ConfigInput]


AgentValidateInput = AgentValidate[ConfigInput, Reference]
