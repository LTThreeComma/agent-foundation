# Agent UI Specifications

## Overview

This directory defines `agent-ui`, the complete local single-user Agent workstation distributed as `converge-agent-ui`. It owns reloadable local configuration, reusable Model, Prompt, Plugin, Skill, Agent, and Environment definitions, Codex-style Sessions, Environment resource lifecycle, foreground and background execution, durable local presentation history, and equivalent WebUI and TUI product surfaces.

Agent UI is not a reduced Foundation Service. It implements the shared [`Session`, `Thread`, `Turn`, and `Item` interaction model](../interaction-model.md) for one local Host. The [Harness](../agent-harness/README.md) remains the process-local Agent runtime, the [Environment Provider package](../agent-environment-provider/README.md) remains the provider lifecycle and attachment boundary, and [Agent Stream Protocol](../agent-stream-protocol/README.md) remains the Harness-to-AG-UI observation boundary.

## Document Catalog

| Document                                                                             | Owning contract                                                                                                                               |
| ------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                     | Local workstation architecture, application-service boundary, surfaces, packaging, and completion boundaries                                  |
| [01-configuration-and-resource-catalog.md](01-configuration-and-resource-catalog.md) | Reloadable file-backed configuration, resource catalogs, credentials, validation, and accepted configuration generations                      |
| [02-agent-composition-and-snapshots.md](02-agent-composition-and-snapshots.md)       | Model, Prompt, Plugin, Skill, Capability, and child-Agent composition; immutable resolved snapshots and Harness reconstruction                |
| [03-local-storage-and-recovery.md](03-local-storage-and-recovery.md)                 | Hybrid SQLite and compressed-file persistence, authority, publication ordering, integrity, projection, retention, and recovery                |
| [04-sessions-environments-and-state.md](04-sessions-environments-and-state.md)       | Session and Thread identity, Environment assignment, Turn/checkpoint lifecycle, fork, background records, model-visible browsing, and cleanup |
| [05-runtime-subagents-and-surfaces.md](05-runtime-subagents-and-surfaces.md)         | Shared application service, run coordination, background children, Environment attachments, WebUI, TUI, replay, and transport                 |

## Reading Paths

### Understand the Local Workstation

Read `00`, `03`, and `04`. Then read the [Harness catalog](../agent-harness/README.md) and [Environment Provider catalog](../agent-environment-provider/README.md) for the embedded runtime contracts.

### Configure and Compose Agents

Read `01` and `02`, then [Harness Agent Definition and Build](../agent-harness/03-agent-definition-and-build.md), [Harness Plugin System](../agent-harness/05-plugin-system.md), and [Delegation and Subagents](../agent-harness/11-delegation-and-subagents.md).

### Integrate Environment Providers

Read `01` and `04`, then [Provider Specifications and Catalog](../agent-environment-provider/01-provider-specs-and-catalog.md) and [Resource Management and Runtime Attachments](../agent-environment-provider/02-resource-management-and-attachments.md).

### Implement a Presentation Surface

Read `05`, then the [Agent Stream Protocol specification](../agent-stream-protocol/README.md). A surface consumes the shared application service and its retained/live AG-UI stream rather than interpreting Harness events or reading storage independently.

## Authority Rules

- Human- and agent-editable configuration files own the desired Model, Prompt, Plugin instance, Skill, Agent, and Environment definitions in the latest accepted configuration generation. SQLite indexes those definitions but does not replace their file authority.
- Agent UI resolves configuration into immutable content-addressed snapshots. A Session pins exact Agent and Environment snapshots; dynamic reload never mutates a running executable or an existing Session composition.
- SQLite owns mutable local metadata and control state. Compressed immutable files own selected `HarnessState`, pending `DeferredToolRequests`, provider resource-state blobs, resolved snapshots, and retained AG-UI event segments.
- `HarnessState` is the canonical process-local Agent state value. A waiting Turn additionally pins the exact complete `DeferredToolRequests` required by the Harness resume contract; AG-UI history, identifiers, transcript projections, SQLite indexes, rendered state, and browser caches replace neither value.
- The Harness and Pydantic AI own native Agent construction, Agent loops, inline delegation, run events, results, and continuation semantics.
- The Environment Provider package owns provider specification validation, Manager behavior, provider resource-state codecs, and fresh runtime attachments. Agent UI owns desired Environment definitions, lifecycle decisions, fencing, persistence, and Session assignment.
- Agent Stream Protocol owns reusable Harness-to-AG-UI conversion and process-local accumulation. Agent UI owns event-file persistence, query projection, replay, fan-out, transport, and presentation policy.
- OpenTelemetry is exported through the repository observability boundary and is not stored in Agent UI SQLite databases, Session files, or AG-UI segments.
- WebUI and TUI are product peers over one application service. Neither owns a separate configuration model, session model, orchestration loop, or rendering truth.
- Persisted references and local identifiers grant no Agent, Environment, model, plugin, credential, or child authority. Every invocation receives fresh current bindings.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly identified as serialized local documents.
- A configuration generation is one atomically accepted view of all reloadable configuration sources; it is not a process generation or Session revision.
- A resource revision is immutable normalized content identified by stable resource identity plus digest; a source file can later select different content without mutating the revision.
- A Session is a local Host interaction tree and is not a Foundation `Execution`, browser connection, Pydantic run, model-provider session, or Environment provider resource.
- A Thread is one independently advancing history whose complete continuation is carried by `HarnessState.thread_id`.
- A Turn is one accepted advancement of one Thread and can span zero or more Harness Runs; internal Harness `ModelAttempt` values remain process-local.
- An Item is a semantic user-visible unit projected for presentation, not a generic Harness stream envelope.
- Background child jobs are process-local Host work. They are not Harness inline delegation state or Foundation child Executions.
