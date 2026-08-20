# Harness State and Resume

## Design Position

`HarnessState` is the portable continuation value produced by one process-local harness run. It stores Pydantic AI message history together with the complete recoverable `AgentContextState`. The state is namespaced by Capability ID and includes Environment, working-state, compaction, discovery, or other configured continuation data. Core inline delegation contributes no partial parent-batch state, and hosted child state remains host-owned. A host may store it, select it, or embed it in a larger durable execution record.

Resume creates a new harness run with fresh trusted bindings for Identity, Environment topology, providers, and Capabilities. Active-run control belongs to the new `HarnessRunStream` and its Pydantic AI run. No live run handle, pending queue, cancellation state, or safe-pause request is restored from state. The stable workload or Agent instance identity can be preserved by the host, but no authority is restored from state. The Harness does not model a durable logical execution.

```mermaid
flowchart LR
    Host -->|input, bindings, optional prior state| Run1[Harness run]
    Run1 -->|HarnessState candidate| Host
    Host -->|selected state, fresh bindings| Run2[New harness run]
```

## HarnessState

```python
class HarnessState(BaseModel):
    schema_version: Literal["1"]
    message_history: tuple[ModelMessage, ...]
    agent_context_state: AgentContextState
```

| Field                 | Meaning                                                                                     |
| --------------------- | ------------------------------------------------------------------------------------------- |
| `schema_version`      | Harness envelope codec                                                                      |
| `message_history`     | Provider-valid Pydantic AI continuation history                                             |
| `agent_context_state` | Versioned state entries owned by configured Capabilities, including multi-Environment state |

```mermaid
flowchart TB
    HS[HarnessState]
    MH[Pydantic message_history]
    ACS[AgentContextState entries by Capability ID]
    ENV[Environment Capability state]
    B1[Binding state A]
    BN[Binding state N]
    OTHER[Working State, Compaction, Discovery, and other entries]

    HS --> MH
    HS --> ACS
    ACS --> ENV
    ENV --> B1
    ENV --> BN
    ACS --> OTHER
```

The envelope excludes host execution status, attempt numbers, leases, accepted input IDs, delivery bookkeeping, Pydantic's live run handles, pending-message queue, cancellation state, and safe-pause requests, plugin artifacts, resolved launch configuration, client-tool attachments and pending-batch delivery state, provider-adapter lifecycle records, provider-suspended model route pins, Environment topology, credentials, provider clients, live objects, bearer handles, `RunUsage` accumulators, usage ledgers, and audit history. Environment entries can contain an observed topology version, versioned backend-local data, or opaque references to objects reachable through an already selected binding, but never provider lifecycle identity, topology, or authority. Rendered Environment context and pending live topology notices are recomputed rather than persisted.

Message content can be sensitive and follows host storage policy. Credential secret material and executable Python objects are never valid state values.

## AgentContext Export

`AgentContext` is the state coordinator:

```python
async def export_state(
    self,
    message_history: Sequence[ModelMessage],
) -> HarnessState: ...
```

Export validates envelope limits and JSON encodability, encodes public Pydantic AI message types, and refreshes the Environment Capability entry from the current `BoundEnvironment`. After run start, each included Capability entry has been accepted or produced by its owner. Before first iteration, non-Environment imported entries are instead preserved as pending values without claiming owner acceptance; each Environment adapter still validates its binding state. The method has no persistence side effect.

Most Capabilities are stateless across runs. Toolsets, callbacks, caches, provider adapters, locks, tasks, and clients are reconstructed. A Capability creates a state entry only for continuation data that messages cannot represent. An Environment binding creates an entry only when its provider has recoverable state.

## Consistent Boundaries

A checkpoint is exported only when the message view is semantically complete.

| Boundary                | Exported view                                                                                                           |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Before Agent start      | Imported history, refreshed Environment state, and other entries preserved as pending; unconsumed input remains outside |
| Before a model request  | Existing history plus the complete request about to be sent, except a provider-suspended continuation placeholder       |
| After a tool-call batch | Model response plus every completed or deferred tool result                                                             |
| After compaction        | Validated compacted history                                                                                             |
| At terminal output      | Complete final history                                                                                                  |
| After stream recovery   | A normalized response containing only recoverable finalized parts                                                       |

Pydantic AI node hooks expose the current messages and the next node. When a complete next `ModelRequest` has not yet been appended to history, the checkpoint Capability builds an export-only view from the current messages and that request. It does not mutate or manually advance the graph.

A provider-suspended continuation has a distinct public shape: current history ends in `ModelResponse(state="suspended")` and the pending public `ModelRequest` has no parts. That empty request is a continuation placeholder rather than a message to send, so checkpoint and safe-pause export preserve the suspended response as the history tail and do not append the placeholder. The Harness candidate carries no provider authority. Before a later no-input run can use Pydantic's provider continuation path, the Host must atomically pair the accepted checkpoint with the locked integration's non-secret exact-target `ModelRoutePin`; the fresh integration Capability reconstructs that target or fails without ordinary rerouting. This classification reads no private node state; other empty requests follow ordinary Pydantic semantics.

State is not exported during an arbitrary active tool side effect, from half-validated tool arguments, from an incomplete model delta stream, or while an inline child or any sibling in its parent tool batch remains unresolved. A child checkpoint alone is not a complete parent boundary because completed sibling results do not enter public parent message history until Pydantic finishes the batch. A stream-recovery Capability can first convert finalized text, thinking, and tool-call parts into a valid partial response; other unresolved tool calls remain unknown or use Pydantic deferred semantics.

```mermaid
sequenceDiagram
    participant PAI as Pydantic AI
    participant Checkpoint as CheckpointCapability
    participant Context as AgentContext
    participant Store as CheckpointStore

    PAI->>Checkpoint: public node hook with complete boundary
    Checkpoint->>Checkpoint: derive complete message view
    Checkpoint->>Context: export_state message view
    Context-->>Checkpoint: HarnessState
    Checkpoint->>Store: save HarnessCheckpoint candidate
```

## HarnessCheckpoint

`HarnessCheckpoint` describes why a state candidate was emitted. It is process-local observation metadata, not durable snapshot identity.

```python
class HarnessCheckpoint(BaseModel):
    run_id: str
    boundary: Literal[
        "before_model_request",
        "after_tool_batch",
        "after_compaction",
        "terminal",
        "recovered_stream",
    ]
    state: HarnessState
```

The host assigns any durable version, timestamp, lineage, storage key, or acceptance status. Multiple candidates may be written, skipped, or superseded.

## Import and Acceptance

State acceptance finishes before model or tool work but has two phases because the Pydantic run remains lazy.

During outer stream preparation, the Harness validates:

- the envelope schema version;
- Pydantic AI message decoding and tool-call/result consistency;
- that every Capability entry ID belongs to the configured build or run Capability set;
- matching Environment binding IDs, provider types, and state versions against fresh bindings;
- configured envelope, message, and content limits.

The Harness creates a fresh `AgentContext`, installs non-Environment Capability entries as pending imported values, and supplies `message_history` as prior Pydantic AI history. Environment import is the deliberate preparation exception: before `RunInputFactory` and before the Pydantic run exists, the Harness uses the Environment Capability's versioned codec to validate its entry and calls public `BoundEnvironment.restore_state()` on the freshly selected bindings. The later Pydantic-bound Environment Capability observes that already-restored facade and does not restore it again.

On first stream iteration, each other stateful run Capability validates the version and typed data of its own pending entry during `for_run()` or ordered `before_run()` before model or tool work. An incompatible entry stops the run at that point. The preparation-only factory and content resolver receive `RunPreparationContext`, which excludes pending `AgentContextState`, so they cannot interpret or act on state that its owner has not accepted. This split preserves native lazy start without adding a second generic Capability codec registry.

Import never restores authority from message metadata or state data. Identity, policy, credentials, approvals, client-executor ownership, Environment generation, binding topology, and host ownership are evaluated from fresh trusted bindings. When prior messages contain a pending external call, the Host also remounts the exact frozen client-tool surface selected for that deferred chain and verifies it against the Host-owned attachment identity before entering the Harness. `HarnessState` stores neither the attachment nor enough prior declaration data for the Harness to prove same-name schema identity. After any required Host adapter attachment has produced that binding, the selected backend may use saved state to reattach a backend-local object only after those checks succeed.

## Resume Inputs

Continuation state and new work are separate inputs. New work can be:

- native Pydantic user content or a hosted `RunInput`;
- Pydantic AI `DeferredToolResults` built from the authoritative pending request, with external calls and approvals kept in their respective maps;
- a hosted child result mapped to a deferred tool result;
- a provider reconciliation result;
- host-selected steering content resubmitted as explicit input only after host reconciliation establishes non-delivery, or under an explicit at-least-once policy with duplicate suppression.

The host selects the exact prior state and passes new input or a `RunInputFactory` when new work is needed. It may omit both to continue from imported messages without manufacturing an empty prompt. When the selected history tail is provider-suspended, omission is valid only with the matching Host-owned route pin and same locked model integration; an ordinary newly routed Model is not a compatible continuation. For deferred client-side execution, it also supplies the same effective client-tool declarations through the definition defaults or a fresh `ClientToolRunBinding`; a current Preset edit or unrelated run replacement cannot reinterpret the pending call. Exact declaration or digest comparison occurs in the Host because the public Harness resume values carry no prior surface identity. A factory runs only after fresh, already reachable Environment bindings have restored their compatible backend-local state and receives no pending non-Environment Capability entries. There is no implicit latest-state, pending-call, or client-attachment lookup in the Harness.

## Fork

Fork is a host operation that derives a new run input from selected messages and Capability state. The target definition accepts the derived state only through explicit codecs or migration supplied by the owning Capabilities.

Identity lineage, durable fork lineage, Environment clone or share policy, and storage selection remain host-owned. No credential or live operation transfers. Environment state or opaque provider references transfer only when the fork policy explicitly selects compatible bindings.

## Host Durable Envelope

A host can store a larger state value because complete recovery may need more than Agent continuation:

```python
class HostExecutionState(BaseModel):
    harness: HarnessState
    definition_ref: ResolvedDefinitionRef
    launch: HostLaunchContext
    continuation: HostContinuationState
```

`HostContinuationState` can include the exact model route pin required by a provider-suspended tail; that pin remains outside `HarnessState` and is validated by the locked model integration. The shape is illustrative of ownership, not a Harness API. A host maps the Harness portion through its `CheckpointStore` implementation.

```mermaid
flowchart TB
    HS[HarnessState] --> DS[Host durable execution state]
    RC[Resolved definition and launch context] --> DS
    HC[Host delivery and recovery state] --> DS
    DS --> Store[Host-selected state store]
```

## External Effects

State export does not make a tool effect exactly once. A provider result that was not authoritatively observed remains unknown. Retry and reconciliation follow the tool or provider contract; the Harness does not fabricate a successful tool result to make history look complete.

## Compatibility

The Harness envelope, Pydantic message codec, and each Capability state version evolve independently. The host selects the definition used for resume. The harness accepts the state only when public messages decode correctly and every stored Capability entry is understood by the active Capability set. Cross-definition restore or fork requires any necessary message and Capability-state migration before run creation.

Unknown required state, unsupported versions, or invalid messages fail without mutating the supplied state.

## Boundaries

| Concern                                                     | Owner                       |
| ----------------------------------------------------------- | --------------------------- |
| Message, Capability, and Environment continuation export    | Harness and `AgentContext`  |
| Capability state codec                                      | Owning Capability           |
| Environment binding state codec and native recovery         | Owning Environment provider |
| Durable version, selection, retention, lineage, and fencing | Host                        |
| External effect reconciliation                              | Tool provider and host      |

## Trade-offs

### Portable Continuation vs. Complete Host Recovery

Messages plus complete recoverable Agent Context state are portable across embedded and hosted use. A host still retains delivery, launch, and durable lifecycle state in its own envelope.

### Complete Boundaries vs. Maximum Checkpoint Frequency

Checkpointing only complete semantic views avoids corrupt resume history. A crash during an in-flight side effect can still leave an unknown outcome that requires provider reconciliation.

### Host Definition Selection and State Compatibility

The host controls definition selection, semantic compatibility, and migration policy; the harness enforces only the message and Capability-state codecs it owns. This keeps deployment versioning outside the library while making incompatible stored state fail explicitly.
