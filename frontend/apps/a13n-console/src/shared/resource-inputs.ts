import type { Schema } from "./api";

/** Read representations keep canonical IDs; every write uses explicit resource selections. */
export function mediaInput(
  media: Schema["MediaUnderstandingSelection"],
): Schema["MediaSelectionInput"] {
  return {
    image: media.image == null ? media.image : { id: media.image },
    video: media.video == null ? media.video : { id: media.video },
    audio: media.audio == null ? media.audio : { id: media.audio },
  };
}
export function memoryInput({
  memory_id,
  ...fields
}: Schema["MemoryMount"]): Schema["MemoryMountInput"] {
  return { ...fields, memory: { id: memory_id } };
}
function environmentInput({
  template_id,
  ...fields
}: Schema["ChildEnvironmentPolicy"]): Schema["ChildEnvironmentInput"] {
  return {
    ...fields,
    template: template_id == null ? template_id : { id: template_id },
  };
}
function reviewerInput({
  model,
  ...fields
}: Schema["AgentReviewer"]): Schema["ReviewerInput"] {
  return { ...fields, model: { id: model } };
}
function skillInput({
  skill_id,
  ...fields
}: Schema["SkillSelection"]): Schema["SkillById"] {
  return { ...fields, id: skill_id };
}
export function configInput(
  config: Schema["AgentConfig"],
): Schema["ConfigInput"] {
  const {
    model,
    reviewer,
    media_understanding,
    skills,
    subagents,
    default_environment_template_id,
    memory_mounts,
    ...fields
  } = config;
  const { model_id, ...modelFields } = model;
  return {
    ...fields,
    model: { ...modelFields, id: model_id },
    reviewer: reviewer == null ? reviewer : reviewerInput(reviewer),
    media_understanding: media_understanding && mediaInput(media_understanding),
    skills: skills?.map(skillInput),
    subagents:
      subagents &&
      Object.fromEntries(
        Object.entries(subagents).map(([name, edge]) => {
          const { agent_id, environment, ...edgeFields } = edge;
          return [
            name,
            {
              ...edgeFields,
              agent: { id: agent_id },
              environment: environment && environmentInput(environment),
            },
          ];
        }),
      ),
    default_environment_template:
      default_environment_template_id == null
        ? default_environment_template_id
        : { id: default_environment_template_id },
    memory_mounts: memory_mounts?.map(memoryInput),
  };
}
export function overrideInput(
  override: Schema["AgentOverride"],
): Schema["OverrideInput"] {
  const { model, reviewer, media_understanding, skills, subagents, ...fields } =
    override;
  let selectedModel: Schema["OverrideInput"]["model"] | null | undefined =
    model;
  if (model) {
    const { model_id, ...modelFields } = model;
    selectedModel =
      model_id == null ? modelFields : { ...modelFields, id: model_id };
  }
  return {
    ...fields,
    model: selectedModel,
    reviewer: reviewer == null ? reviewer : reviewerInput(reviewer),
    media_understanding:
      media_understanding == null
        ? media_understanding
        : mediaInput(media_understanding),
    skills: skills == null ? skills : skills.map(skillInput),
    subagents:
      subagents &&
      Object.fromEntries(
        Object.entries(subagents).map(([name, edge]) => {
          if (!edge) return [name, edge];
          const { agent_id, environment, ...edgeFields } = edge;
          return [
            name,
            {
              ...edgeFields,
              agent: agent_id == null ? agent_id : { id: agent_id },
              environment:
                environment == null
                  ? environment
                  : environmentInput(environment),
            },
          ];
        }),
      ),
  };
}
export function optionsInput(
  options: Schema["RunOptions"],
): Schema["RunOptions_OverrideInput_"] {
  return {
    ...options,
    overrides:
      options.overrides == null
        ? options.overrides
        : overrideInput(options.overrides),
  };
}
