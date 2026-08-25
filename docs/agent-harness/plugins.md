# Plugins and Extensions

Agent Harness exposes several focused extension points instead of one universal plugin interface. Choose the narrowest boundary that owns the behavior and lifecycle you need.

| Extension point                | Use it for                                                                                                            | Selected and created                                                                                                 | Lifecycle                                                                                             | Model-visible                                              |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| Harness middleware plugin      | Transforming semantic input, observing events, wrapping errors, or replacing a complete result candidate              | Direct `AbstractHarnessPlugin`, or an opted-in Harness plugin document selecting `HarnessPluginFactory` entry points | Agent-bound at build, then freshly run-bound around each logical Harness run                          | Only through explicit Capability contributions             |
| Pydantic Capability or Toolset | Instructions, model-request hooks, tools, Agent-loop state, and collaboration with other run Capabilities             | Direct code in the Agent definition, a trusted Harness plugin contribution, or fresh `RunBindings.capabilities`      | Native Pydantic Agent and run binding                                                                 | Yes                                                        |
| Environment provider           | Implementing file, shell, process, port, output, readiness, or portable-state operations for one Environment resource | Direct `EnvironmentProviderBinding`, or a Host-selected `EnvironmentProviderFactory` catalog                         | One provider revision inside an `EnvironmentRunBinding`; dynamic topology may add or retire revisions | Only through an explicit Environment Capability or Toolset |
| Environment run extension      | Holding a resource scope that needs the complete entered Environment aggregate rather than one provider revision      | Direct `EnvironmentRunExtension`, or a Host-selected `EnvironmentRunExtensionFactory` catalog                        | Once after Environment state restore; reverse-order exit before provider teardown                     | No                                                         |

Installed entry-point metadata means that code is available, not authorized. Importing `converge_agent_harness` scans no entry points and activates no extension. Harness middleware configuration is the only Harness-owned YAML/JSON plugin document; Environment provider and run-extension selection remain explicit Host code using immutable catalogs.

## Harness middleware

Harness plugins are trusted Python middleware around a complete process-local Harness run. Use a Pydantic AI Capability for behavior inside the Agent loop; use a Harness plugin when behavior must wrap semantic input, stream events, and the complete result boundary.

A plugin can be supplied directly as an `AbstractHarnessPlugin` object or published as a Python distribution with a `HarnessPluginFactory` entry point. The distribution mode lets a Host select installed plugins from bounded YAML or JSON without accepting arbitrary Python import targets.

### Publish a plugin distribution

Register one no-argument factory class under the Harness entry-point group:

```toml
[project.entry-points."converge_agent_harness.plugins"]
"acme.audit" = "acme_harness.plugin:AuditPluginFactory"
```

The entry-point name and `plugin_key()` must match:

```python
from converge_agent_harness import (
    AbstractHarnessPlugin,
    HarnessPluginFactory,
    HarnessPluginFactoryContext,
)


class AuditPlugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id

    @property
    def plugin_id(self) -> str:
        return self._plugin_id


class AuditPluginFactory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls) -> str:
        return "acme.audit"

    def create_plugin(
        self,
        context: HarnessPluginFactoryContext,
    ) -> AbstractHarnessPlugin:
        # Validate context.configuration with a package-owned schema.
        return AuditPlugin(context.plugin_id)
```

Factory construction must be side-effect free. The factory receives detached JSON configuration and returns a fresh concrete plugin for each configured instance. Mutable run state belongs in an exact-type value returned by the plugin's `for_run()` method.

### Select configured plugins

The Harness configuration document is a data schema, not a file requirement. Host code can pass an ordinary mapping directly; the Harness validates and detaches it before retaining the build context:

```python
from converge_agent_harness import HarnessBuildContext, HarnessBuilder

configuration = {
    "schema_version": "1",
    "plugins": [
        {
            "plugin_id": "audit-primary",
            "plugin_key": "acme.audit",
            "enabled": True,
            "configuration": {"mode": "metadata"},
        }
    ],
}
context = HarnessBuildContext.from_configuration(configuration)
builder = HarnessBuilder(build_context=context)
```

The same schema can come from an optional YAML or JSON file when that is more convenient for deployment:

```yaml
schema_version: "1"
plugins:
  - plugin_id: audit-primary
    plugin_key: acme.audit
    enabled: true
    configuration:
      mode: metadata
```

```python
context = HarnessBuildContext.from_file("harness-plugins.yaml")
builder = HarnessBuilder(build_context=context)
```

Both forms select stable entry-point keys and never accept a `module:object` import target. Alternatively, a builder can resolve an opt-in environment source:

```bash
export CONVERGE_HARNESS_PLUGIN_CONFIG_ENABLED=true
export CONVERGE_HARNESS_PLUGIN_CONFIG_FILE=/etc/converge/harness-plugins.yaml
```

```python
from converge_agent_harness import HarnessBuilder

builder = HarnessBuilder()
```

Configuration is disabled by default. When disabled, the builder does not read a configuration file or scan package metadata. When enabled, it imports only entry-point keys referenced by enabled entries.

### Use a Host-managed plugin directory

A long-lived Host can discover a newly installed plugin distribution without rebuilding its image or restarting its Python process. The directory must be on Python's package search path and must contain both the import package and standard distribution metadata, including the entry point. Copying only a `.py` module is insufficient.

Use `PYTHONPATH`, not the shell executable `PATH`, when a fully populated distribution directory is known before process startup:

```bash
export PYTHONPATH=/srv/agent-plugins/preinstalled${PYTHONPATH:+:$PYTHONPATH}
```

Changing `os.environ["PYTHONPATH"]` after Python has started does not update `sys.path`. A Host that publishes a new distribution directory at runtime should update `sys.path` itself and invalidate import caches after installation completes:

```python
import importlib
import sys
from pathlib import Path


def activate_plugin_directory(path: str | Path) -> Path:
    plugin_directory = Path(path).resolve()
    normalized = str(plugin_directory)
    if normalized not in sys.path:
        sys.path.append(normalized)
    importlib.invalidate_caches()
    return plugin_directory
```

Do not install incrementally into a directory already present on `sys.path`. Package-metadata discovery can observe directory changes before an explicit cache invalidation, so invalidation is not a publication barrier. Instead, install into a fresh immutable release directory that is not yet searchable. For example, an operator or trusted sidecar can stage a wheel on the same filesystem and rename the completed directory into place:

```bash
set -euo pipefail

release_dir=/srv/agent-plugin-releases/acme-audit-1.2.3
staging_dir=$(mktemp -d /srv/agent-plugin-releases/.acme-audit-1.2.3.XXXXXX)
python -m pip install --target "$staging_dir" ./dist/acme_harness_plugin-1.2.3-py3-none-any.whl
# Run the Host's artifact and dependency validation here.
test ! -e "$release_dir"
mv "$staging_dir" "$release_dir"
```

The destination must not already exist. Run the destination check and same-filesystem rename under the Host's installation lock; the shell sequence omits lock acquisition because that mechanism is deployment-specific. A failed installation or validation must leave the staging directory unpublished.

After the completed release directory is in place, serialize the Host's package-path publication and builder replacement, activate the exact release directory, optionally inspect available metadata, and construct a new builder:

```python
from converge_agent_harness import (
    HarnessBuildContext,
    HarnessBuilder,
    discover_harness_plugin_factory_references,
)

activate_plugin_directory(
    "/srv/agent-plugin-releases/acme-audit-1.2.3"
)

available_keys = {
    reference.plugin_key
    for reference in discover_harness_plugin_factory_references()
}
if "acme.audit" not in available_keys:
    raise RuntimeError("acme.audit is not available")

context = HarnessBuildContext.from_file("harness-plugins.yaml")
builder = HarnessBuilder(build_context=context)
```

Discovery reads the interpreter's current distribution metadata on each call. `HarnessBuilder` selects and loads the enabled factories during builder construction, then retains that factory selection. Therefore:

- a new builder can see a newly available distribution in the same process;
- an existing builder does not discover newly added or removed factory keys;
- each later `build()` on that builder still asks its retained factories for fresh plugin instances;
- an existing executable retains its already constructed plugin graph;
- runs already using an executable continue with that graph;
- installing a package grants no authority until configuration enables its key;
- unloading or replacing an already imported module in place is not supported by the plugin contract.

For a safe Host rollout, construct and validate the replacement builder or executable after publishing the complete release directory, and only then make it available to new runs. Keep the old executable alive until its active runs finish. Do not append two releases that provide the same plugin key to one interpreter. Upgrade already imported code under the same key or module identity with a replacement process whose search path contains only the new release.

The Host remains responsible for plugin artifact trust, dependency compatibility, installation durability, directory ordering, and any exact artifact lock. The Harness deliberately does not include a package installer or a process-global mutable plugin registry.

### Direct code composition

An embedded application can bypass package metadata and construct a plugin directly:

```python
from converge_agent_harness import HarnessBuilder
from pydantic_ai.agent.spec import AgentSpec

plugin = AuditPlugin("audit-primary")
agent = HarnessBuilder().build_code(
    AgentSpec(model="logical:example"),
    output_type=str,
    plugins=(plugin,),
)
```

Direct and configured plugins enter the same ordering, Agent binding, run binding, middleware, result validation, and cleanup path.

### Runnable example

The [integration package example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) contains a complete wheel-ready distribution with:

- an installed Harness plugin entry point;
- package-owned configuration validation;
- YAML-configured and direct-code composition;
- fresh run-bound plugin state;
- offline demos and focused tests.

Run it from the repository root with:

```bash
make examples-check-all
```

## Environment providers

An Environment provider owns one logical resource revision and implements the provider-neutral operations that the aggregate can route. Use this boundary for a new sandbox, workspace, remote execution service, or local resource implementation. Do not use it merely to run setup code around several providers.

A distribution registers a side-effect-free factory class under `converge_agent_harness.environments`:

```toml
[project.entry-points."converge_agent_harness.environments"]
"acme.sandbox" = "acme_environment.provider:AcmeEnvironmentProviderFactory"
```

The Host explicitly selects installed keys and creates fresh provider binding candidates:

```python
from converge_agent_harness import (
    build_environment_provider_factory_catalog,
    discover_environment_provider_factory_references,
)

available = {
    reference.provider_key
    for reference in discover_environment_provider_factory_references()
}
if "acme.sandbox" not in available:
    raise RuntimeError("acme.sandbox is not installed")

catalog = build_environment_provider_factory_catalog(
    provider_keys=("acme.sandbox",)
)
binding = catalog.create_provider_binding(
    "acme.sandbox",
    {"region": "us-west", "profile": "isolated"},
)
```

`create_provider_binding()` must return a fresh pre-entry-inert `EnvironmentProviderBinding`. Allocation, authentication, session entry, maintenance tasks, and cleanup-producing work belong in the binding's async `bind()` scope. The Host places candidates in `EnvironmentBindingRequest` values and constructs the aggregate with `create_environment_run_binding()`.

The factory key selects installed code. It is distinct from `provider_type`, `environment_id`, Harness `binding_id`, and model-facing alias. The Host owns desired topology, permission ceilings, lifecycle policy, and artifact trust.

## Environment run extensions

Use an `EnvironmentRunExtension` when setup and teardown need the stable complete `BoundEnvironment`, including zero, one, or several provider bindings. Examples include aggregate-wide workspace preparation, a run lease tied to the complete Environment, or a provider-neutral observer that must be released before provider scopes close.

```python
from contextlib import asynccontextmanager

from converge_agent_harness import (
    EnvironmentRunExtensionContext,
    create_environment_run_binding,
)


class WorkspaceMarkerExtension:
    def __init__(self, extension_id: str) -> None:
        self._extension_id = extension_id

    @property
    def extension_id(self) -> str:
        return self._extension_id

    @asynccontextmanager
    async def bind(self, *, context: EnvironmentRunExtensionContext):
        path = "/workspace/.active-run"
        await context.environment.files.write_text(
            path,
            f"{context.run_id}\n",
            mode="create",
        )
        try:
            yield
        finally:
            await context.environment.files.remove(path)


run_environment = create_environment_run_binding(
    initial_topology=topology,
    topology_limits=topology_limits,
    state_limits=state_limits,
    extensions=(WorkspaceMarkerExtension("workspace-marker"),),
)
```

The aggregate validates and freezes unique extension IDs at construction. After provider entry and optional Environment state restoration, it enters extensions in registration order, then activates the topology controller. A dynamic topology update does not re-enter them. At the terminal fence, extensions exit in reverse order while the `BoundEnvironment` is still open; provider-neutral operations remain available, but the controller no longer accepts topology changes. Setup failure prevents controller activation and unwinds already entered extensions.

An extension receives only `run_id`, `AgentInstanceContext`, and `BoundEnvironment`. It receives no `AgentContext`, model, Harness plugin exchange, topology controller, arbitrary metadata, or Capability registry. It is trusted process-local code but cannot widen provider authority through the facade.

### Publish and select an extension factory

A distribution can register a no-argument factory class under the separate group:

```toml
[project.entry-points."converge_agent_harness.environment_run_extensions"]
"acme.workspace-marker" = "acme_environment.extension:WorkspaceMarkerExtensionFactory"
```

Host code selects the key and supplies the instance ID separately from package-owned JSON configuration:

```python
from converge_agent_harness import (
    EnvironmentRunExtensionFactoryContext,
    build_environment_run_extension_factory_catalog,
)

catalog = build_environment_run_extension_factory_catalog(
    extension_keys=("acme.workspace-marker",)
)
extension = catalog.create_extension(
    EnvironmentRunExtensionFactoryContext(
        extension_key="acme.workspace-marker",
        extension_id="workspace-marker-primary",
        configuration={"path": "/workspace/.active-run"},
    )
)
```

Metadata discovery imports no targets. Empty selection scans nothing. Catalog construction imports only selected keys, supports explicit factory objects for embedding and tests, and returns an immutable snapshot. Factory construction and `create_extension()` remain side-effect free; resource acquisition belongs in `EnvironmentRunExtension.bind()`.

The Harness owns no Environment run-extension YAML schema. A hosted system maps its own durable, authorized configuration into factory contexts and aggregate construction.

## Capabilities and Toolsets

Capabilities and Toolsets are the native extension points inside the Pydantic Agent loop. Use them for model instructions, request hooks, tool schemas, run-local Agent behavior, and portable Capability state. A reusable Toolset should operate over a provider-neutral protocol; its Capability owns Agent/run binding and hook composition.

`DynamicEnvironmentCapability` is the optional model projection for `BoundEnvironment`. `SkillMaterializer` is a Capability-side ordered materialization contract. Neither is an Environment run extension: they operate inside Agent composition or run preparation rather than owning aggregate-wide Environment resource scopes.

## Complete runnable examples

The [integration package example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) includes wheel-ready implementations and offline tests for all package extension boundaries:

- Environment provider entry-point and explicit-factory modes;
- Environment run-extension entry-point and explicit-factory modes;
- Harness middleware YAML/entry-point and direct-object modes.

Run the complete suite from the repository root:

```bash
make examples-check-all
```
