# Integration Package Examples

This standalone project demonstrates the supported configuration and direct-code composition modes for the Agent Harness package-extension boundaries. Every path is runnable offline and covered by focused tests.

## Composition Matrix

| Boundary    | Installed entry-point mode                                                                                                                     | Explicit code mode                                                                                            |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Environment | Select an `EnvironmentProviderFactory` from `converge_agent_harness.environments`, then call `create_provider_binding()`                       | Supply an `EnvironmentProviderFactory` object directly, then call the same `create_provider_binding()` method |
| Harness     | Let a `HarnessBuildContext` load preferred YAML or JSON, select enabled `HarnessPluginFactory` entries, and apply fresh instances during build | Construct an `AbstractHarnessPlugin` directly and place it in `AgentDefinition.plugins`                       |

Entry-point metadata provides only a stable key and import target. Harness middleware configuration uses the Harness-owned versioned envelope; YAML is preferred for files, JSON is supported for files and inline environment values, and each plugin package owns only the typed `configuration` payload.

Both entry-point paths are explicit and lazy:

1. metadata discovery does not import target modules;
2. Environment callers select an exact stable key, while the Harness builder selects only enabled document keys;
3. catalog construction imports only those selected targets;
4. factory output enters the same ordinary concrete-object path used by code mode.

Package presence is availability, not authorization. Neither catalog accepts an arbitrary import path or mutates a process-global registry.

## Quick Start

From the repository root:

```bash
make examples-check-all
```

From this directory:

```bash
uv sync --locked
uv run plugin-example-environment-entrypoint
uv run plugin-example-environment-code
uv run plugin-example-harness-entrypoint
uv run plugin-example-harness-code
uv run pytest
```

The project is intentionally outside the root release workspace. Its independent lock resolves the local checkout through:

```toml
[tool.uv.sources]
converge-agent-harness = { path = "../../packages/agent-harness", editable = true }
```

A standalone plugin distribution should remove that development source and declare the released `converge-agent-harness` range it supports.

## Environment Provider Factory

The distribution registers one package factory:

```toml
[project.entry-points."converge_agent_harness.environments"]
"example.workspace" = "converge_plugin_examples.environment:WorkspaceEnvironmentProviderFactory"
```

[`environment.py`](src/converge_plugin_examples/environment.py) contains:

- a strict package-owned `WorkspaceEnvironmentConfiguration` model;
- a no-argument, side-effect-free `WorkspaceEnvironmentProviderFactory` factory;
- `create_provider_binding()`, which returns a fresh, pre-entry-inert Direct Local binding.

The factory does not create directories, open sessions, authenticate, or acquire cleanup-producing resources. Those operations belong to the returned binding's async lifecycle.

### Installed entry-point mode

[`run_environment_entrypoint_demo()`](src/converge_plugin_examples/demo_environment.py) discovers metadata, verifies that `example.workspace` is installed, selects only that key, and invokes the selected factory twice with Host-supplied JSON configuration.

```bash
uv run plugin-example-environment-entrypoint
```

### Explicit code mode

[`run_environment_code_demo()`](src/converge_plugin_examples/demo_environment.py) imports and supplies `WorkspaceEnvironmentProviderFactory()` directly. It still invokes the same catalog factory method, so package-specific configuration validation and provider-binding validation remain identical.

```bash
uv run plugin-example-environment-code
```

Both paths create two bindings, assemble one topology, enter and activate the aggregate, then verify default and alias-qualified routing:

```text
selection mode: entrypoint
selected provider: example.workspace
active aliases: source, docs
default route: source workspace
docs route: documentation workspace
```

Code mode prints the same result with `selection mode: code`.

### Real provider checklist

- Use one stable entry-point name and return the same value from `provider_key()`.
- Keep no-argument factory construction safe and side-effect free.
- Define and document a strict bounded configuration schema.
- Return a fresh `EnvironmentProviderBinding` from each factory call.
- Defer allocation, authentication, session creation, and cleanup-producing work to `bind()`.
- Publish only operations the entered provider can enforce.
- Keep aliases, permission ceilings, topology limits, artifact authorization, and run selection under Host control.
- Resolve current credentials through a fresh provider or Host boundary rather than persisting them in configuration.

The example delegates operations to `DirectLocalEnvironmentProviderBinding` to stay focused on packaging and selection. A remote provider implements its own binding and entered-provider contracts.

## Harness Plugin

The distribution registers one package factory:

```toml
[project.entry-points."converge_agent_harness.plugins"]
"example.run-recorder" = "converge_plugin_examples.harness:RunRecorderPluginFactory"
```

[`harness.py`](src/converge_plugin_examples/harness.py) contains:

- a strict package-owned `RunRecorderConfiguration` model for the `configuration` payload;
- `RunRecorderPluginFactory`, which receives standardized plugin key/ID, package parameters, and namespaced extensions through `HarnessPluginFactoryContext`;
- `RunRecorderPlugin`, with stable ordering and fresh exact-type run binding;
- small immutable observation records in a demo-only in-memory sink that does not retain prompts or model output.

[`records.py`](src/converge_plugin_examples/records.py) holds neutral result types so metadata discovery and demo imports do not import the plugin target early.

### Configured installed mode

[`harness-plugins.yaml`](src/converge_plugin_examples/harness-plugins.yaml) is the complete configuration-file example:

```yaml
schema_version: "1"
plugins:
  - plugin_id: recorder-entrypoint
    plugin_key: example.run-recorder
    enabled: true
    configuration:
      count_events: true
  - plugin_id: recorder-disabled
    plugin_key: example.run-recorder
    enabled: false
    configuration:
      count_events: false
```

`plugin_id`, `plugin_key`, and `enabled` belong to the Harness envelope. `count_events` is a real package-owned parameter validated by `RunRecorderConfiguration` and used directly by the plugin. Disabled entries are neither imported on their own nor created.

A Host does not need a file. It can pass the same data schema directly:

```python
context = HarnessBuildContext.from_configuration(
    {
        "schema_version": "1",
        "plugins": [
            {
                "plugin_id": "recorder-entrypoint",
                "plugin_key": "example.run-recorder",
                "enabled": True,
                "configuration": {"count_events": True},
            }
        ],
    }
)
builder = HarnessBuilder(build_context=context)
```

[`run_harness_entrypoint_demo()`](src/converge_plugin_examples/demo_harness.py) uses the optional file form and loads the YAML with `HarnessBuildContext.from_file()`. `HarnessBuilder` selects the installed `example.run-recorder` entry point and builds the concrete middleware without exposing the factory or plugin object in `AgentDefinition`:

```bash
uv run plugin-example-harness-entrypoint
```

A hosted create-and-run or create-and-stream path can keep the deployment default disabled and opt one executable construction in without rewriting configuration:

```bash
export CONVERGE_HARNESS_PLUGIN_CONFIG_ENABLED=false
export CONVERGE_HARNESS_PLUGIN_CONFIG_FILE=/etc/converge/harness-plugins.yaml
```

```python
builder = HarnessBuilder(configured_plugins_enabled=True)
```

`True` overrides only the enable switch; source precedence, validation, selected-key import, and failure behavior remain identical. The choice is fixed when the Agent is built because plugins may contribute Capabilities, tools, settings, instructions, and hooks. `run()` and `stream()` therefore do not expose an unsafe partial late toggle.

### Long-lived Host plugin directory

A Host may install a complete plugin distribution into a fresh directory that is not yet searchable, publish that directory on Python's package search path while the Host remains alive, invalidate Python's import caches, and construct a new builder. The new builder sees the current entry-point metadata. Existing builders retain their selected factories and create fresh plugin instances from them on later builds; existing executables retain their already constructed plugin graphs. This supports adding plugins without rebuilding the Host image or restarting its Python process, but it does not define in-place reload or replacement of an already imported module.

Use `PYTHONPATH` or `sys.path` for Python packages; the shell executable `PATH` is unrelated. Never install incrementally into a directory already exposed to the running process. The completed distribution must include `.dist-info` entry-point metadata rather than only the import module. See the [Harness plugin guide](../../docs/agent-harness/plugins.md#use-a-host-managed-plugin-directory) for the complete Host sequence and rollout boundaries.

### Explicit code mode

[`run_harness_code_demo()`](src/converge_plugin_examples/demo_harness.py) constructs `RunRecorderPlugin` directly. This mode can inject a Python observation sink that cannot be represented as JSON:

```python
observations: list[RunObservation] = []
plugin = RunRecorderPlugin(
    plugin_id="recorder-code",
    observation_sink=observations,
)
```

```bash
uv run plugin-example-harness-code
```

Both paths use an offline `FunctionModel` and produce a deterministic result apart from the generated run ID and positive event count:

```text
selection mode: entrypoint
plugin id: recorder-entrypoint
run id: <generated run ID>
output: offline model response
observed status: completed
observed events: <positive count>
```

Code mode reports `selection mode: code` and `plugin id: recorder-code`.

### Real middleware checklist

- Give every concrete instance a unique, stable, non-blank `plugin_id`.
- Keep the package factory no-argument and side-effect free.
- Accept standardized identity and extensions through `HarnessPluginFactoryContext`; validate only the package-owned `configuration` payload.
- Keep the Agent-bound plugin reentrant.
- Return an exact-type run-isolated instance from `for_run()` when run state is mutable.
- Express ordering through `PluginOrdering`.
- Call `call_next(exchange)` at most once and put cleanup in the returned async iterator.
- Do not retain credentials, raw prompts, model output, or unbounded event payloads.
- Use a production-owned async sink when observations leave the process.

The example list intentionally has no long-term retention policy; it only keeps each record small. A long-lived application must inject a sink with its own bounded retention, backpressure, and export policy.

Harness middleware is trusted in-process code. Behavior inside the Pydantic Agent loop belongs in a Capability rather than a wider `wrap_run()` layer.

## Tests and Packaging

[`test_environment.py`](tests/test_environment.py) verifies:

- metadata-only discovery and selected-only import;
- installed and explicit factory modes;
- pre-entry factory inertness;
- package configuration validation;
- real two-binding routing through both modes.

[`test_harness.py`](tests/test_harness.py) verifies:

- metadata-only discovery and selected-only import;
- configured YAML factory and explicit concrete-object modes;
- standardized context and package parameter validation;
- both complete offline demos;
- isolated state across concurrent logical runs.

The fast gate validates locks, style, and types:

```bash
make examples-check
```

The complete gate also runs focused tests and every smoke path, then builds each wheel and source distribution:

```bash
make examples-check-all
```

For a shorter loop inside this directory:

```bash
uv run --locked ruff check --no-fix .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```
