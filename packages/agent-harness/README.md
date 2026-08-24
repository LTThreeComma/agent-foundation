# Agent Harness

`converge-agent-harness` is the process-local Pydantic AI execution foundation for Converge agents. The repository directory is `packages/agent-harness`, the Python distribution is `converge-agent-harness`, and the import package is `converge_agent_harness`.

## Capability composition

Agent definitions compose behavior through Pydantic AI Capabilities. The first-party feature Capabilities own lifecycle hooks and select pure Toolsets; the Toolsets depend only on provider-neutral ports such as `FileOperator`, `MediaReader`, `DocumentConverter`, and `WebClient`.

```python
from converge_agent_harness import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    HandoffCapability,
    RuntimeContextCapability,
    UserInteractionCapability,
    WorkingStateCapability,
)

capabilities = (
    DynamicEnvironmentCapability(
        DynamicEnvironmentConfiguration(
            file_tools=True,
            shell_tools=True,
            process_tools=True,
            port_tools=False,
            max_topology_bindings=16,
            max_topology_bytes=64 * 1024,
            max_reference_entries=1_024,
        )
    ),
    RuntimeContextCapability(),
    HandoffCapability(),
    WorkingStateCapability(),
    UserInteractionCapability(),
)
```

Embedding code supplies current Environment and provider collaborators through `RunBindings`. Media, document, and Web implementations stay behind their typed run collaborators rather than becoming dependencies of the Harness core. Static callers may import reusable Toolsets from `converge_agent_harness.toolsets`; their model-facing JSON results use named `TypedDict` contracts in the corresponding Toolset modules.

## Execution boundary and filters

Every built Agent includes one outer `ToolExecutionBoundaryCapability` and one innermost `MessageIntegrityFilterCapability`. The execution boundary preserves ordinary Pydantic dispatch while applying the code-owned redaction, size, and spill policy to locally executable function-tool text/JSON results. Complete trusted `HarnessToolMetadata` additionally selects managed authorization, credentials, grants, retry, and invocation events.

Request/history filters live in `converge_agent_harness.filters`. Message integrity is mandatory; `ContentFilterCapability` and `ColdStartFilterCapability` are optional definition-selected filters for native multimodal request compatibility and cold-cache reduction of already-consumed tool-result strings. Model-specific one-shot history repair remains in `SelfHealingModel`, and interrupted-stream semantic retry remains in Harness recovery rather than either filter.

## Versioning

Agent Harness and `converge-agent-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag publishes both distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`. The published Stream Protocol artifact pins this exact Harness version; Agent UI releases independently and selects a Harness release explicitly.

The accepted architecture and public contract are defined in the [Agent Harness specification](../../spec/agent-harness/README.md).
