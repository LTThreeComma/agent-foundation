"""Pure composition of selected invocation facts; no database access."""

from __future__ import annotations

from a13n_service.digests import digest_request
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.models.settings import effective_settings
from a13n_service.skills.domain import SkillRevisionLock

from ..domain import ChildAgentExecution, EffectiveAgentConfig, EffectiveAgentModel
from ..model_characteristics import compose_model_characteristics
from .contracts import FrozenAgentInvocation, PreparedAgentInvocation


def compose_invocation(prepared: PreparedAgentInvocation) -> FrozenAgentInvocation:
    """Build the candidate from exactly the facts selected during preparation."""
    children = {}
    for item in prepared.subagents:
        child = item.invocation
        assert child.agent_revision_id is not None and child.revision_content_digest is not None
        frozen = compose_invocation(child)
        children[child.agent_revision_id] = ChildAgentExecution(
            agent_id=child.agent_id,
            revision_content_digest=child.revision_content_digest,
            effective_config=frozen.effective_config,
            connection_selections=frozen.connection_selections,
        )
    skills = tuple(
        SkillRevisionLock(
            skill_id=item.binding.skill_id,
            skill_revision_id=item.revision_id,
            skill_key=item.binding.skill_key,
            version=item.revision_version,
            content_digest=item.content_digest,
        )
        for item in prepared.skills
    )
    return compose_config(prepared, skills=skills, child_configs=children)


def compose_config(
    prepared: PreparedAgentInvocation,
    *,
    skills: tuple[SkillRevisionLock, ...],
    child_configs: dict[str, ChildAgentExecution],
) -> FrozenAgentInvocation:
    """Compose selected facts, also supporting explicitly refreshed Skill locks."""
    resolved_subagents = tuple(item.edge for item in prepared.subagents)
    config_payload = {
        "subagent_mode": prepared.merged.subagent_mode,
        "child_configs": child_configs,
        "schema_version": "1",
        "resolved_model": EffectiveAgentModel(
            execution=ModelExecutionSnapshot.freeze(prepared.model.resource),
            settings=effective_settings(
                prepared.model.resource.model_api,
                prepared.model.resource.settings,
                *prepared.model.settings_layers,
            ),
            characteristics=compose_model_characteristics(
                prepared.model.resource.declarations,
                prepared.merged.model.characteristics,
            ),
        ),
        "toolsets": prepared.merged.toolsets,
        "reviewer": prepared.merged.reviewer,
        "resolved_reviewer_model": (
            EffectiveAgentModel(
                execution=ModelExecutionSnapshot.freeze(prepared.reviewer_model.resource),
                settings=effective_settings(
                    prepared.reviewer_model.resource.model_api,
                    prepared.reviewer_model.resource.settings,
                    *prepared.reviewer_model.settings_layers,
                ),
                characteristics=compose_model_characteristics(prepared.reviewer_model.resource.declarations),
            )
            if prepared.reviewer_model is not None and prepared.merged.reviewer is not None
            else None
        ),
        "plugins": prepared.merged.plugins,
        "skills": skills,
        "connection_tools": prepared.merged.connection_tools,
        "resolved_subagents": resolved_subagents,
        "instructions": prepared.merged.instructions,
        "input_adapter": prepared.merged.input_adapter,
        "client_tools": prepared.merged.client_tools,
        "output_spec": prepared.merged.output_spec,
        "retries": prepared.merged.retries,
        "secret_requirements": prepared.merged.secret_requirements,
        "memory": prepared.merged.memory,
        "protocol": prepared.merged.protocol,
    }
    effective_without_digest = EffectiveAgentConfig(
        **config_payload,
        content_digest="0" * 64,
    )
    digest_payload = effective_without_digest.model_dump(
        mode="json",
        by_alias=True,
        exclude={"content_digest"},
    )
    effective = EffectiveAgentConfig(
        **config_payload,
        content_digest=digest_request(digest_payload),
    )
    return FrozenAgentInvocation(
        agent_id=prepared.agent_id,
        agent_revision_id=prepared.agent_revision_id,
        selector_kind=prepared.selector_kind,
        effective_config=effective,
        connection_selections=prepared.connectivity.selections.connection_selections,
    )
