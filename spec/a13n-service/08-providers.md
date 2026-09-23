# Providers: what a run calls

A provider is code the service calls while a run executes, selected by `type`. The service's core never knows which implementations exist; it asks the registry for the one matching a configured resource's `type` and talks to it through a small interface. Adding a backend is adding a module under `providers/` and one line in the registry list.

## The word "provider"

- A **provider resource** is a tenant-configured backend record managed under `resources/`.
- A **provider definition** registers an implementation's schemas and factory under `providers/`.
- A **provider handle** is the runtime object built from configuration and credentials; its caller owns its lifetime.

Resource references resolve through the owning service: a web selection names a web-provider resource directly, while a model or environment template resolves its provider reference. The resource's `type` selects a registered definition.

Connections select tool-source definitions through `type`, using the same registry rules: `mcp` is built in, and every other type is served by a connector provider resource. Their authentication mode is a separate field; adding an implementation does not require a core CHECK-enum change.

## Interfaces

The interfaces below describe capability boundaries. Reuse existing Harness contracts where available; `providers/interfaces.py` contains no I/O.

```python
class ModelProvider(Protocol):
    """Provided by the Harness ProviderCatalog; the service selects, never implements."""

# EnvironmentProvider is defined in 06-environments.md; do not invent a second
# open-that-also-creates lifecycle here.

class WebProvider(Protocol):
    """Search and scrape capabilities provided by the Harness."""

class ToolSource(Protocol):
    type: str
    async def discover(self, connection: ConnectionConfig, auth: Auth) -> list[ToolInfo]: ...
    async def open(self, connection: ConnectionConfig, auth: Auth, headers: Mapping[str, str]) -> Toolset: ...

class TraceProvider(Protocol):
    type: str
    async def query(self, harness_run_id: str, correlation: Correlation) -> Trace: ...
```

The service composes provider capabilities into the Harness run; host HTTP supplies fetch/download alongside search/scrape bindings.

## Registry

```python
@dataclass(frozen=True)
class ProviderDefinition:
    kind: Literal["model", "environment", "connector", "web", "trace"]
    type: str
    config_schema: type[BaseModel]
    credential_schema: type[BaseModel] | None
    factory: ProviderFactory               # discriminated, typed factory matching kind

def get(kind, type) -> ProviderDefinition   # raises unavailable(dependency=f"{kind}:{type}")
def types(kind) -> list[ProviderDefinition]
```

The registry collects built-in and distribution definitions at startup. Resources use their schemas for validation; execution selects an implementation by `(kind, type)`. The Console queries available types through `GET /provider-types/{kind}`. Existing Harness definitions are reused, including the model `ProviderCatalog` and Web definitions, without a second implementation catalogue.

## What a provider may and may not do

- It receives plain values: the resource's `config`, the revealed `credential`, and for environments the template config and a handle. It never receives a database session, a row, or a principal.
- It performs I/O with operation-specific deadlines and reports classified errors: rejected, retryable-before-dispatch, known failed, or unknown-after-dispatch. Core chooses recovery based on those facts, not on every exception being retryable.
- It may keep process-local state (clients, connection pools) keyed by resource id and credential digest, and must drop it when the credential changes.
- It may use Redis for ephemeral coordination, such as discovery caches, under the prefix `provider:{type}:`. It never creates PostgreSQL tables in the core schema; any required durable provider rows belong to its own module, use the distribution's migration path, and are never referenced by a core foreign key. HTTP envd uses the existing provider/environment records and does not require private tables or Redis coordination; see [06](06-environments.md#envd-over-http).

## Provider rollout

| Kind        | Types                                                                                      | Notes                                                                                                                                                                |
| ----------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| model       | whatever the Harness catalogue ships (OpenAI, Anthropic, Google, ... )                     | bridged in `providers/models`                                                                                                                                        |
| environment | `docker`, development-only `local`; `http_envd` and hosted backends qualified individually | managed backends pass lifecycle recovery tests; connect-only HTTP envd passes the identity, Session and uncertain-command gates in 06/12; WebSocket envd is deferred |
| connector   | `composio`                                                                                 | configured once as a connector provider; each connection binds one account of one app through the provider's hosted setup and exposes its pinned actions             |

Remote MCP is the built-in connection type and needs no provider resource: none/bearer/headers/oauth auth, built as a Harness `ContextualMCP` whose header factory returns the run's frozen copy of its thread's `mcp_headers` for that connection, resolved once per logical run. | web | Harness-supported search/scrape implementations | configured accounts in `web_providers`; host transport supplies fetch/download | | trace | `langfuse`, `logfire` | read-only query |

Memory is not a provider kind in this design. Agent memory of any form (providers, agent entries, subjects, organization) is deferred until its scope is decided; nothing here reserves a table, a kind or an option for it.

Factory signatures, schemas and return protocols are validated per kind at assembly. Lifecycle differences remain explicit in the execution orchestrator: environment operations, OAuth and tool discovery do not become one generic lifecycle. Providers must expose cancellation, response bounds and stable dispatch correlation needed by [09](09-runtime.md). Internal provider rows are still subject to tenant isolation and crash testing; moving a mechanism into an adapter does not remove its engineering cost.
