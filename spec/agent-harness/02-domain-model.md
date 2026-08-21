# Harness Domain Model

## Design Position

The Harness domain contains process-local Agent construction, one logical execution, its trusted bindings, events, usage observations, results, and portable continuation state. Durable definitions, executions, worker Attempts, leases, queues, and delivery remain Host domains.

```mermaid
flowchart LR
    Definition[AgentDefinition] --> Executable[ExecutableAgent]
    Bindings[RunBindings] --> Run[Logical Harness run]
    Previous[Optional HarnessState] --> Run
    Executable --> Run
    Run --> Context[AgentContext]
    Run --> Attempts[One or more Pydantic attempts]
    Attempts --> Events[Harness events]
    Attempts --> Result[HarnessRunResult]
    Context --> State[HarnessState]
```

## Ownership

| Concept              | Meaning                                                                              | Owner                                                                      |
| -------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| `AgentDefinition`    | Immutable process-local native build inputs                                          | [Agent Definition and Build](03-agent-definition-and-build.md)             |
| `ExecutableAgent`    | Reusable built Pydantic Agent plus Agent-bound plugins                               | [Public API](14-public-api-and-packaging.md)                               |
| `RunBindings`        | Fresh trusted Agent instance, Environment, model binding, Capabilities, and metadata | [Execution Context](06-execution-context-and-lifecycle.md)                 |
| `AgentContext`       | One logical run's shared Pydantic dependency                                         | [Capability Model](04-capability-model.md)                                 |
| `BoundPluginContext` | Immutable index of fresh plugins used by one logical run                             | [Plugin System](05-plugin-system.md)                                       |
| Logical Harness run  | One outer context/plugin/Environment/usage scope with one public `run_id`            | [Execution Context](06-execution-context-and-lifecycle.md)                 |
| Pydantic attempt     | One inner `Agent.run_stream_events()` invocation with a unique upstream run ID       | Pydantic AI and [Execution Context](06-execution-context-and-lifecycle.md) |
| `HarnessState`       | Detached messages, Capability namespaces, and optional portable Environment data     | [State and Resume](10-snapshot-and-resume.md)                              |
| `HarnessRunResult`   | Immutable process-local terminal outcome                                             | [Public API](14-public-api-and-packaging.md)                               |
| `BoundEnvironment`   | Identity-bound Harness lifecycle facade entered for one logical run                  | [Environment Integration](08-environment-integration.md)                   |
| Topology controller  | Paired process-local Host mutation handle retained only for the entered logical run  | [Environment Integration](08-environment-integration.md)                   |
| Pydantic `RunUsage`  | Live accumulator shared across all inner attempts of the logical run                 | Pydantic AI                                                                |

## Identity

```python
class AgentIdentityRef(BaseModel):
    issuer: str
    subject: str


class AgentInstanceRef(BaseModel):
    identity: AgentIdentityRef
    agent_instance_id: str


class AgentInstanceContext(BaseModel):
    identity: AgentIdentityRef
    agent_instance_id: str
    parent_agent_instance_id: str | None
    delegation_id: str | None
    actor: ActorRef | None
    host_refs: Mapping[str, str]
```

The trusted Host supplies `AgentInstanceContext`. Identity names the workload principal but contains no credential or policy decision. Actor, lineage, and Host references are correlation and policy inputs; model content cannot replace them.

A Host can preserve one Agent instance across a durable continuation while every logical Harness run receives a fresh context and bindings. A new root or child receives another instance ID under Host policy.

## Execution Identities

The platform distinguishes:

| Identity               | Lifetime and owner                                               |
| ---------------------- | ---------------------------------------------------------------- |
| Agent definition ID    | Logical process-local correlation; Host may map its own revision |
| Agent instance ID      | Stable workload instance selected by the Host                    |
| Harness run ID         | One logical process-local invocation                             |
| Pydantic inner run ID  | One semantic attempt inside the logical run                      |
| Host Execution/Attempt | Durable work and worker generation outside the Harness           |
| Tool call ID           | Pydantic call correlation                                        |

No identifier grants authority by itself.

## Model-facing References

When a model must name a resource in a later tool call, the owning Capability exposes a compact scoped reference instead of serializing an internal, provider, or globally durable identifier. First-party forms use a bounded readable prefix plus the shortest suffix suitable for the owning scope, such as `process-1`, `output-1`, `task-1`, or `code-reviewer-a7b9`.

A compact model reference:

- is unique only within an explicit owner scope, such as one logical Harness run, one Working State task scope, or one parent Agent instance's Delegation State;
- maps through trusted code to the exact internal identity or opaque handle and is reauthorized on every use;
- is a selector, never a bearer credential, idempotency identity, provider receipt, or substitute for a durable resource ID;
- is allocated under the owning scope's mutation boundary with collision detection and a finite namespace limit;
- is never reused or silently redirected after release, expiry, removal, incompatible restore, or topology replacement; and
- fails explicitly when unknown, stale, exhausted, or no longer authorized rather than exposing a longer private identifier as fallback.

The owner persists a compact reference only when its semantic continuation crosses runs. Environment revision, cursor, process, and retained-output projections are run-local and disappear with the run. Working State task references remain in their task scope, and inline child references remain in the parent Delegation State. Harness run IDs, Pydantic tool-call IDs, `AgentInstanceRef`, provider handles and cursors, receipts, operation IDs, and Host Execution or Attempt IDs retain their owning opaque/full representations and are not rewritten by this projection rule. The Environment, Working State, and Delegation specifications own each concrete allocator and lifetime.

## Process-local and Durable State

A Host revision reconstructs a process-local `AgentDefinition`; it is not itself a Harness value. A Host Execution selects an executable, fresh `RunBindings`, optional input, and optional prior `HarnessState`. The Harness result and state become durable only if the Host commits them.

One logical Harness run can use several inner Pydantic run IDs during bounded model recovery. This does not change the Host Attempt, Harness run ID, context, Environment aggregate/controller lifetime, plugins, state coordinator, or usage accumulator. Dynamic topology replacement changes immutable routing snapshots inside that one Environment lifetime rather than creating another run identity.

## Version Boundaries

| Version                       | Owner                |
| ----------------------------- | -------------------- |
| Harness state envelope        | Harness              |
| Capability state entry        | Owning Capability    |
| Pydantic message codec        | Pydantic AI          |
| Environment provider state    | Environment provider |
| Host definition revision      | Host                 |
| Host durable lifecycle schema | Host                 |

These versions evolve independently.

## Invariants

1. `AgentDefinition` is process-local and code-first.
2. A logical Harness run has one public run ID and may have several unique inner Pydantic run IDs.
3. `AgentContext` is fresh per logical run and shared only by that run's internal attempts.
4. `HarnessState` restores data, not authority, desired Environment topology, provider launch state, controllers, or live resources.
5. Trusted plugins may intentionally transform complete result state; the Harness does not infer provenance.
6. A process-local terminal result does not commit a Host Execution or external delivery.
7. Events and usage snapshots are observations until their owning Host subsystem persists them.
8. A compact model-facing reference is scoped, non-authoritative, collision-checked, and never substitutes for its internal or durable identity.

## Trade-offs

### Small Shared Model

This document owns identities and cross-contract relationships only. Detailed schemas and failure rules stay in their owning documents.

### Stable Workload Identity, Transient Runs

Stable Agent identity supports policy and credentials across continuation, while fresh process-local runs avoid duplicating durable Host Attempt semantics.
