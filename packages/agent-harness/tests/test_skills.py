from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from converge_agent_harness import (
    DefinitionError,
    DirectLocalEnvironmentConfiguration,
    DirectLocalEnvironmentProviderBinding,
    DirectLocalRootConfiguration,
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentPermissionSet,
    EnvironmentSkillSource,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResultEvent,
    RunBindings,
    SkillCatalogItem,
    SkillManager,
    SkillsCapability,
    create_environment_run_binding,
)
from converge_agent_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


class _StaticSource:
    def __init__(self, source_id: str, roots: tuple[str, ...], entries: tuple[SkillCatalogItem, ...]) -> None:
        self.source_id = source_id
        self.logical_roots = roots
        self.entries = entries

    async def catalog(self, *, environment) -> tuple[SkillCatalogItem, ...]:
        del environment
        return self.entries


class _Materializer:
    materializer_id = "test-materializer"
    target_root = "/workspace/.agents/skills"

    async def materialize(self, *, environment) -> None:
        await environment.files.mkdir(self.target_root, parents=True, exist_ok=True)
        path = f"{self.target_root}/review"
        await environment.files.mkdir(path, parents=True, exist_ok=True)
        await environment.files.write_text(
            f"{path}/SKILL.md",
            "---\nname: review\ndescription: Review code carefully.\n---\n\n# Review\n\nFollow the checklist.\n",
            mode="upsert",
        )


def _binding(root: Path):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="skills-test",
            root=DirectLocalRootConfiguration(path=root, ownership="caller_owned"),
        )
    )
    return create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(
                EnvironmentBindingRequest(
                    binding_id="binding-1",
                    binding_revision=1,
                    alias="local",
                    permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                    default_working_directory="/",
                    provider_binding=provider,
                ),
            ),
            default_binding_id="binding-1",
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


def _manager(*, materialize: bool = False) -> SkillManager:
    return SkillManager(
        (
            EnvironmentSkillSource(
                "workspace",
                ("/workspace/.agents/skills",),
            ),
        ),
        materializers=(_Materializer(),) if materialize else (),
    )


async def test_skill_manager_materializes_into_authorized_root_and_freezes_frontmatter(tmp_path: Path) -> None:
    (tmp_path / ".agents").mkdir()
    seen: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        seen.append(info)
        yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(_manager(materialize=True)),),
    )
    result = await executable.run("Review this", bindings=RunBindings.local(environment=_binding(tmp_path)))

    assert result.output_or_raise() == "done"
    assert (tmp_path / ".agents" / "skills" / "review" / "SKILL.md").is_file()
    instructions = str(seen[0].instructions)
    assert "<available-skills>" in instructions
    assert "Review code carefully." in instructions
    assert "<path>/workspace/.agents/skills/review</path>" in instructions
    assert "skill_activate" not in {tool.name for tool in seen[0].function_tools}
    assert "skill_inspect" not in {tool.name for tool in seen[0].function_tools}


async def test_skill_catalog_uses_ordered_later_source_precedence(tmp_path: Path) -> None:
    for root, description in (("global", "Global version"), ("project", "Project version")):
        path = tmp_path / root / "same"
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: same\ndescription: {description}\n---\n\n# Same\n",
            encoding="utf-8",
        )
    captured: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        captured.append(info)
        yield "done"

    manager = SkillManager(
        (
            EnvironmentSkillSource("global", ("/workspace/global",)),
            EnvironmentSkillSource("project", ("/workspace/project",)),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(SkillsCapability(manager),),
    )
    await executable.run("Use a skill", bindings=RunBindings.local(environment=_binding(tmp_path)))

    instructions = str(captured[0].instructions)
    assert "Project version" in instructions
    assert "Global version" not in instructions


async def test_ordinary_environment_skill_read_emits_usage_observation(tmp_path: Path) -> None:
    path = tmp_path / ".agents" / "skills" / "review"
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code.\n---\n\n# Review\n",
        encoding="utf-8",
    )

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": "/workspace/.agents/skills/review/SKILL.md"}),
                    tool_call_id="skill-read-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(
                DynamicEnvironmentConfiguration(
                    max_topology_bindings=8,
                    max_topology_bytes=4096,
                    max_reference_entries=64,
                )
            ),
            SkillsCapability(_manager()),
        ),
    )
    events: list[HarnessEvent | HarnessRunResultEvent[str]] = []
    async with executable.stream(
        "Review",
        bindings=RunBindings.local(
            environment=_binding(tmp_path),
            capabilities=(InvocationPolicyCapability(evaluator=_Allow()),),
        ),
    ) as run:
        async for event in run:
            events.append(event)

    extension_payloads = [
        event.event.payload
        for event in events
        if isinstance(event, HarnessEvent) and isinstance(event.event, HarnessExtensionEvent)
    ]
    assert any(payload.get("type") == "skills_catalog_resolved" for payload in extension_payloads)
    assert any(
        payload.get("type") == "skill_accessed"
        and payload.get("skill_name") == "review"
        and payload.get("source_id") == "workspace"
        for payload in extension_payloads
    )


async def test_skill_source_cannot_escape_its_declared_logical_roots(tmp_path: Path) -> None:
    for directory in ("allowed", "outside"):
        skill = tmp_path / directory / "escape"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: escape\ndescription: Escape root.\n---\n",
            encoding="utf-8",
        )
    source = _StaticSource(
        "static",
        ("/workspace/allowed",),
        (
            SkillCatalogItem(
                name="escape",
                description="Escape root.",
                path="/workspace/outside/escape",
            ),
        ),
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(SkillManager((source,))),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "Use a skill",
            bindings=RunBindings.local(environment=_binding(tmp_path)),
        )
    assert exc_info.value.code == "skill_path_outside_source"


async def test_skill_catalog_rejects_truncated_frontmatter_lines(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "long"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: long\ndescription: This description is too long for the selected line budget.\n---\n",
        encoding="utf-8",
    )
    manager = SkillManager(
        (
            EnvironmentSkillSource(
                "workspace",
                ("/workspace/skills",),
                max_line_length=16,
            ),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(manager),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "Use a skill",
            bindings=RunBindings.local(environment=_binding(tmp_path)),
        )
    assert exc_info.value.code == "skill_catalog_invalid"


async def test_skill_catalog_stops_reading_after_frontmatter(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "valid"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: valid\ndescription: Valid metadata.\n---\n" + "x" * 10_000,
        encoding="utf-8",
    )
    manager = SkillManager(
        (
            EnvironmentSkillSource(
                "workspace",
                ("/workspace/skills",),
                max_line_length=64,
            ),
        )
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(manager),),
    )

    result = await executable.run(
        "Use a skill",
        bindings=RunBindings.local(environment=_binding(tmp_path)),
    )
    assert result.output_or_raise() == "done"


async def test_custom_skill_source_requires_existing_regular_document(tmp_path: Path) -> None:
    (tmp_path / "skills" / "missing").mkdir(parents=True)
    source = _StaticSource(
        "static",
        ("/workspace/skills",),
        (
            SkillCatalogItem(
                name="missing",
                description="Missing document.",
                path="/workspace/skills/missing",
            ),
        ),
    )
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=lambda messages, info: _text("done")),
        capabilities=(SkillsCapability(SkillManager((source,))),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "Use a skill",
            bindings=RunBindings.local(environment=_binding(tmp_path)),
        )
    assert exc_info.value.code == "skill_path_unavailable"


async def _text(value: str) -> AsyncIterator[str]:
    yield value
