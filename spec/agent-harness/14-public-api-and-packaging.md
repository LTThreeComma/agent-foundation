# Public API and Packaging

## Design Position

`agent-harness` is distributed as `converge-agent-harness`. Its public API is async for execution and cleanup, while Agent construction is synchronous and code-first. It exposes native Pydantic AI types where upstream already owns the semantics and adds only the process-local definition, context, plugin, state, model-recovery, event, and result boundaries shared by embedded and hosted callers.

The package does not expose a serialized Agent-definition language, compiler, or universal extension framework. Hosted systems reconstruct trusted Python inputs through their own adapters and call the same public builder as embedded applications. The one package-discovery surface is the explicit Environment plugin catalog: it reads Python distribution entry-point metadata and imports only operator-selected Environment factory classes.

## Root Public Surface

The package root exports these contract groups:

| Group                 | Public values                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Definition and build  | `AgentDefinition`, `HarnessBuilder`, `ExecutableAgent`, `CapabilityTypeRegistration`, `CapabilityTypeCatalog`, `SubagentDefinition`, `BuiltSubagent`, `SubagentCollection`, `DelegationContextPolicy`                                                                                                                                                                                                                                                                                                                                                                                |
| Context and identity  | `RunBindings`, `AgentContext`, `AgentIdentityRef`, `AgentInstanceRef`, `AgentInstanceContext`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Input                 | `NativeRunInput`, `RunInputValue`, `SemanticRunInput`, `RunInputFactory`, `RunPreparationContext`, `DeferredToolResume`                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Plugins               | `AbstractHarnessPlugin`, `PluginOrdering`, `BoundPluginContext`, `PluginRunExchange`, `PluginRunNext`, `PluginRunResponse`                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Models and recovery   | `ModelRunBinding`, `SelfHealingModel`, `ModelRecoveryRule`, `ModelRecoveryPolicy`, `RecoveryPromptFactory`                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Environment           | `EnvironmentRunBinding`, `CompositeEnvironmentRunBinding`, `NoopEnvironmentRunBinding`, `BoundEnvironment`, `NoopBoundEnvironment`, `EnvironmentProviderBinding`, `EnvironmentTopologyController`, `EnvironmentPlugin`, `EnvironmentPluginCatalog`, plugin registration/provenance values, `discover_environment_plugins`, `build_environment_plugin_catalog`, `create_environment_run_binding`, `create_noop_environment_run_binding`, topology/binding/readiness/operation values, opaque process/output scalars, Direct Local configuration/binding types, and `EnvironmentError` |
| State                 | `HarnessState`, `EnvironmentState`, `AgentContextState`, `AgentContextStateSnapshot`, `CapabilityState`                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Execution and results | `HarnessRunStream`, `HarnessEvent`, `HarnessExtensionEvent`, `HarnessEventEmitter`, `HarnessRunResultEvent`, `HarnessRunResult`, `HarnessStreamItem`, `SafeFailure`                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Errors                | Stable Harness error subclasses including `DefinitionError`, `ModelResolutionError`, `PluginError`, `RunError`, and `StateError`                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

The Environment root also exports `EnvironmentAction` and `ENVIRONMENT_ACTION_CATALOG_VERSION`; persisted or provider-specific code uses the exact catalog values rather than copying action strings or deriving permission from operation families. The Direct Local root exports are `DirectLocalEnvironmentConfiguration`, `DirectLocalRootConfiguration`, `DirectLocalFilePolicy`, `DirectLocalShellProfile`, `DirectLocalProcessPolicy`, `DirectLocalRetentionPolicy`, `DirectLocalPortPolicy`, and `DirectLocalEnvironmentProviderBinding`. The programmatic opaque scalar exports are `OpaqueProcessHandle`, `OpaqueOutputReference`, and `OpaqueOutputCursor`; their bound wrappers and operation models are exported with the rest of the Environment value types, but no generic JSON serializer is exported for an opaque scalar.

Feature packages also expose their Harness-specific typed Capabilities, including optional `EnvironmentToolsCapability`, `EnvironmentToolsConfiguration`, `InvocationPolicyCapability`, `ClientToolsRunCapability`, `TaskStateRunCapability`, and `ModelCostRunCapability`. `EnvironmentToolsCapability` is a model projection over `BoundEnvironment`, not a lifecycle or authority type. Its tool DTO classes and internal retained-result projector are package-owned implementation types rather than root exports; emitted tool schemas and compact-reference behavior remain the compatibility contract. Capability, Model, Toolset, output, deferred-tool, message, event, usage, and usage-limit authors otherwise import upstream primitives directly from `pydantic_ai`.

## Build API

```python
@dataclass(frozen=True, slots=True)
class AgentDefinition[OutputT]:
    agent: AgentSpec
    output_type: OutputSpec[OutputT] | None
    definition_id: str = <UUID>
    model: Model | KnownModelName | str | None = None
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    plugins: tuple[AbstractHarnessPlugin, ...] = ()
    subagents: tuple[SubagentDefinition, ...] = ()
    self_healing: bool = True
    model_recovery: ModelRecoveryPolicy = ModelRecoveryPolicy()


class HarnessBuilder:
    def __init__(
        self,
        *,
        capability_type_catalog: CapabilityTypeCatalog | None = None,
    ) -> None: ...

    def build[OutputT](
        self,
        definition: AgentDefinition[OutputT],
    ) -> ExecutableAgent[OutputT]: ...

    @overload
    def build_code[OutputT](
        self,
        agent: AgentSpec,
        *,
        output_type: OutputSpec[OutputT],
        definition_id: str | None = None,
        model: Model | KnownModelName | str | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        subagents: Sequence[SubagentDefinition] = (),
        self_healing: bool = True,
        model_recovery: ModelRecoveryPolicy | None = None,
    ) -> ExecutableAgent[OutputT]: ...

    @overload
    def build_code(
        self,
        agent: AgentSpec,
        *,
        output_type: None,
        definition_id: str | None = None,
        model: Model | KnownModelName | str | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        subagents: Sequence[SubagentDefinition] = (),
        self_healing: bool = True,
        model_recovery: ModelRecoveryPolicy | None = None,
    ) -> ExecutableAgent[dict[str, JsonValue]]: ...
```

`HarnessBuilder` accepts at most one exact immutable custom Capability type catalog constructed by trusted Host code; `None` selects the canonical empty catalog. `build_code()` delegates to `build()`. Its explicit-output overload returns `ExecutableAgent[OutputT]`; its `output_type=None` overload requires `AgentSpec.output_schema` and returns `ExecutableAgent[dict[str, JsonValue]]`. Both use the one build-time construction contract in [Agent Definition and Build](03-agent-definition-and-build.md). Function tools and Toolsets enter only through native Capabilities.

## Run Bindings

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

    @classmethod
    def local(
        cls,
        *,
        identity: AgentIdentityRef | None = None,
        environment: EnvironmentRunBinding | None = None,
        model_binding: ModelRunBinding | None = None,
        capabilities: Sequence[
            AbstractCapability[AgentContext]
        ] = (),
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> RunBindings: ...
```

Bindings are fresh trusted inputs for one logical Harness run. Collection and metadata values are copied into immutable views. When `RunBindings.local()` receives no Environment, it calls `create_noop_environment_run_binding()` and never exposes a separate null/no-op execution branch or `is_noop` flag. `model_binding` is the only Harness-specific run-scoped model seam. It resolves a logical string to a native Model or raises; when absent, the thin resolver delegates to Pydantic's native inference.

`RunBindings.capabilities` are passed to every internal Pydantic attempt. Feature-specific fresh attachments use documented public Capability types and stable IDs, then are resolved from Pydantic's finalized run Capability mapping by their owning definition-selected Capability. Missing, duplicate, or incompatible typed collaborators fail in the owner before dependent behavior. The Harness does not inspect them as a second registry or require class-free role names. Identity, Environment, and model resolution remain explicit typed fields because they are universal run construction inputs rather than optional feature collaborators. In particular, an Environment binding cannot be moved into `RunBindings.capabilities`; `EnvironmentToolsCapability` can be omitted without changing resource entry or controller availability.

## Executable API

```python
class ExecutableAgent[OutputT]:
    definition: AgentDefinition[OutputT]
    subagents: SubagentCollection

    async def run(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        bindings: RunBindings,
        previous_state: HarnessState | None = None,
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunResult[OutputT]: ...

    def stream(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        bindings: RunBindings,
        previous_state: HarnessState | None = None,
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunStream[OutputT]: ...

    async def close(self) -> None: ...
```

An immediate input and `input_factory` are mutually exclusive. Omitting both passes no new user input, which permits continuation from imported messages. `deferred_resume` is a separate Harness correlation envelope around native Pydantic requests and results rather than user content. It requires a compatible prior state and current tool surface; after preflight, only its native results are consumed by the first inner attempt. A supplied `RunUsage` remains the one accumulator shared across all internal model attempts; otherwise the Harness creates a fresh value. Native `UsageLimits` are passed to every attempt and remain monotonic through the shared accumulator.

`run()` consumes the canonical stream internally and returns its sole terminal result. `stream()` returns a lazy single-entry async context manager and single-consumer async iterator. `ExecutableAgent` is itself an async context manager whose exit calls idempotent `close()`.

## Stream API

```python
class HarnessRunStream[OutputT](
    AsyncIterator[HarnessStreamItem[OutputT]]
):
    run_id: str

    @property
    def context(self) -> AgentContext: ...

    @property
    def result(self) -> HarnessRunResult[OutputT] | None: ...

    @property
    def usage(self) -> RunUsage: ...

    def cancel(self) -> None: ...

    async def export_state(self) -> HarnessState: ...
```

Context entry allocates the Harness run ID, enters and publishes the initial Environment topology with its paired controller still non-active, restores compatible portable Environment state against that fixed snapshot, activates the controller, optionally builds input, creates `AgentContext`, binds plugins, and builds the outer response. The controller remains valid until the logical terminal fence across every inner attempt and recovery backoff. No model or tool work begins until iteration reaches the inner Pydantic path.

The stream has exactly one consumer and forbids concurrent `__anext__()` calls. It yields normalized `HarnessEvent` values followed by at most one `HarnessRunResultEvent`. Public event sequence numbers are reassigned after plugin transformation and remain monotonic from zero.

`result` remains `None` until the terminal event is actually yielded. Leaving the context earlier establishes the terminal fence and closes resources without synthesizing a normal result. `cancel()` is idempotent and interrupts pre-start, active-attempt, or recovery-backoff work. `export_state()` is valid only while the stream is entered and not closed and includes Environment state collection linearized with topology publication.

The Harness exposes no parallel enqueue queue, safe-pause state machine, durable command receipt, or Pydantic private run handle. `EnvironmentTopologyController` is deliberately a process-local Host mutation seam, not a durable command protocol. Product steering, external mount authorization, and durable command semantics remain Host concerns.

## Result

```python
class HarnessRunResult[OutputT]:
    @property
    def run_id(self) -> str: ...
    @property
    def status(
        self,
    ) -> Literal["completed", "suspended", "failed", "cancelled"]: ...
    @property
    def output(self) -> OutputT | None: ...
    @property
    def state(self) -> HarnessState | None: ...
    @property
    def usage(self) -> RunUsage: ...
    @property
    def failure(self) -> SafeFailure | None: ...
    @property
    def suspend_reason(
        self,
    ) -> Literal["deferred"] | None: ...
    @property
    def deferred(self) -> DeferredToolRequests | None: ...

    def all_messages(self) -> tuple[ModelMessage, ...]: ...
    def new_messages(self) -> tuple[ModelMessage, ...]: ...
    def replace(self, ...) -> HarnessRunResult[Any]: ...
    def raise_for_status(self) -> None: ...
    def output_or_raise(self) -> OutputT: ...
```

All mutable values are copied on construction and access, including the complete nested `RunUsage`. `all_messages()` and `new_messages()` decode detached copies. `replace()` preserves run correlation and message views while allowing trusted middleware to replace terminal fields.

Valid field combinations are:

| Status      | Required                                                         | Excluded                                      |
| ----------- | ---------------------------------------------------------------- | --------------------------------------------- |
| `completed` | state, usage, output compatible with the built `OutputSpec`      | failure, suspension reason, deferred requests |
| `suspended` | state, usage, `suspend_reason="deferred"`, and deferred requests | output, failure                               |
| `failed`    | usage and `SafeFailure`; state when export succeeded             | output, suspension reason, deferred requests  |
| `cancelled` | usage; optional latest state                                     | output, failure, suspension reason, deferred  |

A completed output may legitimately be `None` when the output contract permits it. A result's private message view is independent from the optional continuation state under the trusted plugin contract; structural validation does not impose state provenance or equality.

`raise_for_status()` returns only for completion. `output_or_raise()` preserves a valid `None` output instead of using truthiness.

## Events

```python
class HarnessEvent(BaseModel):
    run_id: str
    sequence: int
    occurred_at: datetime
    event: AgentStreamEvent | HarnessExtensionEvent


class HarnessRunResultEvent(BaseModel):
    run_id: str
    sequence: int
    occurred_at: datetime
    result: HarnessRunResult[Any]
```

Ordinary events wrap validated public Pydantic AI stream events. A terminal result event is emitted only after all run resources close successfully. The event is a process-local completion observation, not a Host durable commit.

## Errors

```python
class SafeFailure(BaseModel):
    code: str
    message: str
    details: dict[str, JsonValue] = {}
    retry_hint: Literal[
        "none", "new_run", "dependency_change"
    ] = "none"
```

`HarnessError` subclasses represent stable caller-facing failures. Original exceptions remain protected causes. `SafeFailure` is the bounded terminal projection and contains no traceback or arbitrary object.

| Condition                                         | Public behavior                                                        |
| ------------------------------------------------- | ---------------------------------------------------------------------- |
| Definition, binding, input, or plugin setup error | Raise typed `HarnessError` before a result                             |
| `UsageLimitExceeded`                              | Failed result with `code="usage_limit_exceeded"`                       |
| Recognized terminal Agent execution failure       | Failed result with `code="agent_run_failed"`                           |
| Exhausted enabled semantic recovery               | Failed result with `code="model_recovery_exhausted"`                   |
| Native deferred output                            | Suspended result with `suspend_reason="deferred"`                      |
| Requested/native cancellation                     | Cancelled result when consumed through the normal stream               |
| External task cancellation                        | Propagate `asyncio.CancelledError` after cleanup                       |
| Trusted-code failure without a safe mapping       | Propagate after cleanup                                                |
| Failure after a valid candidate                   | Raise `RunCleanupError` carrying the nearest valid immutable candidate |

## Packaging and Compatibility

The base distribution depends on Pydantic, Pydantic AI, and the Environment abstractions it directly exposes. Module import loads no provider package, scans no entry points, performs no network request, reads no credential, and configures no global instrumentation. Environment metadata discovery and selected target loading occur only when the caller invokes the explicit catalog API.

The public Python API, Harness state envelope, Environment provider-state codecs, Pydantic message codec, plugin contract, and model recovery rules evolve independently. The package tracks the repository-selected compatible Pydantic AI release and relies only on documented public Agent, Capability, Model, Toolset, message, deferred, event, output, and usage APIs.

Provider packages, hosted adapters, managed-tool policy integrations, Environment implementations, and observability exporters remain optional composition. Environment packages may register factory classes under `converge_agent_harness.environments`; the Harness imports only selected names into an immutable caller-owned catalog and keeps every other extension code-first. There is no process-global registration or import-time auto-enable behavior.

## Boundaries

| Concern                                        | Owner          |
| ---------------------------------------------- | -------------- |
| Agent loop and native extension primitives     | Pydantic AI    |
| Public code-first facade and process-local run | Harness        |
| Durable definitions, artifact locks, execution | Host           |
| Provider and Environment implementation        | Owning package |

## Trade-offs

### Small Code-first Facade vs. a Universal Schema

The facade preserves native Python composition and type fidelity. Hosted products must own stable schemas and adapters rather than expecting the library to serialize arbitrary extension objects.

### Async Execution vs. Sync Construction

Execution and cleanup are async because providers perform I/O. Construction remains synchronous because it validates and composes process-local objects only.
