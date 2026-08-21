# Capability and Agent Context Model

## Design Position

Reusable behavior inside the Pydantic Agent loop uses native `AbstractCapability[AgentContext]`. The Harness does not define a second Capability base, lifecycle, ordering graph, or class registry. Trusted native Models, tools, Toolsets, and Capabilities enter `AgentDefinition` directly; fresh run Capabilities enter `RunBindings`.

Harness plugins govern only the outer semantic-input-to-complete-result boundary and may contribute ordinary Pydantic Capabilities.

## Native Composition

| Pydantic primitive                 | Harness use                                               |
| ---------------------------------- | --------------------------------------------------------- |
| `AbstractCapability`               | Core, Agent feature, provider, and Host integration       |
| `CapabilityOrdering`               | Dependencies, order, and wrapper nesting                  |
| `AbstractToolset`/`WrapperToolset` | Tool contribution and managed invocation                  |
| `RunContext[AgentContext]`         | Messages, usage, limits, tools, run-bound peers, and deps |
| Agent/run Capability binding       | Native reentrant and fresh invocation composition         |

Pydantic AI owns `for_agent()`, `for_run()`, Toolset composition, lifecycle hooks, node hooks, and cleanup. Capability authors do not inspect private Agent graph state.

## AgentContext

```python
@dataclass(frozen=True, slots=True)
class AgentContext:
    run_id: str
    instance: AgentInstanceContext
    state: AgentContextState
    environment: BoundEnvironment
    model_binding: ModelRunBinding | None
    events: HarnessEventEmitter
    plugins: BoundPluginContext
    subagents: SubagentCollection
    metadata: Mapping[str, JsonValue]

    @property
    def identity(self) -> AgentIdentityRef: ...

    async def export_state(
        self,
        message_history: Sequence[ModelMessage],
    ) -> HarnessState: ...
```

One fresh context is created for every logical Harness run and reused by that run's internal model attempts. Fields have cohesive cross-feature meaning:

- `instance` is the trusted workload, actor, and lineage binding;
- `state` coordinates detached Capability namespaces;
- `environment` is the entered provider facade;
- `model_binding` is the optional fresh logical-model resolver;
- `events` emits bounded Harness-owned observations into the one canonical run stream;
- `plugins` indexes the complete fresh run-bound plugin graph after binding;
- `subagents` is the immutable collection owned by the executable;
- `metadata` is immutable non-authoritative correlation.

`identity` is derived from `instance`; no second value can diverge. The context is not a generic service locator and cannot be supplied by plugins or model content.

## Lifecycle Integration

| Need                                  | Integration                                             |
| ------------------------------------- | ------------------------------------------------------- |
| Produce input after Environment entry | `RunInputFactory`                                       |
| Transform semantic input/result       | Harness plugin `wrap_run()`                             |
| Bind a fresh Agent-loop feature       | Capability `for_run()`                                  |
| Contribute instructions or tools      | Native Capability/Toolset                               |
| Observe model, node, or tool behavior | Native hooks plus `AgentContext.events`                 |
| Resolve a logical Model               | Thin `ResolveModelId` over `AgentContext.model_binding` |
| Store continuation data               | `AgentContextState` namespace                           |
| Persist a checkpoint candidate        | Host adapter using exported `HarnessState`              |

A Capability that needs another run-bound Capability uses Pydantic's public run-bound mapping after binding. A Capability contributed by a Harness plugin resolves the matching fresh plugin through `ctx.deps.plugins.require(id, ExpectedType)`.

The Harness does not validate class-free Host role names or maintain another registry of final Capability replacements. A Host that needs an exact run collaborator constructs a typed Capability and its feature-specific code validates the expected public type and ID before use.

## Capability State

```python
class CapabilityState(BaseModel):
    version: str
    data: JsonValue


class AgentContextState:
    async def read[T: BaseModel](
        self,
        capability_id: str,
        state_type: type[T],
        *,
        version: str,
    ) -> T | None: ...

    async def write(
        self,
        capability_id: str,
        value: BaseModel,
        *,
        version: str,
    ) -> None: ...

    async def snapshot(
        self,
    ) -> AgentContextStateSnapshot: ...
```

The owning Capability chooses a stable non-blank ID, state model, and exact version. `read()` validates those values and returns a detached typed model. `write()` atomically replaces the namespace. `snapshot()` copies every namespace.

The coordinator intentionally has no active-Capability registry. Imported entries need not be consumed before model work, and unknown namespaces remain opaque. A Capability accepts its state only by performing the typed read it requires before dependent behavior.

Trusted Python can intentionally read, replace, migrate, or transfer complete state. Namespace ownership is a composition contract, not a sandbox or cryptographic provenance mechanism.

Pydantic messages live separately in `HarnessState.message_history`. Usage, clients, credentials, policy decisions, queues, locks, Host execution state, plugin objects, and provider sessions are not Capability state.

## State Export

`AgentContext.export_state()` creates a detached `HarnessState` from the supplied complete message view and current snapshot. It performs no persistence I/O and does not consult a Capability codec registry.

The normal inner run exports aligned messages and state. Trusted result middleware may return a different well-formed state for handoff, migration, or caching. The Harness does not require equality with `HarnessRunResult.all_messages()`.

## Categories

The categories describe ownership, not subclasses:

| Category             | Examples                                               |
| -------------------- | ------------------------------------------------------ |
| Agent feature        | Guidance, compaction, memory, working state            |
| Provider integration | Environment tools, model behavior, MCP, skills         |
| Host integration     | Policy, credentials, checkpoint observation, telemetry |
| Tool behavior        | Managed invocation, external tools, discovery          |

Each feature retains its own narrow collaborators and security checks. The Harness does not collect them into a generic map.

## Checkpoint Integration

A Capability may observe a public complete Pydantic boundary and call `AgentContext.export_state()`, then hand the candidate to a typed Host store. The store decides durability, generation, fencing, retention, and failure policy.

The Harness state API itself does not define `CheckpointStore`, choose a latest checkpoint, or load state implicitly. A Host loads one selected `HarnessState` before creating the next run.

## Failure Semantics

| Failure                               | Outcome                                          |
| ------------------------------------- | ------------------------------------------------ |
| Capability composition/order failure  | Pydantic Agent build or run binding fails        |
| Blank namespace ID or version         | `StateError`                                     |
| Version mismatch on typed read        | `capability_state_version_unsupported`           |
| Payload fails owning model validation | `capability_state_invalid`                       |
| Capability hook or Toolset fails      | Native Pydantic/Harness failure handling applies |

## Boundaries

| Concern                              | Owner                                |
| ------------------------------------ | ------------------------------------ |
| Capability lifecycle and Toolsets    | Pydantic AI                          |
| Shared context and state coordinator | Harness                              |
| One feature's state and behavior     | Owning Capability package            |
| Plugin middleware                    | [Plugin System](05-plugin-system.md) |
| Durable checkpoint authority         | Host                                 |

## Trade-offs

### Cohesive Context vs. Generic Dependency Container

A small fixed context makes Identity, Environment, model binding, plugins, children, and state explicit. Feature-specific clients remain typed Capability or tool collaborators instead of undocumented context entries.

### Opaque State Preservation vs. Global Ownership Checks

Opaque namespaces allow independent Capability evolution and trusted state handoff. The Harness cannot assert that every stored entry was accepted by the current composition; only the owning typed read establishes that.
