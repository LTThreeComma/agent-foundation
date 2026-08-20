# agent-envd Specifications

## Overview

This directory defines the current design of `agent-envd`, the first-class Environment data-plane daemon for daemon-governed local, sandboxed, and remote execution. `agent-envd` implements the versioned Environment Interaction Protocol (EIP) for bounded file, command, process, port, state, and retained-output operations.

The Harness consumes only its typed provider-neutral [`Environment`](../agent-harness/08-environment-integration.md) abstraction. Docker, E2B, remote, and optional local-daemon adapters provision or attach the native resource and establish an EIP connection. Direct `LocalFileOperator` and `LocalShell` bindings remain equally first-class and do not require this daemon. The Foundation Service defines no platform-owned Sandbox resource.

`agent-envd` can apply its own OS-native command isolation with Linux bubblewrap or macOS Seatbelt. That layer defaults to fail-closed `required` mode for a daemon running directly on a user machine. A deployment whose outer sandbox already owns command containment explicitly selects `disabled`; this disables only envd's inner OS sandbox and does not disable transport authentication, EIP file and cwd policy, handle ownership, output bounds, quotas, or lifecycle cleanup. In disabled mode, the outer sandbox determines which host resources the child can actually reach.

## Document Catalog

| Document                                                                                   | Owning contract                                                                                                                                   |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                           | Subsystem position, major components, authority boundaries, end-to-end flow, and stable principles                                                |
| [01-daemon-lifecycle-and-configuration.md](01-daemon-lifecycle-and-configuration.md)       | Trusted startup configuration, Environment identity and generation, readiness, admission, shutdown, and observability                             |
| [02-eip-protocol.md](02-eip-protocol.md)                                                   | Shared JSON-RPC envelope, initialization, operation context, method catalog, handles, receipts, errors, cancellation, idempotency, and versioning |
| [03-transports-and-sessions.md](03-transports-and-sessions.md)                             | Stdio framing, env-secret-authenticated HTTP, WebSocket upgrade and initialization handshake, session lifecycle, and transport failure            |
| [04-resource-operations.md](04-resource-operations.md)                                     | Mount-scoped paths, filesystem methods, local port observation, recoverable Environment state, canonicalization, and mutation semantics           |
| [05-command-and-process-execution.md](05-command-and-process-execution.md)                 | Structured commands, shell profiles, transactional spawn, process records, foreground/background lifecycle, signaling, cleanup, and leases        |
| [06-output-retention.md](06-output-retention.md)                                           | EIP `OutputPolicy`, Harness policy translation, producer-side bounds, aggregate retention quotas, references, cursors, expiry, and gaps           |
| [07-execution-isolation.md](07-execution-isolation.md)                                     | `required` and `disabled` modes, filesystem and child-environment policy, Linux bubblewrap, macOS Seatbelt, network policy, probes, and posture   |
| [08-protocol-source-client-and-generation.md](08-protocol-source-client-and-generation.md) | Canonical IDL, generated Rust/Python protocol surfaces, dedicated client package, direct Harness adapter, validation, and co-release contract     |

## Reading Paths

### Understand agent-envd

Read `00`, `01`, and `02`. Then read `03` for the selected transport, `04` through `07` for operation and security behavior, and `08` for protocol/client realization.

### Integrate an Environment provider

Read `00`, `01`, `02`, `03`, and `08`, then [Harness Environment Integration](../agent-harness/08-environment-integration.md). A Docker, E2B, remote, or optional local-daemon adapter owns provisioning and connection setup, but every daemon-backed Environment operation crosses the same generated client and EIP contract. Direct-local integration remains owned by the Harness Environment specification.

### Implement files, commands, or processes

Read `02`, `04`, `05`, `06`, and `08`. Read `07` before implementing any command start path: foreground and background commands share one transactional execution and containment boundary.

### Review security or recovery

Read `01`, `03`, `06`, and `07` together with [Harness Security, Compatibility, and Trade-offs](../agent-harness/15-security-compatibility-and-tradeoffs.md) and [Harness State and Resume](../agent-harness/10-snapshot-and-resume.md).

## Authority Rules

- The Host owns provider selection, provisioning, lifecycle credentials, opaque adapter lifecycle records, and creation of fresh `EnvironmentRunBinding` values. Vendor credentials never enter the target Environment or daemon.
- The Harness owns multi-Environment selection, routing, and run-bound adaptation. It does not own provider-native resources, EIP sessions, or native process trees.
- EIP owns transport-neutral method names, payloads, errors, capabilities, handles, generations, cancellation, idempotency, receipts, and output dispositions. Canonical IDL and generated language surfaces realize that contract without becoming a second semantic owner.
- The selected transport authenticates the peer and establishes a session. Transport identity and session state never come from ordinary EIP method params.
- `agent-envd` owns Environment-local canonicalization, configured mount and command policy, process-tree lifecycle, native command isolation when enabled, handles, producer-side output bounds, aggregate retained-output quotas, release and expiry, and provider receipts.
- An outer sandbox owns containment outside envd only when the operator explicitly configures envd isolation as `disabled`. No platform detection or failed probe can select that mode implicitly.
- Neither a descriptor, handle, cursor, receipt, saved Environment state, nor model-supplied identifier grants authority. Every operation is checked against the authenticated current session, generation, configured ceiling, and any required invocation grant.
- Adapter lifecycle continuation is consumed before binding creation and remains outside Harness `EnvironmentState`, which contains only backend-local recoverable data.

## Specification Conventions

- Python-like schemas are conceptual unless a section explicitly labels them as serialized wire schemas. EIP wire names use `snake_case` JSON fields.
- EIP envelopes are JSON-RPC 2.0. Batch requests are not part of EIP.
- Stdio, HTTP, and WebSocket are transport profiles over one protocol, not separate APIs.
- “Session” means an authenticated, initialized EIP protocol session. It is distinct from a TCP connection, WebSocket transport connection, Harness run, Host execution, and provider lifecycle session.
- “Initial command” means the executable requested by `shell.exec` or `process.start`; a sandbox helper or supervisor is not the initial command.
- Identifiers and handles are selectors, not bearer credentials.
- A provider receipt records an observed provider outcome. It does not imply Host durable completion.
- Provider-specific provisioning and bootstrap behavior stays outside the transport-neutral EIP method catalog.
