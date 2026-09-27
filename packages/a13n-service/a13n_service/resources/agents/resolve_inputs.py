"""Resolve configuration input once, then hand ID-only values to validation, persistence and execution."""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.resources.agents.inputs import ConfigInput, OverrideInput
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.memories.inputs import mount_ids
from a13n_service.resources.memories.tables import MemoryRow
from a13n_service.resources.models.inputs import collect_media, media_ids
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.references import IdReference, KeyReference, ReferenceBatch, resolved_model
from a13n_service.resources.skills.tables import SkillRow


def collect_config(batch: ReferenceBatch, config: ConfigInput | OverrideInput) -> None:
    if isinstance(config.model, IdReference | KeyReference):
        batch.add(ModelRow, config.model)
    if config.reviewer is not None:
        batch.add(ModelRow, config.reviewer.model)
    if config.media_understanding is not None:
        collect_media(batch, config.media_understanding)
    for skill in config.skills or ():
        batch.add(SkillRow, skill)
    for edge in (config.subagents or {}).values():
        if edge is not None:
            batch.add(AgentRow, edge.agent)
            if edge.environment is not None:
                batch.add(EnvironmentTemplateRow, edge.environment.template)
    if isinstance(config, ConfigInput):
        batch.add(EnvironmentTemplateRow, config.default_environment_template)
        for mount in config.memory_mounts:
            batch.add(MemoryRow, mount.memory)


def _config_values(batch: ReferenceBatch, config: ConfigInput | OverrideInput) -> dict[str, object]:
    values = config.model_dump(exclude_unset=True)
    if config.model is not None:
        model = config.model.model_dump(exclude={"id", "key"}, exclude_unset=True)
        if isinstance(config.model, IdReference | KeyReference):
            model["model_id"] = batch.id(ModelRow, config.model)
        values["model"] = model
    if config.reviewer is not None:
        values["reviewer"] = {**config.reviewer.model_dump(), "model": batch.id(ModelRow, config.reviewer.model)}
    if config.media_understanding is not None:
        values["media_understanding"] = media_ids(batch, config.media_understanding).model_dump()
    if config.skills is not None:
        values["skills"] = [
            {"skill_id": batch.id(SkillRow, skill), "revision_id": skill.revision_id} for skill in config.skills
        ]
    if config.subagents is not None:
        edges: dict[str, object] = {}
        for name, edge in config.subagents.items():
            if edge is None:
                edges[name] = None
                continue
            fields = edge.model_dump(exclude={"agent", "environment"}, exclude_unset=True)
            if edge.agent is not None:
                fields["agent_id"] = batch.id(AgentRow, edge.agent)
            if edge.environment is not None:
                environment = edge.environment
                fields["environment"] = {
                    "mode": environment.mode,
                    "template_id": None
                    if environment.template is None
                    else batch.id(EnvironmentTemplateRow, environment.template),
                }
            edges[name] = fields
        values["subagents"] = edges
    return values


def config_ids(batch: ReferenceBatch, config: ConfigInput) -> AgentConfig:
    values = _config_values(batch, config)
    values.pop("default_environment_template", None)
    values["default_environment_template_id"] = (
        None
        if config.default_environment_template is None
        else batch.id(EnvironmentTemplateRow, config.default_environment_template)
    )
    values["memory_mounts"] = [mount_ids(batch, mount) for mount in config.memory_mounts]
    return resolved_model(AgentConfig, values)


def override_ids(batch: ReferenceBatch, override: OverrideInput) -> AgentOverride:
    return resolved_model(AgentOverride, _config_values(batch, override))


async def resolve_config(session: AsyncSession, workspace_id: str, config: ConfigInput) -> AgentConfig:
    batch = ReferenceBatch()
    collect_config(batch, config)
    await batch.resolve(session, workspace_id)
    return config_ids(batch, config)
