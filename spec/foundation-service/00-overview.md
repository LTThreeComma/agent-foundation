# Foundation Service Overview

## Design Position

`foundation-service` is the optional hosted control and execution service for Agent Foundation. It turns an accepted Agent definition source into an immutable materialized definition revision, durably accepts work against that revision, and delegates one process-local attempt at a time to `agent-harness`.

The service adds hosting, coordination, persistence, and policy around the Harness. It does not add another Agent loop, Capability lifecycle, tool runtime, Environment state model, process-local result contract, or platform-owned Sandbox subsystem. An execution worker selects a Host provider adapter that returns a fresh Harness binding. Direct-local adapters use first-class `LocalFileOperator` and `LocalShell`; Docker, E2B, remote, and optional local-daemon adapters use the equally first-class `agent-envd` EIP backend.

```mermaid
flowchart TB
    Product[Product or internal service] --> Control[Control plane]
    Control --> Definitions[Definition and Preset revisions]
    Control --> Executions[Durable executions]
    Executions --> Scheduler[Scheduler and coordination]
    Scheduler --> Worker[Execution plane worker]
    Definitions --> Resolver[Definition and provider resolution]
    Resolver --> Worker
    Worker --> Provider[Environment provider adapter]
    Provider --> Local[Direct LocalFileOperator and LocalShell]
    Provider --> Envd[agent-envd EIP backend]
    Worker --> Harness[agent-harness]
    Harness --> Candidate[Events, result, and HarnessState candidates]
    Candidate --> Worker
    Worker --> Executions
    Executions --> ClientTools[Foundation Client external-tool delivery and feedback]
    Executions --> Delivery[Product delivery or webhook]
```

## Major Components

| Component                   | Owns                                                                                                                                | Explicit boundary                                                                                              |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Definition control          | Definition sources, typed Presets, immutable revisions, dependency locks, provenance, and selection                                 | Produces the canonical `AgentDefinition`; never stores live clients or plaintext credentials                   |
| Durable execution lifecycle | Accepted work, selected definition revision, fenced Attempts, checkpoint selection, recovery, durable completion, and cancellation  | A Harness terminal result is only a candidate until the service commits its own transition                     |
| Scheduler and coordination  | Runnable work, leases, wakeups, maintenance, and worker ownership                                                                   | Coordination delivery is not an independent durable execution authority                                        |
| Execution API and events    | Foundation Client resources, idempotent acceptance and commands, lifecycle event replay, and delivery projections                   | Streams, webhooks, and queues never replace the durable Execution authority                                    |
| Usage recording             | Idempotent per-response observation identity, lineage, actual cost source, pricing coverage and revision, and rebuildable estimates | `RunUsage`, durable records, billing, and payment are distinct facts                                           |
| Execution worker            | Provider selection, run bindings, Harness consumption, checkpoints, and terminal commit proposals                                   | Uses public Harness and EIP boundaries; defines no Sandbox domain and does not interpret provider-native state |
| Hosted service plugins      | Ingress, storage, lifecycle projection, connectors, and hosted policy outside the Agent loop                                        | Agent-affecting behavior enters the Harness through typed definitions, Capabilities, Toolsets, or bindings     |
| Client-tool lifecycle       | Accepted external-tool attachment, durable pending batch, authenticated delivery, feedback, and resume                              | Client owns the side effect; Harness and `agent-envd` execute no client handler                                |

## Definition-to-Execution Flow

```mermaid
sequenceDiagram
    participant Caller
    participant Control
    participant Catalog as Preset and artifact catalogs
    participant Worker
    participant Provider as Environment provider adapter
    participant Harness

    Caller->>Control: submit inline definition or exact Preset input
    Control->>Catalog: resolve typed Preset and dependency revisions
    Catalog-->>Control: materialized AgentDefinition and locks
    Control->>Control: validate and commit immutable definition revision
    Caller->>Control: request work against selected revision
    Control->>Control: durably accept and schedule execution
    Worker->>Control: acquire selected revision and attempt
    Worker->>Worker: resolve model-integration plans, native tools, Toolsets, Capability types, and reentrant build instances
    Worker->>Harness: build ResolvedAgentDefinition
    Worker->>Provider: provision or attach with selected adapter lifecycle record
    Provider-->>Worker: fresh direct-local, EIP-backed, or mixed EnvironmentRunBinding
    Worker->>Worker: bind Identity, policy, credentials, checkpointing, telemetry, and permitted client tools for this run
    Worker->>Harness: run with RunBindings
    Harness-->>Worker: events, result, usage, and optional HarnessState
    Worker->>Control: commit checkpoint, durable deferred wait, or terminal transition
    Control-->>Caller: host-owned status or delivery
```

Preset materialization and definition revision commit complete before a durable execution selects the revision. Execution-time build resolution compiles authority-neutral model-integration plans, may select only attested credential-free Models, and can select reentrant provider clients without mutating the selected materialized definition. A model integration that needs current Identity, policy, credentials, revocation state, or a continuation route pin realizes its locked logical selection through exactly one fresh run Capability. Current-run Identity, Environment, delegation, checkpoint, telemetry, and all other authority likewise enter through `RunBindings` and never enter the immutable executable. The first-party Client Tools Capability can additionally permit a typed whole-run replacement of external tool declarations through `ClientToolRunBinding`; those declarations contain no handler or authority and are frozen with the accepted execution for exact deferred resume.

A durable Attempt can retain an opaque, versioned provider-adapter lifecycle record so a later worker can attach or resume an optional local daemon, Docker container, E2B environment, or other vendor resource before constructing the next binding. Direct-local roots normally need no provider lifecycle record. This record is outside `HarnessState`; the service applies storage encryption, size limits, retention, and deletion, while the adapter exclusively owns its schema, validation, migration, and lifecycle meaning. The service stores backend-local `EnvironmentState` only as opaque bytes inside complete Harness checkpoints under the same generic custody controls; each direct or EIP backend codec owns those bytes' semantics.

[Durable Execution Lifecycle](03-execution-lifecycle.md) defines one stable `Execution` across monotonic, fenced Attempt generations. [Execution API and Durable Events](04-execution-api-and-events.md) defines idempotent acceptance and command receipts plus replayable committed events; transient queues, live Harness streams, SSE, WebSocket, and webhooks remain projections. [Usage Recording and Cost Estimation](05-usage-accounting.md) defines one idempotent record per newly produced model response and Foundation-selected pricing injection without turning terminal `RunUsage` into a bill.

## Authority and Completion

| Fact                             | Authority                                                     |
| -------------------------------- | ------------------------------------------------------------- |
| Accepted definition source       | Control plane                                                 |
| Materialized definition revision | Definition control                                            |
| Installed plugin or provider     | Operator-selected catalog                                     |
| Process-local Agent construction | Harness and Pydantic AI                                       |
| Process-local result and state   | Harness                                                       |
| Durable checkpoint selection     | Hosted execution lifecycle                                    |
| Durable execution completion     | Hosted execution lifecycle                                    |
| Durable lifecycle event          | Hosted execution lifecycle transaction                        |
| Model-usage record and estimate  | Usage recording subsystem                                     |
| External delivery                | Product or connector that commits the delivery fact           |
| Client-side tool action          | Authenticated external executor and the system it affects     |
| Client-tool pending/result fact  | Hosted execution lifecycle                                    |
| Environment-local side effect    | Environment provider and the external system that performs it |

Definition acceptance, Preset materialization, execution acceptance, Harness completion, durable deferred-call commit, client-side action, result-submission acceptance, durable host completion, and external delivery are independent boundaries. Later observations do not retroactively commit an earlier authority's state. [Client-Side Tools](02-client-side-tools.md) owns the hosted external-tool flow.

## Extension Boundary

Agent behavior is configured by the materialized `AgentDefinition` and resolved Pydantic Capabilities, tools, and Toolsets. Hosted service plugins operate outside the Agent loop and can contribute typed Preset catalogs or provider resolvers, but they cannot silently mutate an immutable definition revision during execution.

Provider resolution can vary reentrant authority-neutral clients while preserving the selected logical definition. Short-lived credentials, model-selection policy, and policy grants are resolved through the current run's bindings rather than stored in the build plan. Any change to model or tool configuration that changes the materialized Agent behavior creates a new definition revision.

## Stable Principles

01. One immutable materialized definition revision is selected before durable work starts.
02. Presets are deterministic authoring inputs, not runtime inheritance or authority.
03. Process-local model, native tool, Toolset, custom Capability type, and reentrant build-Capability values enter one `ResolvedAgentDefinition` and never become durable definition payloads.
04. Identity, Environment, policy, credential, checkpoint, telemetry, and every other execution-scoped authority enter through fresh `RunBindings` rather than the immutable executable.
05. Environment provider adapters expose direct-local and EIP-backed bindings as first-class peers; the service defines no Sandbox resource and has storage custody, not semantic ownership, over opaque adapter lifecycle and backend-local state.
06. One durable Execution spans fenced, monotonic Attempt generations; stale workers cannot advance checkpoints or lifecycle.
07. Durable acceptance and lifecycle event append commit before ephemeral scheduling or delivery can claim success.
08. The execution plane consumes the public Harness contract and does not duplicate the Pydantic Agent loop.
09. Harness completion, Host durable completion, event or webhook delivery, telemetry, durable usage recording, billing, and payment remain separate facts.
10. Minimal and distributed deployments use the same durable semantics; storage and coordination adapters can differ.
11. Client-side tools use native external deferral; the service durably fences authenticated feedback, while Foundation Client owns local execution and `agent-envd` remains uninvolved.

## Trade-offs

### Materialized definitions vs. late Preset evaluation

Persisting the complete materialized `AgentDefinition` makes execution, recovery, inspection, and comparison independent from later Preset edits. It consumes more durable storage than retaining only a Preset reference, but definitions are small and reproducibility is more important than deduplicating their configuration bytes.

### Host resolution vs. persisted live configuration

Resolving clients, credentials, policy, and Environment bindings at execution time preserves revocation and deployment portability. A definition revision therefore identifies logical behavior and locked artifacts without claiming that live infrastructure remains unchanged.
