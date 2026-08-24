# agent-envd Specifications

## Overview

This directory defines the current design of `agent-envd`, the first-class, client-neutral Environment host and data-plane daemon for daemon-governed local, sandboxed, and remote resources. One daemon serves one user and one Environment for one process generation. `agent-envd` implements the versioned Environment Interaction Protocol (EIP) with JSON-RPC control, raw bidirectional file transfer, and bounded command, process, port, and retained-output operations.

The Harness is one client through its typed provider-neutral [`Environment`](../agent-harness/08-environment-integration.md) abstraction. Docker, E2B, remote, and optional local-daemon adapters provision or attach the native resource and establish an EIP connection. The first-party Direct Local provider remains equally first-class and does not require this daemon. Product file gateways, CLIs and IDEs, provider controllers, and trusted background jobs can consume the low-level EIP client without importing Harness. Browser users remain behind product authentication and scoped gateway policy; they never receive the daemon key. The Foundation Service defines no platform-owned Sandbox resource.

`agent-envd` can apply its own OS-native command isolation with Linux bubblewrap or macOS Seatbelt. That layer defaults to fail-closed `required` mode for a daemon running directly on a user machine. A deployment whose outer sandbox already owns command containment explicitly selects `disabled`; this disables only envd's inner OS sandbox and does not disable transport authentication, session-selected EIP file and cwd authority, handle ownership, output bounds, quotas, or lifecycle cleanup. Resource authority and command isolation are independent: a session defaults to configured `scoped` mounts and can request the daemon-visible `server` filesystem only when startup configuration explicitly permits it. In disabled isolation, the outer sandbox determines which host resources the child can actually reach.

## Document Catalog

| Document                                                                                   | Owning contract                                                                                                                                 |
| ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                           | Subsystem position, major components, authority boundaries, end-to-end flow, and stable principles                                              |
| [01-daemon-lifecycle-and-configuration.md](01-daemon-lifecycle-and-configuration.md)       | Trusted startup configuration, Environment identity and generation, readiness, admission, shutdown, and observability                           |
| [02-eip-protocol.md](02-eip-protocol.md)                                                   | JSON-RPC control, binary-transfer lifecycle, initialization, methods, handles, receipts, errors, cancellation, idempotency, and versioning      |
| [03-transports-and-sessions.md](03-transports-and-sessions.md)                             | Stdio control/data framing, authenticated HTTP bodies, WebSocket text/binary messages, sessions, browser trust boundary, and transport failure  |
| [04-resource-operations.md](04-resource-operations.md)                                     | Scoped/server authority, mount-local paths, bounded text and raw transfer, complete-candidate publication, search, mutations, and ports         |
| [05-command-and-process-execution.md](05-command-and-process-execution.md)                 | Structured commands, shell profiles, transactional spawn, process records, foreground/background lifecycle, signaling, and cleanup              |
| [06-output-retention.md](06-output-retention.md)                                           | Optional EIP `OutputPolicy`, generous daemon defaults, producer-side bounds, generation-scoped references, cursors, expiry, and gaps            |
| [07-execution-isolation.md](07-execution-isolation.md)                                     | `required` and `disabled` modes, filesystem and child-environment policy, Linux bubblewrap, macOS Seatbelt, network policy, probes, and posture |
| [08-protocol-source-client-and-generation.md](08-protocol-source-client-and-generation.md) | Canonical IDL, generated Rust/Python protocol surfaces, dedicated client package, direct Harness adapter, validation, and co-release contract   |

## Reading Paths

### Understand agent-envd

Read `00`, `01`, and `02`. Then read `03` for the selected transport, `04` through `07` for operation and security behavior, and `08` for protocol/client realization.

### Integrate an Environment provider

Read `00`, `01`, `02`, `03`, and `08`, then [Harness Environment Integration](../agent-harness/08-environment-integration.md). A Docker, E2B, remote, or optional local-daemon adapter owns provisioning and connection setup, but every daemon-backed Environment operation crosses the same generated client and EIP contract. Direct-local integration remains owned by the Harness Environment specification.

### Integrate another trusted EIP client

Read `00`, `02`, `03`, `04`, and `08`. Product file gateways, CLIs, IDEs, controllers, and background jobs use the low-level client directly while retaining their own caller identity and policy. A browser-facing gateway must also follow the product trust boundary in `03`; it never forwards the daemon key or transfer handles to the browser.

### Implement files, commands, or processes

Read `02`, `03`, `04`, `05`, `06`, and `08`. File transfer spans protocol control, transport data framing, and resource commit semantics; retained output remains separate. Read `07` before implementing any command start path: foreground and background commands share one transactional execution and containment boundary.

### Review security or resource lifetime

Read `01`, `02`, `03`, `04`, `06`, and `07` together with [Harness Security, Compatibility, and Trade-offs](../agent-harness/15-security-compatibility-and-tradeoffs.md).

## Authority Rules

- The Host owns provider selection, provisioning, lifecycle credentials, opaque adapter lifecycle records, and creation of fresh `EnvironmentRunBinding` values. Vendor credentials never enter the target Environment or daemon.
- The Harness owns multi-Environment selection, routing, and run-bound adaptation. It does not own provider-native resources, EIP sessions, or native process trees.
- EIP owns transport-neutral control methods, raw file-transfer lifecycle, payloads, errors, capabilities, handles, generations, cancellation, idempotency, receipts, and output dispositions. Canonical IDL and generated language surfaces realize that contract without becoming a second semantic owner.
- The selected transport authenticates the peer and establishes a session. Transport identity and session state never come from ordinary EIP method params.
- `agent-envd` owns Environment-local canonicalization, configured resource-authority ceiling, session-effective mounts, command policy, staged-candidate ownership, process-tree lifecycle, native command isolation when enabled, handles, producer-side output bounds, aggregate retained-output quotas, release and expiry, and operation-owned provider receipts.
- An outer sandbox owns containment outside envd only when the operator explicitly configures envd isolation as `disabled`. No platform detection or failed probe can select that mode implicitly.
- One API key or trusted stdio parent authenticates the daemon's single user. Sessions negotiate protocol metadata and select an immutable `scoped` or operator-permitted `server` resource view; this is not a multi-principal permission boundary. Sessions own only ephemeral file readers, pre-handoff writers, and data attachments, not processes or retained output.
- Neither a descriptor, handle, cursor, receipt, nor model-supplied identifier grants authority. Every operation is checked against the authenticated user, current daemon generation, capability, and configured ceiling.
- Files are native Environment state. Process, operation-owned receipt/idempotency evidence, cursor, and retained-output records are volatile daemon-generation state and are never exported or restored through EIP; a crash-left destination-local candidate is native state, not a restorable writer.

## Specification Conventions

- Python-like schemas are conceptual unless a section explicitly labels them as serialized wire schemas. EIP wire names use `snake_case` JSON fields.
- EIP control envelopes are JSON-RPC 2.0. Batch requests and JSON-RPC notifications are not part of EIP; raw file bytes use only the correlated data plane.
- Stdio, HTTP, and WebSocket are carrier profiles over one control and transfer protocol, not separate APIs.
- “Session” means an authenticated, initialized EIP protocol session. It is distinct from a TCP connection, WebSocket transport connection, Harness run, Host execution, and provider lifecycle session.
- “Initial command” means the executable requested by `shell.exec` or `process.start`; a sandbox helper or supervisor is not the initial command.
- Identifiers and handles are selectors, not bearer credentials.
- A provider receipt records an observed provider outcome. It does not imply Host durable completion.
- Provider-specific provisioning and bootstrap behavior stays outside the transport-neutral EIP method catalog.
