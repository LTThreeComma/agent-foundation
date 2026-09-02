from __future__ import annotations

import pytest
from a13n_service.agent_presets.domain import (
    AgentPresetCommandRequest,
    BuiltinAgentPresetRegistration,
    CreateAgentPresetRequest,
    DuplicateAgentPresetRequest,
    ReplaceAgentPresetConfigRequest,
)
from a13n_service.agent_presets.errors import AgentPresetError
from a13n_service.agent_presets.models import AgentPresetRecord, AgentPresetRevisionRecord
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.model_configs.models import ModelConfigRecord
from a13n_service.storage import transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import MODEL_ID, WORKSPACE_ID, actor, preset_config

BUILTIN_PRESET_ID = "ap_builtinpreset0001"
SYSTEM_ACTOR_ID = "sa_1234567890abcdef"


def registration(
    *, instructions: str = "Be helpful.", name: str = "Foundation Assistant"
) -> BuiltinAgentPresetRegistration:
    return BuiltinAgentPresetRegistration(
        preset_id=BUILTIN_PRESET_ID,
        system_actor_id=SYSTEM_ACTOR_ID,
        name=name,
        description="Distribution-owned starter Preset.",
        config=preset_config(instructions=instructions),
    )


@pytest.mark.anyio
async def test_builtin_registration_is_executable_idempotent_and_upgradable(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first = await agent_preset_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )

    assert first.preset.id == BUILTIN_PRESET_ID
    assert first.preset.source == "builtin"
    assert first.preset.lifecycle_state == "enabled"
    assert first.preset.default_revision_id == first.revision.id
    assert first.preset.config_base_revision_id == first.revision.id
    assert not first.preset.config_changed_since_revision
    assert first.revision.revision_number == 1
    assert first.revision.created_by.principal_id == SYSTEM_ACTOR_ID

    replay = await agent_preset_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )
    assert replay == first

    upgraded = await agent_preset_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(instructions="Use the upgraded behavior."),
    )
    assert upgraded.preset.resource_version == 2
    assert upgraded.preset.default_revision_id == upgraded.revision.id
    assert upgraded.preset.config_base_revision_id == upgraded.revision.id
    assert upgraded.revision.revision_number == 2
    assert upgraded.revision.config.instructions == "Use the upgraded behavior."

    revisions = await agent_preset_service.list_revisions(
        actor=actor(),
        preset_id=BUILTIN_PRESET_ID,
        limit=10,
        cursor=None,
    )
    assert tuple(item.revision_number for item in revisions.items) == (2, 1)
    async with transaction(agent_preset_sessions) as session:
        preset_record = await session.get(AgentPresetRecord, BUILTIN_PRESET_ID)
        revision_records = tuple(
            (
                await session.scalars(
                    select(AgentPresetRevisionRecord).where(
                        AgentPresetRevisionRecord.agent_preset_id == BUILTIN_PRESET_ID
                    )
                )
            ).all()
        )
        assert preset_record is not None
        assert preset_record.created_by_type == "system"
        assert preset_record.updated_by_type == "system"
        assert {item.created_by_type for item in revision_records} == {"system"}


@pytest.mark.anyio
async def test_builtin_registration_updates_metadata_without_creating_revision(
    agent_preset_service: AgentPresetService,
) -> None:
    first = await agent_preset_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )
    renamed = await agent_preset_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(name="Foundation Helper"),
    )

    assert renamed.preset.name == "Foundation Helper"
    assert renamed.preset.resource_version == 2
    assert renamed.revision == first.revision
    revisions = await agent_preset_service.list_revisions(
        actor=actor(),
        preset_id=BUILTIN_PRESET_ID,
        limit=10,
        cursor=None,
    )
    assert len(revisions.items) == 1


@pytest.mark.anyio
async def test_builtin_registration_race_replay_requires_exact_committed_result(
    agent_preset_service: AgentPresetService,
) -> None:
    registered = await agent_preset_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )

    replay = await agent_preset_service._builtin_registration_replay(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
        expected_content_digest=registered.revision.content_digest,
    )
    assert replay == registered
    assert (
        await agent_preset_service._builtin_registration_replay(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            registration=registration(name="Different metadata"),
            expected_content_digest=registered.revision.content_digest,
        )
        is None
    )
    assert (
        await agent_preset_service._builtin_registration_replay(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            registration=registration(),
            expected_content_digest="0" * 64,
        )
        is None
    )


@pytest.mark.anyio
async def test_builtin_registration_revisions_changed_resolved_dependencies(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first = await agent_preset_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )
    async with transaction(agent_preset_sessions) as session:
        model = await session.get(ModelConfigRecord, MODEL_ID)
        assert model is not None
        model.model_name = "gpt-5.1"

    upgraded = await agent_preset_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )

    assert upgraded.revision.revision_number == 2
    assert upgraded.revision.config == first.revision.config
    assert upgraded.revision.resolved_model.execution.model_name == "gpt-5.1"
    assert upgraded.revision.content_digest != first.revision.content_digest
    assert upgraded.preset.default_revision_id == upgraded.revision.id


@pytest.mark.anyio
async def test_builtin_is_user_read_only_but_can_be_duplicated(
    agent_preset_service: AgentPresetService,
) -> None:
    registered = await agent_preset_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )

    with pytest.raises(AgentPresetError) as config_rejected:
        await agent_preset_service.replace_config(
            actor=actor(),
            preset_id=BUILTIN_PRESET_ID,
            request=ReplaceAgentPresetConfigRequest(
                expected_resource_version=registered.preset.resource_version,
                config=preset_config(instructions="User mutation."),
            ),
        )
    assert config_rejected.value.code == "preset_state_conflict"

    with pytest.raises(AgentPresetError) as revision_rejected:
        await agent_preset_service.create_revision(
            actor=actor(),
            preset_id=BUILTIN_PRESET_ID,
            idempotency_key="builtin-user-revision",
            request=AgentPresetCommandRequest(expected_resource_version=registered.preset.resource_version),
        )
    assert revision_rejected.value.code == "preset_state_conflict"

    duplicate = await agent_preset_service.duplicate(
        actor=actor(),
        preset_id=BUILTIN_PRESET_ID,
        idempotency_key="duplicate-builtin",
        request=DuplicateAgentPresetRequest(
            expected_resource_version=registered.preset.resource_version,
            name="Customized Assistant",
        ),
    )
    assert duplicate.source == "custom"
    assert duplicate.lifecycle_state == "enabled"
    assert duplicate.default_revision_id is not None
    assert duplicate.duplicated_from_preset_id == BUILTIN_PRESET_ID
    assert duplicate.duplicated_from_revision_id == registered.revision.id


@pytest.mark.anyio
async def test_failed_builtin_registration_creates_no_partial_preset(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    invalid = registration().model_copy(
        update={
            "config": preset_config().model_copy(
                update={"model": preset_config().model.model_copy(update={"model_config_id": "mdl_missingmodel0001"})}
            )
        }
    )

    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_service.register_builtin(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            registration=invalid,
        )
    assert rejected.value.code == "preset_revision_create_failed"

    async with transaction(agent_preset_sessions) as session:
        assert await session.get(AgentPresetRecord, BUILTIN_PRESET_ID) is None


@pytest.mark.anyio
async def test_builtin_name_conflict_creates_no_partial_preset(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="custom-name-conflict",
        request=CreateAgentPresetRequest(name="Foundation Assistant", config=preset_config()),
    )

    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_service.register_builtin(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            registration=registration(),
        )
    assert rejected.value.code == "preset_name_conflict"

    async with transaction(agent_preset_sessions) as session:
        assert await session.get(AgentPresetRecord, BUILTIN_PRESET_ID) is None
