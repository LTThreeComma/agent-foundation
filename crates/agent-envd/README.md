# agent-envd

`agent-envd` is the environment daemon and Environment Interaction Protocol provider for Agent Foundation. It is distributed as the `converge-agent-envd` crate and installs the `agent-envd` binary.

## Current runtime profile

The daemon implements the canonical EIP 1.0 protocol over trusted stdio pipes with content-length framing. Its current surface includes:

- initialization, environment description, session close, cancellation, operation receipts, and local port observation;
- configured, capability-confined mounts for text reads, metadata, listing, bounded find and search, and binary streaming reads;
- atomic text and binary writes, directory creation, patch, copy, move, and remove on eligible writable mounts;
- bounded operation, transfer, staging, receipt, and retained-output state;
- correlated concurrent requests, fresh generations, strict framing and envelope validation, finite deadlines, and typed errors.

Every descriptor advertises `environment.describe`, `operation.cancel`, `port.observe`, `receipt.read`, and `session.close`. It advertises the complete `file.read`, `file.write`, `file.find`, or `file.search` capability only when at least one configured mount supports that complete family. Atomic writable mounts are currently available on Linux and macOS.

The retained-output substrate and `output.*` handlers are implemented, but that capability is intentionally not advertised until a command or process producer exists. `shell.exec` and `process.*` currently return the typed `unsupported` error. HTTP, WebSocket, command execution, process management, and native execution isolation are not implemented yet.

## Launch configuration

The daemon requires:

```text
AGENT_ENVD_ENVIRONMENT_ID=<provider-owned stable identity>
AGENT_ENVD_EXECUTION_ISOLATION=disabled
```

`AGENT_ENVD_TRANSPORT` defaults to `stdio`. Network mode is not available yet.

Native required isolation remains the secure default: omitting `AGENT_ENVD_EXECUTION_ISOLATION`, or setting it to `required`, fails startup until that backend exists. Explicit `disabled` mode delegates containment to the outer Host, emits one structured startup warning on stderr, and must be used only inside an appropriate provider sandbox or test boundary.

In stdio mode, stdin and stdout are reserved exclusively for framed EIP traffic; startup and runtime diagnostics use stderr.

An optional trusted JSON file configures mounts and is supplied as an absolute path:

```bash
agent-envd --config /absolute/path/to/agent-envd.json
```

A writable mount needs a private staging directory on the same filesystem and exclusive mutation control:

```json
{
  "mounts": [
    {
      "mount_id": "workspace",
      "native_root": "/absolute/path/to/workspace",
      "staging_root": "/absolute/path/to/private-staging",
      "writable": true,
      "exclusive_mutation_control": true,
      "allow_command_execution": false,
      "max_file_bytes": 104857600
    }
  ]
}
```

Both directories must already exist. The staging directory must be private to the daemon. Omitting `allowed_operations` enables the operations appropriate for the mount; specifying it narrows the policy.

## Installation

```bash
cargo install converge-agent-envd
```

## License

Licensed under the Apache License 2.0.
