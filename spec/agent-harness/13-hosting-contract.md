# Harness Hosting Contract

## Design Position

Embedded applications and hosted execution workers use the same code-first Harness API. The Harness does not expose a separate hosted build format. A hosted service owns durable definition schemas, Presets, immutable revisions, dependency locks, and reconstruction adapters; the worker reconstructs one process-local `AgentDefinition` and calls `HarnessBuilder`.

The Host also owns durable acceptance, worker Attempts, leases, checkpoint selection, deferred delivery, recovery, and terminal commit. The Harness returns only process-local observations and state candidates.

## Boundary

| Concern                                    | Host                     | Harness                             |
| ------------------------------------------ | ------------------------ | ----------------------------------- |
| Authoring schema, Presets, revision, locks | Owns                     | No durable schema                   |
| Trusted Python object reconstruction       | Owns                     | Validates process-local composition |
| Agent Identity and provider policy         | Issues/evaluates         | Carries through fresh bindings      |
| Environment and model binding              | Constructs fresh values  | Enters/uses                         |
| Agent loop and outer middleware            | Delegates                | Owns process-locally                |
| Internal model semantic attempts           | Observes one logical run | Owns bounded recovery               |
| Worker crash and durable replay            | Owns                     | Exports portable state only         |
| Durable completion and delivery            | Owns                     | Returns a candidate                 |

## Definition Mapping

```mermaid
flowchart LR
    Source[Host source or Preset] --> Materialize[Host materialization]
    Materialize --> Revision[Immutable Host definition revision]
    Revision --> Verify[Verify Host dependency locks]
    Verify --> Adapters[Trusted reconstruction adapters]
    Adapters --> Definition[Process-local AgentDefinition]
    Definition --> Builder[HarnessBuilder]
    Builder --> Executable[ExecutableAgent]
```

The Host revision stores only Host-owned serializable values and exact dependencies. It can include logical model IDs, model settings, plugin/provider configuration, tool declarations, output schema, and artifact locks under Foundation schemas. It does not store a Harness compiler document, catalog manifest, Python class, plugin instance, Model, Toolset, Capability, callable, credential, or live client.

At execution time trusted installed adapters create:

- native `AgentSpec` and process-local `OutputSpec`;
- optional Model or logical model name;
- native tools and Toolsets;
- Agent-bound Capabilities;
- concrete Harness plugin instances;
- self-healing and semantic recovery policy.

The resulting value is an ordinary `AgentDefinition`. The Harness does not verify Host artifact digests, reconstruct import paths from input, or inspect Preset provenance.

## Run Mapping

For each logical run the Host constructs `RunBindings` with:

- the trusted `AgentInstanceContext`;
- one fresh `EnvironmentRunBinding`;
- an optional fresh `ModelRunBinding`;
- fresh run Capabilities;
- bounded non-authoritative metadata.

A hosted model integration normally implements `ModelRunBinding`, resolves its own trusted configuration, current policy, credentials, and route selection, and returns a native Model or raises. The Harness applies no special catalog role validation and, if a Host omits the binding for a string model, deliberately delegates to native Pydantic inference. A fail-closed hosted profile therefore requires its worker adapter to supply and test the binding; this is a Host invariant, not a different Harness API.

The Host passes optional `HarnessState`, native input or an input factory, one `RunUsage` accumulator, and optional native `UsageLimits`. One logical run can contain several inner Pydantic attempts while retaining the same Host Attempt, bindings, context, Environment, plugins, state coordinator, and usage accumulator.

## State and Resume Mapping

```mermaid
sequenceDiagram
    participant Host
    participant Harness

    Host->>Harness: start with fresh bindings and optional selected HarnessState
    Harness-->>Host: events and state/result candidates
    Host->>Host: fenced checkpoint or terminal commit
    Host->>Harness: later new run with fresh bindings and selected state
```

The Host stores and selects `HarnessState`. It separately stores definition revision, provider lifecycle data, accepted client-tool pending data, asynchronous child state, delivery ledgers, and durable reconciliation evidence.

`HarnessState` restores only public Pydantic messages and JSON Capability namespaces. Plugin objects, Model bindings, Environment bindings, credentials, policy, usage, active attempts, and delivery facts are rebuilt.

The Harness defines no mandatory model route pin. If one provider requires an additional durable continuation selector beyond public messages, the Host and that model integration own it as provider-specific launch state. It is not a generic Harness recovery condition.

## Recovery Mapping

The Host distinguishes:

- internal Harness semantic attempts inside one live logical run;
- a new durable worker Attempt after process loss, lease loss, or selected recovery.

A new Host Attempt always creates a new Harness run with fresh bindings. It uses only an authoritative selected checkpoint and does not blindly replay a possible external mutation. The interrupted-tool normalization text explicitly preserves unknown outcome and tells the next model to inspect current state.

Provider transport retry and Harness Model self-healing do not create Host Attempt records. Usage observations from all inner semantic attempts remain in the one logical run accumulator and must not be double-counted with terminal snapshots.

## Deferred and Client-side Tools

Native Pydantic `DeferredToolRequests` end the Harness run as `status="suspended"`. The Host owns durable pending-call/approval records, authentication, external execution, idempotent feedback, and selection of a later continuation state. A continuation starts a new Harness run with that prior state, fresh `RunBindings`, the exact remounted tool surface, and `DeferredToolResume` containing the authoritative pending request plus its complete native results.

External calls and approvals remain distinct. A Host reconstructs exact tool surfaces from its own revision and pending attachment and verifies their identity before resume; those surfaces and the resume envelope are not encoded in `HarnessState`.

## Asynchronous Children

The executable-owned `SubagentCollection` is process-local topology and grants no scheduling authority. A Host-defined Capability can use a fresh typed service collaborator to accept independent child work. The Host owns child Execution identity, Attempts, checkpointing, cancellation, result retention, and delivery.

A successful asynchronous spawn is an ordinary tool result, not `DeferredToolRequests`. Child completion becomes later Host-selected semantic input and does not satisfy the original spawn tool call.

## Events and Completion

The Host consumes one `HarnessRunStream` and may persist, coalesce, or fan out events. Earlier attempt events remain observations even if a later semantic attempt succeeds. The terminal result determines the logical run outcome.

A normal result becomes durable only through a fenced Host transaction. `RunCleanupError.outcome` is an uncertain candidate and cannot be reported as a clean Harness terminal delivery.

## Embedded Profile

An embedded caller can construct `AgentDefinition` directly, use `RunBindings.local()`, accept native Pydantic inference when no `ModelRunBinding` is present, and retain state in memory or application-selected storage. It follows the same plugin, model recovery, result, and cleanup semantics.

## Compatibility

Compatibility is evaluated separately for:

- the Host definition/revision schema;
- trusted reconstruction adapters and installed artifacts;
- the Harness public Python API;
- the selected Pydantic AI public surface;
- Harness and Capability state codecs;
- provider-specific launch/continuation data.

A Host rejects an incompatible revision or adapter before building process-local objects. The Harness does not turn an unknown Host schema into a generic Python import or fallback configuration.

## Invariants

1. Hosted and embedded callers use the same `AgentDefinition`, builder, bindings, stream, result, and state contracts.
2. Durable schemas and dependency locks belong to the Host.
3. Python objects are reconstructed in-process and never stored in Host records.
4. Fresh authority enters every logical run through typed bindings and Capabilities.
5. Internal model attempts do not create additional Host Attempt generations.
6. New durable recovery uses a fresh Harness run and fresh bindings.
7. Process-local completion is only a candidate for Host durable completion.
8. State restores data, not authority or live resources.

## Trade-offs

### Host-owned Reconstruction vs. Harness Compilation

Host-owned schemas keep durable compatibility where it belongs and allow native Python composition in the worker. Every Host must maintain explicit adapters, but the Harness avoids becoming a package manager or universal configuration language.
