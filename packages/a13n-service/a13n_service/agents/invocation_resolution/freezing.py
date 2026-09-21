"""Run snapshot composition and commit-time validation for Agent management."""

from __future__ import annotations

from a13n_service.connectivity.selection_domain import ConnectionRunSelection
from a13n_service.digests import digest_request
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.models.settings import effective_settings
from a13n_service.skills.domain import SkillRevisionLock

from ..domain import (
    ChildAgentExecution,
    EffectiveAgentConfig,
    EffectiveAgentModel,
)
from ..errors import (
    agent_revision_not_executable,
)
from ..model_characteristics import compose_model_characteristics
from .contracts import (
    FrozenAgentInvocation,
    PreparedAgentInvocation,
)


class AgentInvocationFreezer:
    """Compose and validate the complete selected graph without reading live configuration."""

    @staticmethod
    def freeze_selected(*, prepared: PreparedAgentInvocation) -> FrozenAgentInvocation:
        """Build one Run snapshot from checked selections, without querying current configuration."""
        child_configs = {}
        for item in prepared.subagents:
            child = item.invocation
            frozen = AgentInvocationFreezer.freeze_selected(prepared=child)
            assert child.agent_revision_id is not None and child.revision_content_digest is not None
            child_configs[child.agent_revision_id] = ChildAgentExecution(
                agent_id=child.agent_id,
                revision_content_digest=child.revision_content_digest,
                effective_config=frozen.effective_config,
                connection_selections=frozen.connection_selections,
            )
        return _compose_invocation(
            prepared,
            execution=ModelExecutionSnapshot.freeze(prepared.model.resource),
            reviewer_execution=ModelExecutionSnapshot.freeze(prepared.reviewer_model.resource)
            if prepared.reviewer_model is not None
            else None,
            skills=prepared.skills,
            connection_selections=prepared.connectivity,
            child_configs=child_configs,
        )


def _compose_invocation(
    prepared: PreparedAgentInvocation,
    *,
    execution: ModelExecutionSnapshot,
    reviewer_execution: ModelExecutionSnapshot | None,
    skills: tuple[SkillRevisionLock, ...],
    connection_selections: tuple[ConnectionRunSelection, ...],
    child_configs: dict[str, ChildAgentExecution],
) -> FrozenAgentInvocation:
    config_payload = {
        "subagent_mode": prepared.merged.subagent_mode,
        "child_configs": child_configs,
        "schema_version": "1",
        "resolved_model": EffectiveAgentModel(
            execution=execution,
            settings=effective_settings(
                execution.model_api,
                prepared.model.resource.settings,
                *prepared.model.settings_layers,
            ),
            characteristics=compose_model_characteristics(
                prepared.model.resource.declarations,
                prepared.merged.model.characteristics,
            ),
        ),
        "media_understanding": {
            kind: EffectiveAgentModel(
                execution=ModelExecutionSnapshot.freeze(model.resource),
                settings=effective_settings(model.resource.model_api, model.resource.settings),
                characteristics=compose_model_characteristics(model.resource.declarations),
            )
            for kind, model in prepared.media_models.items()
        },
        "toolsets": prepared.merged.toolsets,
        "reviewer": prepared.merged.reviewer,
        "resolved_reviewer_model": (
            EffectiveAgentModel(
                execution=reviewer_execution,
                settings=effective_settings(
                    reviewer_execution.model_api,
                    prepared.reviewer_model.resource.settings,
                    *prepared.reviewer_model.settings_layers,
                ),
                characteristics=compose_model_characteristics(prepared.reviewer_model.resource.declarations),
            )
            if reviewer_execution is not None
            and prepared.reviewer_model is not None
            and prepared.merged.reviewer is not None
            else None
        ),
        "plugins": prepared.merged.plugins,
        "skills": skills,
        "connection_tools": prepared.merged.connection_tools,
        "resolved_subagents": tuple(item.edge for item in prepared.subagents),
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
    _validate_model_snapshots(effective_without_digest)
    digest_payload = effective_without_digest.model_dump(
        mode="json",
        by_alias=True,
        exclude={"content_digest"},
    )
    effective = effective_without_digest.model_copy(update={"content_digest": digest_request(digest_payload)})
    return FrozenAgentInvocation(
        agent_id=prepared.agent_id,
        agent_revision_id=prepared.agent_revision_id,
        selector_kind=prepared.selector_kind,
        effective_config=effective,
        connection_selections=connection_selections,
    )


def _validate_model_snapshots(config: EffectiveAgentConfig) -> None:
    """Reject conflicting captures before accepting a Run that cannot be reconstructed."""
    snapshots: dict[str, ModelExecutionSnapshot] = {}
    pending = [config]
    while pending:
        node = pending.pop()
        models = [node.resolved_model, *node.media_understanding.values()]
        if node.resolved_reviewer_model is not None:
            models.append(node.resolved_reviewer_model)
        for model in models:
            previous = snapshots.setdefault(model.execution.model_id, model.execution)
            if previous != model.execution:
                raise agent_revision_not_executable("model_configuration_changed")
        pending.extend(child.effective_config for child in node.child_configs.values())
