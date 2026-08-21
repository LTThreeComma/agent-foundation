# Input, Model, and Output Boundaries

## Design Position

The Harness preserves native Pydantic AI input, Model, settings, profile, messages, deferred values, and output semantics. It adds four narrow boundaries:

1. normalized code-first semantic input visible to Harness middleware;
2. optional fresh run-scoped resolution of a logical model ID;
3. exact one-shot provider-history self-healing;
4. bounded logical-run recovery after a recoverable model interruption.

It does not add a hosted input wire format, model registry, settings/profile system, provider route-pin schema, output mode, or Capability-only retry framework.

## Input

```python
type NativeRunInput = str | Sequence[UserContent]
type RunInputValue = NativeRunInput


@dataclass(frozen=True, slots=True)
class RunPreparationContext:
    run_id: str
    instance: AgentInstanceContext
    environment: BoundEnvironment
    metadata: Mapping[str, JsonValue]


type RunInputFactory = Callable[
    [RunPreparationContext],
    Awaitable[RunInputValue],
]


@dataclass(frozen=True, slots=True)
class SemanticRunInput:
    value: str | tuple[UserContent, ...] | None
```

`run()` and `stream()` accept either an immediate value or one async factory, never both. The factory runs exactly once after the Environment core has entered initial provider scopes, restored compatible portable Environment data against the fixed initial topology, and activated the Host-retained controller, but before plugin middleware or Pydantic execution. It can use trusted run identity, metadata, scoped readiness, and the entered Environment without depending on `EnvironmentToolsCapability` or receiving a live Pydantic run handle.

Normalization rules are deliberately small:

- `None` means no new input and does not manufacture an empty prompt;
- a string must be non-empty;
- a `Sequence[UserContent]` must be non-empty and is copied to a tuple;
- bytes and non-sequence values are rejected.

Plugins receive `SemanticRunInput` and can replace it through the same normalization. The trusted `AgentContext` cannot be replaced. Hosted correlation, content references, artifact policy, and transport schemas belong to the Host adapter that creates native Pydantic `UserContent` before calling the Harness.

`DeferredToolResults` is not user content and does not enter `RunInputValue`, `SemanticRunInput`, or plugin input rewriting. The Harness accepts the following separate correlation envelope:

```python
@dataclass(frozen=True, slots=True)
class DeferredToolResume:
    requests: DeferredToolRequests
    results: DeferredToolResults
```

`requests` is the exact terminal pending value returned by the prior Harness run or accepted by the Host, not a value reconstructed from message metadata. `run()` and `stream()` accept it through `deferred_resume=` together with prior `HarnessState` and fresh bindings. The Harness verifies request/result categories, complete coverage, pending message identity, and the current assembled surface before forwarding only `results` through Pydantic AI's native `deferred_tool_results=` parameter on the first inner attempt. A later semantic attempt uses the already incorporated public message history and receives no deferred results again. Both nested values are defensively detached. The envelope carries no authority and is not stored in `HarnessState`; the full validation and Host boundary is owned by [Tool Execution](07-tool-execution.md#approval-and-deferred-calls).

## Model Resolution

Every built Agent receives one thin Pydantic `ResolveModelId` Capability. It uses the fresh `AgentContext.model_binding` only when Pydantic asks to resolve a string model ID.

```python
class ModelRunBinding(ABC):
    async def resolve_model(
        self,
        context: ModelResolutionContext[AgentContext],
        model_id: str,
    ) -> Model: ...
```

Resolution follows these rules:

1. A concrete `Model` supplied at build time is used directly and does not call `ModelRunBinding`.
2. A logical string reaches the thin resolver after the fresh run context exists.
3. With a `ModelRunBinding`, the resolver calls it and requires a native `Model`; an invalid value or exception becomes `ModelResolutionError`.
4. Without a binding, the resolver returns `None`, deliberately delegating to Pydantic AI's native model inference chain.

The Harness has no second model profile, provider settings, registry, route envelope, or fallback policy. Embedded applications may use native inference. A hosted worker that requires fail-closed logical aliases supplies a binding whose own trusted configuration returns an allowed Model or raises.

`RunBindings` supplies a fresh binding for each logical Harness run. The same binding and `AgentContext` are shared by all internal recovery attempts. Pydantic's `ModelResolutionContext` carries the effective Agent dependencies and native resolution semantics.

### Settings and Profile

Pydantic AI retains the complete layering:

- `ModelSettings` expresses request intent and tuning;
- native Model/provider settings merge under upstream rules;
- `Model.profile` and provider adapters own compatibility and rendering facts;
- Capabilities own reusable Agent-loop behavior.

The Harness does not serialize `ModelProfile`, merge profile keys, copy provider settings into a Host schema, or translate `AgentSpec` field by field. A hosted model-integration adapter may own a durable configuration, but it constructs a native Model before returning from `ModelRunBinding`.

## Narrow Self-Healing

When `AgentDefinition.self_healing` is true, every concrete build-time or run-resolved Model is wrapped exactly once in `SelfHealingModel`. The wrapper preserves the native Model interface and profile.

For one non-streaming request, the wrapper:

1. calls the wrapped Model;
2. on an exception, finds the first exact configured recovery rule that matches;
3. mutates the request-local message list through that rule;
4. retries the request once only when the repair changed at least one value;
5. otherwise re-raises the original exception.

For streaming, only stream establishment can replay. Once a stream has been yielded, emitted content cannot be retracted; a later interruption belongs to the Harness semantic attempt loop.

Default rules are narrow tested provider repairs:

| Rule                            | Repair                                                                              |
| ------------------------------- | ----------------------------------------------------------------------------------- |
| Oversized request payload       | Replace inline images with an explicit removal reminder                             |
| Invalid provider item ID        | Remove provider-bound response IDs, metadata, reasoning state, and compaction parts |
| Incomplete Anthropic thinking   | Remove thinking parts                                                               |
| Modified Anthropic thinking     | Remove thinking parts                                                               |
| Stale or unverifiable reasoning | Remove thinking parts                                                               |

The wrapper does not retry generic transport, rate-limit, tool, output-validation, or cancellation failures. Provider/client `RetryConfig` owns transport retry.

Custom `ModelRecoveryRule` values contain one exact matcher and one history repair function. Their safety is the caller's responsibility; the wrapper still permits at most one replay per request.

## Semantic Attempt Recovery

`ModelRecoveryPolicy` owns recovery after an inner Pydantic attempt fails at a recoverable model boundary. It is disabled by default.

```python
@dataclass(frozen=True, slots=True)
class ModelRecoveryPolicy:
    enabled: bool = False
    max_attempts: int = 5
    continuation_prompt: RunInputValue = DEFAULT_RECOVERY_PROMPT
    prompt_factory: RecoveryPromptFactory | None = None
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
```

`max_attempts` is total, including the first attempt. Backoff uses full jitter from zero to the bounded exponential ceiling. A prompt factory may be synchronous or asynchronous and receives the failure, next attempt index, and detached latest messages.

Recoverable failures are narrowly classified model API errors, non-output-exhaustion `UnexpectedModelBehavior`, and public history showing an interrupted model-request boundary. Harness errors, cancellation, usage limits, exhausted output validation, tool failures, and native deferred/HITL results are hard stops.

Each retry uses normalized interrupted history and new semantic input while preserving one outer Harness run, context, Environment, plugin graph, and `RunUsage`. Each Pydantic attempt receives a unique inner run ID. Detailed lifecycle semantics are owned by [Execution Context and Lifecycle](06-execution-context-and-lifecycle.md#model-attempt-recovery).

Provider-suspended continuation is not attempt recovery. Pydantic owns the public suspended message semantics; the Harness does not append a generic route pin or force a special hosted resolver contract.

## Stream Boundary

Pydantic `AgentStreamEvent` values are the source events. The Harness validates each event, adds public Harness correlation and sequence, and lets trusted plugin middleware transform the stream. Public sequence numbers are assigned after transformation.

Events from a recoverable failed attempt remain visible. A later attempt continues the logical run but cannot retract earlier observations. Consumers therefore use the terminal result to determine the logical outcome rather than treating any intermediate model event as completion.

The Harness does not buffer for replay, fan out consumers, persist events, or reconstruct partial provider deltas into synthetic complete messages.

## Output Boundary

Pydantic `OutputSpec`, output validators, output tools, and retry behavior own output production. `AgentDefinition.output_type` is the sole business-output contract, so `AgentSpec.output_schema` is rejected instead of being allowed to replace a bare `str` through `Agent.from_spec()` default semantics. A business output that directly or transitively includes `DeferredToolRequests` or a subclass through a sequence, union, `Annotated` value, PEP 695 type alias, output marker, or callable return type is also rejected at definition construction; that native value is reserved as the Harness suspension control outcome. Every Harness run passes `[definition.output_type, DeferredToolRequests]` as the native per-run Pydantic output contract; this is the upstream control value needed to end the run cleanly, not another business result type. The Harness builds its process-local `TypeAdapter` only from `definition.output_type`, including return annotations of synchronous, `Awaitable`, and `Coroutine` output functions.

On completion, the Harness validates plugin-produced output strictly against that adapter. If Pydantic cannot generate a schema for an otherwise valid arbitrary process-local return type, the adapter permits arbitrary types rather than rejecting the upstream output contract.

A Pydantic result whose output is `DeferredToolRequests` becomes a suspended Harness result rather than a completed business output. `.calls` and `.approvals` retain their native distinct meanings. The later Host or caller supplies the exact pending requests and matching Pydantic results through `DeferredToolResume` in a new logical run with prior state and fresh bindings.

Trusted plugins may replace the complete result candidate, including output, usage, and state. The Harness revalidates field combinations, output type, message suffix, and run correlation. It does not enforce state provenance or require state history to match the result message view.

## Failure Semantics

| Failure                                        | Outcome                                                      |
| ---------------------------------------------- | ------------------------------------------------------------ |
| Invalid immediate or factory input             | Typed input/run error before model work                      |
| Invalid or uncorrelated deferred continuation  | Typed run/deferred error before new model or tool work       |
| Binding raises or returns a non-Model          | `ModelResolutionError`                                       |
| No binding for a logical string                | Delegate to native Pydantic inference                        |
| Exact self-healing repair succeeds             | Replay the same request once                                 |
| Exact repair does not match or changes nothing | Propagate the original Model error                           |
| Recoverable model interruption with budget     | Start another inner attempt after cancellation-aware backoff |
| Recovery budget exhausted                      | Failed result with `model_recovery_exhausted`                |
| Output validation retries exhausted            | No Harness semantic retry                                    |
| Native deferred/HITL output                    | Suspended result with native `DeferredToolRequests`          |
| Invalid plugin-completed output                | `PluginError(code="plugin_result_invalid")`                  |

## Boundaries

| Concern                              | Owner                           |
| ------------------------------------ | ------------------------------- |
| Native input, Model, profile, output | Pydantic AI                     |
| Semantic input and thin resolution   | Harness                         |
| Exact one-shot history repair        | `SelfHealingModel`              |
| Interrupted semantic attempt loop    | Harness run coordinator         |
| Provider transport retry             | Provider/client and Pydantic AI |
| Hosted model catalog and policy      | Host adapter                    |
| Durable deferred execution           | Host                            |

## Trade-offs

### Thin Resolver vs. a Model Framework

One always-installed `ResolveModelId` seam supports fresh hosted authority without changing embedded inference. Returning `None` when no binding exists preserves upstream behavior and avoids a second registry.

### One-shot Repair vs. Broad Automatic Replay

Exact repairs recover known provider-history incompatibilities with a bounded replay. General replay is unsafe after emitted output or side effects and therefore belongs to the separate attempt, provider, or Host layer.
