# Foundation Service Specifications

## Overview

This directory defines the current design of `foundation-service`, the optional hosted control and execution service that embeds `agent-harness`.

The service owns hosted Agent definition revisions, typed Preset materialization, versioned model-integration selection and locks, durable execution lifecycle, scheduling, worker coordination, durable continuation selection, and service APIs. It does not redefine the Pydantic Agent loop, Harness run semantics, or provider-native Environment-state semantics, and it does not deploy or persist a platform-owned Sandbox subsystem. It can keep encrypted, bounded opaque adapter lifecycle records and complete Harness checkpoints under generic storage custody while provider codecs retain semantic ownership.

## Document Catalog

| Document                                                                   | Owning contract                                                                                                                                               |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                           | Hosted service scope, architecture, authority, major flows, and completion boundaries                                                                         |
| [01-agent-definitions-and-presets.md](01-agent-definitions-and-presets.md) | Definition sources and revisions, unified Agent/Model/Toolset Preset selection, model-integration/profile-construction locks, materialization, and provenance |
| [02-client-side-tools.md](02-client-side-tools.md)                         | Frozen external client-tool surfaces, durable pending batches, authenticated delivery, idempotent feedback, and resume                                        |
| [03-execution-lifecycle.md](03-execution-lifecycle.md)                     | Durable Execution and Attempt identity, fencing, checkpoint selection, recovery, and terminal commit                                                          |
| [04-execution-api-and-events.md](04-execution-api-and-events.md)           | Foundation Client resources, acceptance and command receipts, durable events, replay, webhooks, and connectors                                                |
| [05-usage-accounting.md](05-usage-accounting.md)                           | Per-response observation identity, normal-path custom pricing with explicit coverage, deduplication, projections, and limits                                  |

## Reading Paths

### Understand hosted execution

Read `00`, then `03` for durable lifecycle and `04` for the Foundation Client and event boundary. Follow the owning Harness contract for process-local execution semantics.

### Define or version an Agent

Read `01`, then [Agent Definition and Build](../agent-harness/03-agent-definition-and-build.md) for the canonical materialized definition and process-local build plan.

### Integrate Foundation Client tools

Read `02`, then [Harness Tool Execution](../agent-harness/07-tool-execution.md#client-side-external-tools) for the process-local `ExternalToolset` and deferred-call semantics. Read `04` for acceptance, authentication, mutation receipts, and event replay.

### Integrate accounting or billing

Read `05`, then [Harness Events, Observability, and Usage](../agent-harness/12-events-observability-and-usage.md) for process-local usage semantics.

## Authority Rules

- The control plane owns definition source acceptance, Preset selection, model-integration revision locks, immutable definition revisions, and durable execution records.
- The execution plane resolves one selected definition revision into authority-neutral model-integration descriptors, any attested credential-free Model, native tools, Toolsets, custom Capability types, and reentrant non-model-selecting build Capabilities. Fresh run authority then lets the one locked integration Capability construct the native Model and effective `ModelProfile` or fail closed.
- For each run, the execution plane obtains a fresh direct-local, EIP-backed, or mixed Environment binding and creates `RunBindings` containing Identity, model pricing, policy, credentials, checkpointing, telemetry correlation, and other execution-scoped inputs and authority.
- The Harness owns process-local Agent construction, execution, continuation state production, and result semantics.
- The service owns accepted client-tool attachments, durable pending external-call batches, authenticated delivery and result submission, idempotency, and exact-parent continuation fencing; the external client owns its side effects.
- One durable `Execution` owns logical work across fenced `Attempt` generations; stale workers cannot commit checkpoints, lifecycle events, or outcomes.
- Durable acceptance and command receipts, lifecycle events, stream delivery, external delivery, usage recording, billing, and payment are distinct facts with their own owners.
- Hosts and provider adapters own credentials, policy, deployment resources, and durable completion; provider adapters also own lifecycle-record and daemon-state schemas even when the service stores their opaque bytes.
- A Preset contributes configuration but grants no identity, credential, Environment binding, or invocation authority.

## Specification Conventions

- Python-like schemas are conceptual typed contracts unless a document explicitly defines a wire format.
- A definition revision is immutable; changing source, Presets, dependency locks, or materialized content creates another revision.
- `Ref` values identify another entity or revision and grant no authority.
- Process-local resolved objects never become durable definition payloads.
