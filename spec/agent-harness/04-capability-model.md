# Capability and Agent Context Model

## Design Position

Reusable behavior inside the Pydantic Agent loop uses native `AbstractCapability[AgentContext]`. Capability is the only top-level feature-behavior composition plane in `AgentDefinition`: each feature Capability owns its callable tools, private Toolsets, instructions, settings, hooks, and native ordering as one coherent unit. The Harness does not define a second Capability base, lifecycle, or ordering graph. Fresh run attachment Capabilities enter `RunBindings` under a separate source policy.

Environment itself is not a Capability. It is a Harness-entered run lifecycle resource exposed through the fixed `AgentContext.environment` field. The optional `EnvironmentToolsCapability` consumes that field to contribute model tools, stable guidance, dynamic context, and notices; its presence cannot create, activate, replace, authorize, or close an Environment binding.

Harness plugins govern only the outer semantic-input-to-complete-result boundary and may contribute ordinary Pydantic Capabilities.

## Native Composition

| Pydantic primitive                 | Harness use                                                 |
| ---------------------------------- | ----------------------------------------------------------- |
| `AbstractCapability`               | Agent-loop behavior, model projection, and Host integration |
| `CapabilityOrdering`               | Dependencies, order, and wrapper nesting                    |
| `AbstractToolset`/`WrapperToolset` | Tool contribution and managed invocation                    |
| `RunContext[AgentContext]`         | Messages, usage, limits, tools, run-bound peers, and deps   |
| Agent/run Capability binding       | Native reentrant and fresh invocation composition           |

Pydantic AI owns `for_agent()`, `for_run()`, Toolset composition, lifecycle hooks, node hooks, and cleanup. Capability authors do not inspect private Agent graph state. A direct function tool is authored inside native `Capability(tools=[...])`; an external or custom Toolset is owned by a native Toolset Capability or another feature Capability. `AgentDefinition` and `HarnessBuilder.build_code()` expose no peer `tools` or `toolsets` parameters.

Pydantic's finalized Capability map and ToolManager remain authoritative. Harness stable IDs support uniqueness, lookup, and source provenance only. `CapabilityOrdering.wraps` and `wrapped_by` use concrete Capability types or instances, and `requires` uses concrete types; IDs are not ordering references, and the Harness adds no second Capability sorter.

## Capability Sources and Authorization

Capability availability and Capability grant are distinct. The Harness assigns every Capability to one construction source and rejects a type or reserved ID from any source not explicitly allowed:

| Source                                     | Accepted value                                                        | Authority boundary                                           |
| ------------------------------------------ | --------------------------------------------------------------------- | ------------------------------------------------------------ |
| Native `AgentSpec.capabilities`            | Native built-ins and exact Host-authorized serializable feature types | Definition behavior only; no fresh Host authority            |
| `AgentDefinition.capabilities`             | Concrete `AbstractCapability[AgentContext]` feature instances         | Trusted process-local definition behavior                    |
| `AbstractHarnessPlugin.get_capabilities()` | Concrete plugin-owned feature or infrastructure instances             | Plugin contribution only                                     |
| Mandatory Harness infrastructure           | Exact Harness-created concrete types                                  | Framework authority; never declarative or caller-replaceable |
| `RunBindings.capabilities`                 | Exact documented concrete fresh attachment/policy types               | Current-run authority under reserved types and IDs           |

A type is denied from every unlisted source. Mandatory infrastructure and run-only authority types never enter the declarative custom-type catalog. A definition or plugin instance cannot use `for_run()` to replace itself with a run-only reserved type or ID, and a run attachment cannot launder itself into definition or mandatory infrastructure.

Bare Pydantic `CapabilityFunc` values are not accepted in `AgentDefinition`, plugin contributions, or `RunBindings`. Pydantic resolves such a function once per native Agent run, while one logical Harness run can contain several native recovery attempts. Support requires a future Harness-bound form that resolves once per logical run, validates the complete result tree, memoizes it, and reuses the exact result across every attempt. Concrete `AbstractCapability` instances are the current contract.

## Declarative Custom Capability Types

`HarnessBuilder` can receive one exact immutable `CapabilityTypeCatalog` constructed by trusted Host code. The catalog is narrow AgentSpec reconstruction input, not a general class registry or package-discovery service:

```python
@dataclass(frozen=True, slots=True)
class CapabilityTypeRegistration:
    serialization_name: str
    capability_type: type[AbstractCapability[AgentContext]]


@dataclass(frozen=True, slots=True)
class CapabilityTypeCatalog(
    Mapping[str, CapabilityTypeRegistration]
):
    registrations: tuple[CapabilityTypeRegistration, ...] = ()

    @property
    def custom_capability_types(
        self,
    ) -> tuple[type[AbstractCapability[AgentContext]], ...]: ...

    @classmethod
    def from_types(
        cls,
        capability_types: Sequence[
            type[AbstractCapability[AgentContext]]
        ],
    ) -> CapabilityTypeCatalog: ...
```

Every registered class is a direct dataclass-declared `AbstractCapability`, has a non-blank stable serialization name, does not collide with native or Harness names, is authorized for the `AgentSpec` source, and can participate in deterministic native schema construction. The Host owns package discovery, installation trust, artifact locks, and catalog population. Two builders can use different immutable catalogs in one process without global mutation.

Before `Agent.from_spec()`, the Harness validates every visible `CapabilitySpec` name and nested capability-valued spec against the native registry plus the exact catalog. After construction, it traverses the complete instantiated Capability tree and verifies type/source permission, stable IDs, singleton constraints, and reserved infrastructure provenance before publishing the executable. At each native run boundary it revalidates the finalized Capability mapping so `for_run()` replacement cannot change a protected type, ID, or source. Custom type availability alone grants no Capability; the `AgentSpec` must explicitly select it.

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
- `environment` is the entered Harness lifecycle facade, independent of Capability composition;
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
| Store Capability continuation data    | `AgentContextState` namespace                           |
| Operate on or observe Environment     | Fixed `AgentContext.environment` resource               |
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

Pydantic messages and portable Environment state live in separate `HarnessState` fields. Desired topology, Environment provider lifecycle or launch state, readiness, usage, clients, credentials, policy decisions, queues, locks, Host execution state, plugin objects, and provider sessions are not Capability state. A Capability cannot obtain lifecycle authority by copying an Environment selector or observation into its namespace.

## State Export

`AgentContext.export_state()` creates a detached `HarnessState` from the supplied complete message view, current Capability snapshot, and portable Environment export. It performs no persistence I/O and does not consult a Capability codec registry; Environment collection is owned by the fixed core resource rather than a Capability namespace.

The normal inner run exports aligned messages and state. Trusted result middleware may return a different well-formed state for handoff, migration, or caching. The Harness does not require equality with `HarnessRunResult.all_messages()`.

## Categories

The categories describe ownership, not subclasses:

| Category             | Examples                                                  |
| -------------------- | --------------------------------------------------------- |
| Agent feature        | Guidance, compaction, memory, working state               |
| Provider integration | `EnvironmentToolsCapability`, model behavior, MCP, skills |
| Host integration     | Policy, credentials, checkpoint observation, telemetry    |
| Tool behavior        | Managed invocation, external tools, discovery             |

Each feature retains its own narrow collaborators and security checks. The Harness does not collect them into a generic map.

## Checkpoint Integration

A Capability may observe a public complete Pydantic boundary and call `AgentContext.export_state()`, then hand the candidate to a typed Host store. The store decides durability, generation, fencing, retention, and failure policy.

The Harness state API itself does not define `CheckpointStore`, choose a latest checkpoint, or load state implicitly. A Host loads one selected `HarnessState` before creating the next run.

## Failure Semantics

| Failure                                               | Outcome                                                   |
| ----------------------------------------------------- | --------------------------------------------------------- |
| Capability composition/order failure                  | Pydantic Agent build or run binding fails                 |
| Unknown, colliding, or source-denied declarative type | Definition build fails before model work                  |
| Bare `CapabilityFunc` or source-laundered replacement | Definition build or run setup fails before model exposure |
| Blank namespace ID or version                         | `StateError`                                              |
| Version mismatch on typed read                        | `capability_state_version_unsupported`                    |
| Payload fails owning model validation                 | `capability_state_invalid`                                |
| Capability hook or Toolset fails                      | Native Pydantic/Harness failure handling applies          |

## Boundaries

| Concern                                         | Owner                                |
| ----------------------------------------------- | ------------------------------------ |
| Capability lifecycle and Toolsets               | Pydantic AI                          |
| Shared context and Capability-state coordinator | Harness                              |
| Environment lifecycle and portable aggregate    | Harness Environment core             |
| One feature's state and behavior                | Owning Capability package            |
| Plugin middleware                               | [Plugin System](05-plugin-system.md) |
| Durable checkpoint authority                    | Host                                 |

## Trade-offs

### Capability-only Composition vs. Peer Tool Fields

One feature owner keeps tools, Toolsets, instructions, settings, hooks, and availability coherent and lets Pydantic own their native lifecycle. Authors wrap a standalone tool or Toolset in a small native Capability instead of granting it through a second top-level plane.

### Cohesive Context vs. Generic Dependency Container

A small fixed context makes Identity, Environment, model binding, plugins, children, and state explicit. Feature-specific clients remain typed Capability or tool collaborators instead of undocumented context entries.

### Opaque State Preservation vs. Global Ownership Checks

Opaque namespaces allow independent Capability evolution and trusted state handoff. The Harness cannot assert that every stored entry was accepted by the current composition; only the owning typed read establishes that.
