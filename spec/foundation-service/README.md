# Foundation Service Specifications

## Overview

This directory defines `foundation-service`, the optional hosted control and execution service that embeds `agent-harness`.

Foundation owns durable Agent authoring schemas, typed Presets, immutable definition revisions and dependency locks, process-local reconstruction adapters, Environment provider registry integration and desired topology, durable root and asynchronous child Execution lifecycles, worker Attempts, scheduling, continuation selection, client-tool delivery, service APIs, durable events, and usage records.

It does not redefine the code-first Harness `AgentDefinition`, plugin lifecycle, Pydantic Agent loop, Harness result/state semantics, or provider-native Environment state.

## Document Catalog

| Document                                                                   | Owning contract                                                                                                                 |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                           | Hosted scope, architecture, authority, definition-to-execution flow, and completion boundaries                                  |
| [01-agent-definitions-and-presets.md](01-agent-definitions-and-presets.md) | Foundation-owned authoring schemas, typed Presets, immutable revisions, dependency locks, and worker reconstruction             |
| [02-client-side-tools.md](02-client-side-tools.md)                         | Frozen external tool surfaces, durable pending batches, authenticated delivery, idempotent feedback, and continuation           |
| [03-execution-lifecycle.md](03-execution-lifecycle.md)                     | Durable Execution and Attempt identity, fencing, checkpoints, recovery, completion, and child delivery                          |
| [04-execution-api-and-events.md](04-execution-api-and-events.md)           | Foundation Client resources, HTTP boundary, child/task operations, acceptance, commands, durable events, replay, and connectors |
| [05-usage-accounting.md](05-usage-accounting.md)                           | Mixed-source usage identity, per-model-request reporting, pricing, deduplication, projections, and accounting boundary          |
| [06-environment-providers.md](06-environment-providers.md)                 | Provider registry and locks, desired topology, launch-envelope custody, Attempt materialization, and active-run reconciliation  |

## Reading Paths

### Understand Hosted Execution

Read `00`, then `03` for durable lifecycle and `04` for public APIs and event delivery. Follow the owning Harness documents for process-local behavior.

### Define or Version an Agent

Read `01`, then [Harness Agent Definition and Build](../agent-harness/03-agent-definition-and-build.md). Foundation records are serializable Host schemas; workers reconstruct the process-local Harness definition.

### Integrate Client-side Tools

Read `02`, then [Harness Tool Execution](../agent-harness/07-tool-execution.md) for native external deferral.

### Integrate Usage

Read `05`, then [Harness Events, Observability, and Usage](../agent-harness/12-events-observability-and-usage.md).

### Integrate Environment Providers

Read `06`, then [Harness Environment Integration](../agent-harness/08-environment-integration.md) and `03`. Foundation owns provider selection and durable reconciliation; Harness owns the entered resource and process-local topology controller.

## Authority Rules

- The control plane owns source acceptance, typed Presets, model-integration revisions, immutable definition revisions, dependency locks, and durable Executions.
- Foundation definition records contain only Foundation-owned serializable data. They contain no Python class, plugin instance, Model, Toolset, Capability, callable, client, or credential.
- The worker verifies Host locks and uses trusted installed adapters to reconstruct a process-local Harness `AgentDefinition`.
- Every logical run receives fresh `RunBindings`, including an Environment aggregate materialized from current desired topology and an explicit `ModelRunBinding` when hosted model aliases must fail closed rather than delegate to native inference.
- The current worker retains the Environment controller only while its Harness run is entered; dynamic desired acceptance and effective topology publication are separately fenced facts.
- One Foundation Attempt may contain several internal Harness model attempts; those inner attempts are not durable Attempt generations.
- A stale worker cannot commit a checkpoint, lifecycle event, client feedback, child result, usage record, or terminal outcome.
- Native deferred external calls and approvals remain distinct; Foundation owns durable pending state and authenticated feedback, while the external client owns its side effects.
- Process-local Harness completion, durable Host completion, event delivery, external delivery, usage recording, billing, and payment are independent facts.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly declared as API or storage formats.
- A definition revision is immutable; changing materialized content or a dependency lock creates another revision.
- `Ref` values identify entities or revisions and grant no authority.
- Process-local Python objects are reconstructed and never become durable payloads.
