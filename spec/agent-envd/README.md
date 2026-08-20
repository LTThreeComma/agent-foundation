# agent-envd Specifications

## Overview

This directory defines the current design of `agent-envd`, the first-class Environment data-plane daemon for sandboxed and remote execution. `agent-envd` implements the Environment Interaction Protocol (EIP) for bounded file, shell, process, port, state, and capability operations.

The Harness consumes only its typed provider-neutral [`Environment`](../agent-harness/08-environment-integration.md) abstraction. Docker, E2B, remote, and optional local-daemon provider adapters ensure a compatible `agent-envd` is reachable and create fresh EIP-backed run bindings. Direct `LocalFileOperator` and `LocalShell` bindings are equally first-class and do not require this daemon. The Foundation Service does not own or deploy a separate Sandbox subsystem.

## Document Catalog

| Document                         | Owning contract                                                                                                                         |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md) | Subsystem boundaries, provider integration, shared JSON-RPC protocol, stdio/HTTP/WebSocket transports, authority, lifecycle, and errors |

## Reading Paths

### Understand Environment execution

Read `00`, then [Harness Environment Integration](../agent-harness/08-environment-integration.md) for multi-Environment binding, routing, state, and Agent-facing behavior.

### Add an Environment provider

Read `00` for the daemon-backed provider and protocol boundary. A Docker, E2B, remote, or optional local-daemon integration owns provisioning and connection setup, but its Environment operations cross the same EIP contract implemented by `agent-envd`. Direct-local integration is owned by the Harness Environment specification.

### Review security or recovery

Read `00` together with [Harness Security, Compatibility, and Trade-offs](../agent-harness/15-security-compatibility-and-tradeoffs.md) and [Harness State and Resume](../agent-harness/10-snapshot-and-resume.md).

## Authority Rules

- The Host owns provider selection, provisioning, adapter lifecycle records, lifecycle credentials, and creation of fresh `EnvironmentRunBinding` values; vendor credentials never enter the target Environment or daemon.
- The Harness owns multi-Environment routing and run-bound adaptation, not provider-native resources.
- EIP owns transport-neutral method, payload, error, capability, handle, generation, and cancellation semantics.
- `agent-envd` owns Environment-local canonicalization, native enforcement, handles, producer-side per-call output bounds, aggregate retained-output quotas, release/expiry, and side-effect receipts.
- Docker, E2B, remote, and optional local-daemon adapters cannot weaken `agent-envd` enforcement or create transport-specific method semantics; direct-local backends enforce the parallel provider-neutral contract without pretending to be sandboxed.
- Neither an EIP request nor saved Environment state grants authority; every connection, HTTP logical session, and operation is checked against fresh trusted binding context.
- Adapter lifecycle continuation is consumed before binding creation and remains outside Harness `EnvironmentState`, which contains only backend-local recoverable data.

## Specification Conventions

- EIP wire examples are JSON-RPC 2.0 unless a detail document explicitly states otherwise.
- Stdio, HTTP, and WebSocket are transport profiles over one protocol, not separate APIs.
- Identifiers and handles are selectors, not bearer credentials.
- A provider receipt records an observed provider outcome; it does not imply Host durable completion.
- Provider-specific lifecycle and bootstrap behavior stays outside the transport-neutral method catalog.
