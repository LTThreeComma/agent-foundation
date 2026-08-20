# Agent Envd Client

`converge-agent-envd-client` is the low-level Python client package for the Agent Environment Interaction Protocol (EIP). It belongs to the agent-envd release group and is versioned and published together with `converge-agent-envd`.

## Status

The package currently reserves the distribution and import namespaces and exposes its installed package version. Generated protocol models, codecs, typed stubs, and stdio, HTTP, and WebSocket transports will be added when EIP implementation begins. There is no usable daemon client API yet.

The accepted protocol, generation, ownership, and compatibility design is documented in the [agent-envd specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/agent-envd/08-protocol-source-client-and-generation.md).

## Versioning

The Python package and daemon artifacts share one `X.Y.Z` agent-envd release identity. The negotiated EIP major and minor version remains an independent wire-compatibility identity.

## License

Licensed under the [Apache License 2.0](LICENSE).
