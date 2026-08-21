# Execution Context and Lifecycle

## Design Position

One Harness run is one process-local logical invocation of an `ExecutableAgent`. It creates one outer `AgentContext`, enters one Environment binding, binds one plugin chain, owns one shared `RunUsage` accumulator, and produces at most one terminal Harness result.

A logical Harness run may contain several sequential Pydantic AI model attempts when `ModelRecoveryPolicy` is enabled. Those attempts are an internal recovery mechanism, not separate Harness runs, Host Attempts, plugin invocations, contexts, Environments, or usage ledgers. Each Pydantic attempt receives a unique upstream run ID while the public Harness `run_id` remains stable.

Pydantic AI owns each inner Agent loop, model/tool execution, native deferred and approval boundaries, output validation retries, messages, and provider-suspended continuation. The Harness owns outer preparation, plugin middleware, bounded semantic attempt coordination, terminal normalization, and cleanup.

## Boundary

| Concern                                          | Owner                                      |
| ------------------------------------------------ | ------------------------------------------ |
| Model/tool loop and native deferred values       | Pydantic AI                                |
| Provider transport retry                         | Provider client and Pydantic `RetryConfig` |
| Narrow provider-history repair                   | `SelfHealingModel`                         |
| Logical run and interrupted-stream attempts      | Harness                                    |
| Durable execution, worker Attempt, lease, replay | Host                                       |
| Continuation persistence and selection           | Host                                       |

A Host may map one logical Harness run to one durable worker Attempt. It does not create a new durable Attempt for every internal model attempt.

## RunBindings and AgentContext

```python
@dataclass(frozen=True, slots=True)
class RunBindings:
    instance: AgentInstanceContext
    environment: EnvironmentRunBinding
    model_binding: ModelRunBinding | None = None
    capabilities: tuple[
        AbstractCapability[AgentContext], ...
    ] = ()
    metadata: Mapping[str, JsonValue] = {}
```

The trusted caller supplies fresh bindings for every logical run. The Harness:

1. allocates the public Harness `run_id`;
2. binds and enters `EnvironmentRunBinding` with that ID and Agent instance;
3. invokes an optional `RunInputFactory` exactly once;
4. normalizes semantic input;
5. creates one `AgentContext` with imported `AgentContextState`, the entered Environment, optional model binding, immutable metadata, and executable-owned child collection;
6. binds fresh run plugin replacements and freezes `BoundPluginContext`;
7. creates the outer plugin response.

The same context is reused by every internal Pydantic attempt. Current Identity, Environment, plugins, Capability-state coordinator, model binding, and metadata therefore remain stable across recovery. `RunBindings.capabilities` are passed to every Pydantic attempt and follow upstream per-run Capability binding semantics.

`RunBindings.local()` creates a process-local Agent instance, uses a no-operation Environment when none is supplied, and accepts the same optional model binding, Capabilities, and metadata. It does not create a model registry or hidden provider configuration.

## Logical Lifecycle

```mermaid
stateDiagram-v2
    [*] --> created
    created --> active: stream context entered
    active --> active: recoverable model attempt restarts
    active --> completed: validated output
    active --> suspended: native deferred or approval boundary
    active --> failed: handled terminal execution failure
    active --> cancelled: native or requested cancellation
    completed --> [*]
    suspended --> [*]
    failed --> [*]
    cancelled --> [*]
```

The public states describe the logical run. Internal attempt count, backoff, provider request retries, and self-healing replay are not additional lifecycle states.

## Execution Flow

```mermaid
sequenceDiagram
    participant Caller
    participant Harness
    participant Plugins
    participant PAI as Pydantic AI
    participant Provider

    Caller->>Harness: enter stream with input, bindings, and optional state
    Harness->>Harness: enter Environment and create one AgentContext
    Harness->>Plugins: bind one fresh middleware chain
    Caller->>Harness: request first item
    loop total semantic attempt budget
        Harness->>PAI: run_stream_events with unique inner run ID
        PAI->>Provider: model and tool work
        Provider-->>PAI: events, response, or failure
        PAI-->>Harness: public events and latest messages
        alt completed or deferred
            Harness->>Harness: build terminal candidate
        else recoverable model interruption
            Harness->>Harness: normalize interrupted history
            Harness->>Harness: cancellation-aware backoff and continuation input
        else hard stop
            Harness->>Harness: cancel, fail, or propagate
        end
    end
    Harness->>Plugins: unwind result or error
    Harness->>Harness: validate nearest candidate and close resources
    Harness-->>Caller: terminal result event after cleanup
```

The stream is lazy: entering it performs preparation but no model or tool work. First iteration drives the plugin response and, if middleware reaches the inner path, starts the first Pydantic attempt. A plugin short-circuit starts no Pydantic attempt.

## Model Attempt Recovery

`ModelRecoveryPolicy` is disabled by default. When enabled, `max_attempts` is the total number of Pydantic attempts, including the first one. Its upper bound defaults to five. The policy owns:

- the total attempt budget;
- a fixed continuation input or sync/async prompt factory;
- full-jitter exponential backoff bounded by configured initial and maximum delays.

On a recoverable model interruption, the Harness:

1. captures the latest complete public Pydantic message view;
2. normalizes only an explicitly interrupted terminal tool-call boundary;
3. leaves previously emitted Harness events visible because they cannot be retracted;
4. waits using cancellation-aware backoff;
5. builds the next semantic input;
6. starts another Pydantic run with the normalized history, same outer context and bindings, same shared usage accumulator, and a fresh inner run ID.

The default continuation text says that the previous stream ended before completion, asks the model to continue from available history without repeating completed work, and warns that a side-effecting tool may have partially or fully completed even when no result was recorded.

A tool call missing a result at an explicitly interrupted boundary receives a failed `ToolReturnPart` stating:

> No tool result was recorded because execution was interrupted. The operation may have partially or fully completed. Check the current state before deciding whether to retry it.

This preserves a valid conversation shape without claiming rollback, non-execution, or exactly-once behavior.

Recovery is limited to model-boundary failures. It does not restart after:

- explicit or external cancellation;
- `UsageLimitExceeded`;
- exhausted Pydantic output-validation retries;
- tool execution failure;
- Harness, plugin, state, Environment, or input failure;
- native deferred external-tool or approval output;
- normal provider-suspended continuation.

Provider transport retries remain below this layer. `SelfHealingModel` may replay one request after an exact history repair before the Harness attempt loop observes the failure. These budgets are independent and are not multiplied into a second unbounded retry framework.

When the semantic attempt budget is exhausted, the logical run returns `status="failed"` with `failure.code="model_recovery_exhausted"`. When recovery is disabled, recognized Pydantic execution failure returns `failure.code="agent_run_failed"`.

## Native Deferred and Provider Continuation

A Pydantic result whose output is `DeferredToolRequests` ends the logical run with:

- `status="suspended"`;
- `suspend_reason="deferred"`;
- the native deferred value;
- complete current `HarnessState` and usage.

External calls and approval requests retain their upstream distinct maps. The Harness does not execute them, convert one kind into the other, or start another semantic attempt.

Provider-suspended continuation remains native Pydantic message behavior. The Harness preserves public message history and does not create a route-pin schema, duplicate provider job state, or reinterpret suspension as stream recovery. A later logical run receives fresh bindings and the Host-selected prior `HarnessState`; the selected model integration is responsible for any provider-specific ability to continue those public messages.

## Cancellation

`HarnessRunStream.cancel()` is idempotent. It records cancellation for pre-start and backoff phases and delegates to the active `AgentRunEvents.cancel()` when an inner attempt exists.

Cancellation fences semantic recovery:

- pre-start cancellation prevents model work;
- cancellation during an attempt uses native Pydantic cancellation;
- cancellation interrupts recovery backoff immediately;
- cancellation observed after a recoverable failure prevents the next attempt;
- calls after close or terminal delivery are no-ops.

A normalized cancelled result uses the latest complete messages and usage available. External cancellation of the consumer task remains `asyncio.CancelledError`; it is not translated into a normal result and cannot be suppressed by cleanup.

Cancellation does not prove provider rollback. Any dispatched side effect without authoritative completion evidence remains unknown.

## State Boundary

`HarnessState` combines the latest complete Pydantic message view with a detached snapshot of `AgentContextState`. The outer context and state coordinator remain shared across internal attempts. The previous state is copied when the stream is created, so caller mutation cannot change an active run.

`export_state()` is valid only while the stream context is active. Before an inner attempt starts it returns imported messages plus the current Capability-state snapshot. During execution it returns the latest complete public message view; partial token deltas are not reconstructed into synthetic messages.

The detailed state schema and interrupted-history rules are owned by [Harness State and Resume](10-snapshot-and-resume.md).

## Result and Cleanup

The inner path produces one `HarnessRunResult` candidate. Plugin middleware can replace it under the trusted-plugin contract. The Harness validates candidates at every response boundary so the nearest valid inner outcome remains available if an outer layer later fails.

Cleanup follows reverse acquisition order and stays in the task that entered the async scopes:

1. close registered plugin responses from inner to outer;
2. close the Environment and remaining outer resources;
3. preserve any pending external task cancellation;
4. publish the terminal result event only when cleanup succeeds.

Every registered response is closed at most once. Cleanup continues after an individual close failure and collects secondary causes. A plugin or cleanup failure after a valid candidate raises `RunCleanupError` with that candidate and no terminal event. External cancellation takes precedence and receives cleanup failures as notes.

## Invariants

1. One public Harness `run_id`, context, Environment binding, plugin graph, and usage accumulator span the complete logical run.
2. Every internal Pydantic attempt has a unique upstream run ID.
3. Recovery never creates a new Host execution fact or rebinds current authority.
4. Events already delivered by an earlier attempt remain observations and are never retracted.
5. Explicit cancellation, usage limits, output retry exhaustion, tool failures, and deferred/HITL boundaries stop semantic recovery.
6. Interrupted tool history records uncertainty rather than exactly-once claims.
7. A terminal event is delivered only after successful cleanup.
8. External async cancellation cannot be converted into success or suppressed by cleanup.

## Trade-offs

### One Logical Run with Several Pydantic Attempts

Keeping recovery inside the existing outer context preserves plugin and Environment continuity and one usage budget. It means event consumers can observe activity from an attempt that later restarts, so terminal state rather than event absence determines completion.

### Bounded Recovery vs. General Workflow Replay

The Harness repairs narrow model interruption only. Durable replay, side-effect reconciliation, and worker recovery remain Host/provider concerns, preventing a local retry mechanism from becoming an orchestration engine.
