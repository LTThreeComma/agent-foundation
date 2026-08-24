# Agent Stream Protocol

`converge-agent-stream-protocol` is the shared Harness-to-AG-UI projection and validation package for Converge Agent surfaces. It keeps WebUI, TUI, and optional hosted adapters on one ordered presentation protocol without making UI data an execution or continuation authority.

The repository directory is `packages/agent-stream-protocol`, the Python distribution is `converge-agent-stream-protocol`, and the import package is `converge_agent_stream_protocol`.

## Dependencies

The source manifest declares an unversioned dependency on `converge-agent-harness`, so uv resolves Harness from the workspace during repository development. Its projection boundary is built on the upstream `ag-ui-protocol` models, Pydantic validation, and the lightweight `pydantic-ai-slim` event runtime; it does not pull in Agent UI or a Host persistence model.

Release automation replaces the workspace-oriented dependency in publishable metadata with an exact same-version Harness requirement. Both the sdist and wheel therefore install only the Harness version released with that Stream Protocol artifact.

## Versioning

Agent Stream Protocol and `converge-agent-harness` form the Harness release group. A `release/harness-v<version>` tag publishes both distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`. Agent UI is versioned and released independently.

The accepted architecture and compatibility contract are defined in the [Agent Stream Protocol specification](../../spec/agent-stream-protocol/README.md).
