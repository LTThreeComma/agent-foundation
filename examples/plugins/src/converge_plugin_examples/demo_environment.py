"""Run packaged and explicit Environment provider factories through real bindings."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from converge_agent_harness import (
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentPermissionSet,
    EnvironmentProviderBinding,
    EnvironmentProviderFactoryCatalog,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    RunBindings,
    build_environment_provider_factory_catalog,
    create_environment_run_binding,
    discover_environment_provider_factory_references,
)
from pydantic import JsonValue

# Host configuration selects this metadata key without importing its target.
PROVIDER_KEY = "example.workspace"
type EnvironmentSelectionMode = Literal["entrypoint", "code"]


@dataclass(frozen=True, slots=True)
class EnvironmentDemoResult:
    selection_mode: EnvironmentSelectionMode
    provider_key: str
    aliases: tuple[str, ...]
    default_text: str
    docs_text: str


def _provider_configuration(root: Path, environment_id: str) -> dict[str, JsonValue]:
    return {
        "root": str(root),
        "environment_id": environment_id,
        "read_only": True,
    }


def _binding_request(
    *,
    binding_id: str,
    alias: str,
    provider_binding: EnvironmentProviderBinding,
) -> EnvironmentBindingRequest:
    return EnvironmentBindingRequest(
        binding_id=binding_id,
        binding_revision=1,
        alias=alias,
        permission_ceiling=EnvironmentPermissionSet(operations=frozenset({EnvironmentAction.FILE_READ_TEXT})),
        default_working_directory="/",
        provider_binding=provider_binding,
    )


async def _run_environment_demo(
    *,
    selection_mode: EnvironmentSelectionMode,
    catalog: EnvironmentProviderFactoryCatalog,
    source_root: Path,
    docs_root: Path,
) -> EnvironmentDemoResult:
    source = catalog.create_provider_binding(
        PROVIDER_KEY,
        _provider_configuration(source_root, "workspace-source"),
    )
    docs = catalog.create_provider_binding(
        PROVIDER_KEY,
        _provider_configuration(docs_root, "workspace-docs"),
    )
    topology = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            _binding_request(binding_id="workspace-1", alias="source", provider_binding=source),
            _binding_request(binding_id="docs-1", alias="docs", provider_binding=docs),
        ),
        default_binding_id="workspace-1",
    )
    environment_binding = create_environment_run_binding(
        initial_topology=topology,
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=1),
        state_limits=EnvironmentStateLimits(max_binding_entries=2),
    )
    run_bindings = RunBindings.local(environment=environment_binding)

    # HarnessRunStream performs this same aggregate bind/activate lifecycle.
    async with run_bindings.environment.bind(
        run_id="run-example",
        instance=run_bindings.instance,
    ) as environment:
        await environment.activate()
        default_page = await environment.files.read_text("/workspace/message.txt")
        docs_page = await environment.files.read_text("/environment/docs/message.txt")
        aliases = tuple(binding.alias for binding in environment.topology.bindings)

    return EnvironmentDemoResult(
        selection_mode=selection_mode,
        provider_key=catalog.registrations[0].provider_key,
        aliases=aliases,
        default_text=default_page.text,
        docs_text=docs_page.text,
    )


async def run_environment_entrypoint_demo(
    *,
    source_root: Path,
    docs_root: Path,
) -> EnvironmentDemoResult:
    """Discover and explicitly select the installed package factory."""

    references = discover_environment_provider_factory_references()
    if PROVIDER_KEY not in {reference.provider_key for reference in references}:
        raise RuntimeError(f"Installed Environment provider factory {PROVIDER_KEY!r} was not discovered")
    catalog = build_environment_provider_factory_catalog(provider_keys=(PROVIDER_KEY,))
    return await _run_environment_demo(
        selection_mode="entrypoint",
        catalog=catalog,
        source_root=source_root,
        docs_root=docs_root,
    )


async def run_environment_code_demo(
    *,
    source_root: Path,
    docs_root: Path,
) -> EnvironmentDemoResult:
    """Supply the factory object directly while retaining its factory method."""

    from converge_plugin_examples.environment import WorkspaceEnvironmentProviderFactory

    catalog = build_environment_provider_factory_catalog(explicit_factories=(WorkspaceEnvironmentProviderFactory(),))
    return await _run_environment_demo(
        selection_mode="code",
        catalog=catalog,
        source_root=source_root,
        docs_root=docs_root,
    )


def _run_main(selection_mode: EnvironmentSelectionMode) -> None:
    with TemporaryDirectory(prefix="converge-source-") as source_value:
        with TemporaryDirectory(prefix="converge-docs-") as docs_value:
            source_root = Path(source_value)
            docs_root = Path(docs_value)
            (source_root / "message.txt").write_text("source workspace\n", encoding="utf-8")
            (docs_root / "message.txt").write_text("documentation workspace\n", encoding="utf-8")
            if selection_mode == "entrypoint":
                coroutine = run_environment_entrypoint_demo(source_root=source_root, docs_root=docs_root)
            else:
                coroutine = run_environment_code_demo(source_root=source_root, docs_root=docs_root)
            result = asyncio.run(coroutine)

    print(f"selection mode: {result.selection_mode}")
    print(f"selected provider: {result.provider_key}")
    print(f"active aliases: {', '.join(result.aliases)}")
    print(f"default route: {result.default_text.strip()}")
    print(f"docs route: {result.docs_text.strip()}")


def main_entrypoint() -> None:
    """Run the package entry-point path without model credentials."""

    _run_main("entrypoint")


def main_code() -> None:
    """Run the explicit factory-object path without model credentials."""

    _run_main("code")


if __name__ == "__main__":
    main_entrypoint()
