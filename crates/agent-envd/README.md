# agent-envd

`agent-envd` is the environment daemon and Environment Interaction Protocol provider for Agent Foundation. It is distributed as the `converge-agent-envd` crate and installs the `agent-envd` binary.

## Current runtime profile

The first runtime block supports the canonical EIP 1.0 JSON-RPC protocol over trusted stdio pipes with content-length framing. It implements:

- `initialize`;
- `environment.describe`;
- `session.close`;
- bounded concurrent request admission and correlated responses;
- fresh cryptographically random generations for every daemon start;
- strict startup, framing, envelope, session, capability, and response limits.

The descriptor advertises only `environment.describe` and `session.close`. Other known EIP 1.0 methods return the typed `unsupported` error, and unknown methods return JSON-RPC `method_not_found`.

HTTP, WebSocket, resources, retained output, commands, processes, and native execution isolation are not implemented yet.

## Launch configuration

Block 1 requires:

```text
AGENT_ENVD_ENVIRONMENT_ID=<provider-owned stable identity>
AGENT_ENVD_EXECUTION_ISOLATION=disabled
```

`AGENT_ENVD_TRANSPORT` defaults to `stdio`. Network mode is not available yet.

Native required isolation remains the secure default: omitting `AGENT_ENVD_EXECUTION_ISOLATION`, or setting it to `required`, fails startup until that backend exists. Explicit `disabled` mode delegates containment to the outer Host, emits one structured startup warning on stderr, and must be used only inside an appropriate provider sandbox or test boundary.

In stdio mode stdin and stdout are reserved exclusively for framed EIP traffic; startup and runtime diagnostics use stderr.

## Installation

```bash
cargo install converge-agent-envd
```

## License

Licensed under the Apache License 2.0.
