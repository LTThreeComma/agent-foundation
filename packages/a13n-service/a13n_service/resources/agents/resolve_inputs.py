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


def collect_config(batch: ReferenceBatch, config: ConfigInput | AgentConfig | OverrideInput | AgentOverride) -> None:
    if isinstance(config.model, IdReference | KeyReference):
        batch.add(ModelRow, config.model)
    elif config.model is not None and config.model.id is not None:
        batch.add(ModelRow, IdReference(id=config.model.id))
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
    if isinstance(config, ConfigInput | AgentConfig):
        batch.add(EnvironmentTemplateRow, config.default_environment_template)
        for mount in config.memory_mounts:
            batch.add(MemoryRow, mount.memory)


def _config_values(
    batch: ReferenceBatch, config: ConfigInput | AgentConfig | OverrideInput | AgentOverride
) -> dict[str, object]:
    values = config.model_dump(exclude_unset=True)
    if config.model is not None:
        model = config.model.model_dump(exclude={"id", "key"}, exclude_unset=True)
        if isinstance(config.model, KeyReference) or config.model.id is not None:
            if isinstance(config.model, KeyReference):
                reference = config.model
            else:
                assert config.model.id is not None
                reference = IdReference(id=config.model.id)
            model["id"] = batch.id(ModelRow, reference)
        values["model"] = model
    if config.reviewer is not None:
        values["reviewer"] = {
            **config.reviewer.model_dump(),
            "model": {"id": batch.id(ModelRow, config.reviewer.model)},
        }
    if config.media_understanding is not None:
        values["media_understanding"] = media_ids(batch, config.media_understanding).model_dump()
    if config.skills is not None:
        values["skills"] = [
            {"id": batch.id(SkillRow, skill), "revision_id": skill.revision_id} for skill in config.skills
        ]
    if config.subagents is not None:
        edges: dict[str, object] = {}
        for name, edge in config.subagents.items():
            if edge is None:
                edges[name] = None
                continue
            fields = edge.model_dump(exclude_unset=True)
            if edge.agent is not None:
                fields["agent"] = {"id": batch.id(AgentRow, edge.agent)}
            if edge.environment is not None and edge.environment.template is not None:
                fields["environment"] = {
                    **edge.environment.model_dump(),
                    "template": {"id": batch.id(EnvironmentTemplateRow, edge.environment.template)},
                }
            edges[name] = fields
        values["subagents"] = edges
    return values


def config_ids(batch: ReferenceBatch, config: ConfigInput | AgentConfig) -> AgentConfig:
    values = _config_values(batch, config)
    if config.default_environment_template is not None:
        values["default_environment_template"] = {
            "id": batch.id(EnvironmentTemplateRow, config.default_environment_template)
        }
    values["memory_mounts"] = [mount_ids(batch, mount) for mount in config.memory_mounts]
    return resolved_model(AgentConfig, values)


def override_ids(batch: ReferenceBatch, override: OverrideInput) -> AgentOverride:
    return resolved_model(AgentOverride, _config_values(batch, override))


async def resolve_config(session: AsyncSession, workspace_id: str, config: ConfigInput | AgentConfig) -> AgentConfig:
    batch = ReferenceBatch()
    collect_config(batch, config)
    await batch.resolve(session, workspace_id)
    return config_ids(batch, config)
