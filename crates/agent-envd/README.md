# agent-envd

`agent-envd` is the environment daemon and Environment Interaction Protocol provider for Agent Foundation. It is distributed as the `converge-agent-envd` crate and installs the `agent-envd` binary.

## Current runtime profile

The daemon implements the canonical EIP 1.0 protocol over trusted stdio pipes with content-length framing. Its current surface includes:

- initialization, environment description, session close, cancellation, operation receipts, and local port observation;
- configured, capability-confined mounts for text reads, metadata, listing, bounded find and search, and binary streaming reads;
- atomic text and binary writes, directory creation, patch, copy, move, and remove on eligible writable mounts;
- structured foreground commands and generation-owned background processes through one gated supervisor and backend lifecycle owner;
- bounded stdin, stdout, stderr, live retained-output references, non-draining process cursors, signals, kill, wait, and release;
- bounded operation, process, transfer, staging, receipt, and retained-output state;
- correlated concurrent requests, fresh generations, strict framing and envelope validation, finite deadlines, cancellation, and typed errors.

Every descriptor advertises `environment.describe`, `operation.cancel`, `port.observe`, `receipt.read`, and `session.close`. It advertises the complete `file.read`, `file.write`, `file.find`, or `file.search` capability only when at least one configured mount supports that complete family. Atomic writable mounts are currently available on Linux and macOS.

When command policy and a command-enabled mount are configured, the descriptor also advertises `shell.exec`, `process.manage`, and `output.read`, plus its logical shell profiles. Foreground and background execution share one transactional start gate; `process.start` publishes a handle only after requested-executable spawn succeeds. The same owner drains stdout and stderr concurrently, enforces finite limits, targets the backend-managed command lifecycle for control and cleanup, and drains remaining managed commands during daemon shutdown. In explicit `disabled` mode, native cleanup covers the initial Unix process group and a best-effort Windows task tree; descendants outside that platform-native target remain the outer Host's responsibility, as reported by `process_containment=false` and `cleanup_guarantee=outer_host`. HTTP, WebSocket, and required native execution isolation are not implemented yet.

## Launch configuration

The daemon requires:

```text
AGENT_ENVD_ENVIRONMENT_ID=<provider-owned stable identity>
AGENT_ENVD_EXECUTION_ISOLATION=disabled
```

Command execution additionally requires an absolute private runtime directory:

```text
AGENT_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
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

Command policy supplies fixed executable search roots and optional trusted shell profiles. A command-enabled mount must allow `command_cwd`; relative executables additionally require `executable_source`:

```json
{
  "mounts": [
    {
      "mount_id": "workspace",
      "native_root": "/absolute/path/to/workspace",
      "writable": false,
      "exclusive_mutation_control": false,
      "allow_command_execution": true,
      "max_file_bytes": 104857600,
      "allowed_operations": [
        "stat",
        "read_text",
        "open_reader",
        "list",
        "command_cwd",
        "executable_source"
      ]
    }
  ],
  "trusted_executable_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
  "shell_profiles": [
    {
      "profile_id": "sh",
      "display_name": "POSIX shell",
      "native_executable": "/bin/sh",
      "fixed_arguments": ["-c"],
      "safe_base_environment": {},
      "executable_search_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
      "max_script_bytes": 1048576,
      "allow_login_mode": false
    }
  ]
}
```

Payloads receive a finite allowlist of ordinary locale, terminal, certificate, and language-tool compatibility variables from daemon startup. Request `set` and `unset` values apply after that layer. Envd always replaces `PATH`, `HOME`, and temporary-directory variables and removes daemon control state, `AGENT_ENVD_*`, ambient credentials, and dynamic-loader variables.

## Installation

```bash
cargo install converge-agent-envd
```

## License

Licensed under the Apache License 2.0.
