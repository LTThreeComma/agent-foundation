# Harness Plugins

Harness plugins are trusted Python middleware around a complete process-local Harness run. Use a Pydantic AI Capability for behavior inside the Agent loop; use a Harness plugin when behavior must wrap semantic input, stream events, and the complete result boundary.

A plugin can be supplied directly as an `AbstractHarnessPlugin` object or published as a Python distribution with a `HarnessPluginFactory` entry point. The distribution mode lets a Host select installed plugins from bounded YAML or JSON without accepting arbitrary Python import targets.

## Publish a plugin distribution

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

## Select configured plugins

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

## Use a Host-managed plugin directory

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

## Direct code composition

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

## Runnable example

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
