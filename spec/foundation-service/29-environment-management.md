# Environment Providers, Revisions, Targets, and Runtime

## Design Position

Foundation manages the complete runtime lifecycle of an Environment target selected
for a Run. It supports two ownership modes:

- **managed**: Foundation creates, observes, retains, and destroys the exact target;
- **attached**: Foundation attaches to, observes, and where supported retains an
  existing external target, but never destroys, replaces, starts, resumes, pauses, or
  stops it.

The configuration plane and runtime plane are separate. `Environment` and immutable
`EnvironmentRevision` records describe reusable desired configuration.
`EnvironmentTarget` represents one concrete runtime sandbox, container, or attached
workspace selected for one root Run execution family. Publishing a revision performs
no provider target I/O and creates no target.

A target is materialized only when a Run using a named revision or inline
Environment is accepted. Every independent root Run gets a new managed target; an
Environment revision is a template, not a singleton sandbox or a pool key. Replacement
RunAttempts for the same Run reuse its target. Async children using `shared_root`
bind to the same target. A Retry, Continue, fork, or `dedicated` async child is a new
Run and receives a new target.

The shared [`a13n-environment-provider`](../agent-environment-provider/README.md)
package supplies the single Provider plugin, provider-side lifecycle Control,
optional portable state, and data-plane Environment. Foundation owns Provider
connections, Environment configuration and revisions, durable lifecycle policy,
association, authorization, scheduling, persistence, and reconciliation; Provider
code owns native API mapping but no Foundation resource or state machine.

## Boundaries

| Concern                                             | Owner                                        |
| --------------------------------------------------- | -------------------------------------------- |
| Trusted Provider discovery and exact package lock   | Foundation deployment and Provider catalog   |
| Provider account, endpoint, and credential bindings | `EnvironmentProviderConnection`              |
| Reusable Environment metadata and current revision  | `Environment`                                |
| Immutable managed recipe or attached reference      | `EnvironmentRevision` or inline config       |
| Concrete target identity, state, and lifecycle      | `EnvironmentTarget`                          |
| Run-to-target active-use relation                   | `RunEnvironmentBinding`                      |
| Lifecycle policy, operation claim, and fencing      | Foundation                                   |
| Native create, attach, observe, retain, destroy I/O | `EnvironmentControl`                         |
| Exact-target file, shell, process, output, port I/O | shared `Environment` data-plane adapter      |
| Multi-mount policy and adapter entry/close          | Harness                                      |
| Provider Secret storage and current eligibility     | [Secret Management](27-secret-management.md) |

Neither a Provider key, connection ID, target ID, portable state, nor Run binding is
an authorization grant. Foundation reauthorizes the exact tenant resources and
credential sources at each lifecycle or RunAttempt boundary.

## One Shared Provider Abstraction

Foundation uses the shared `EnvironmentProvider` directly. It does not define a
Foundation-only attachment Provider or a second lifecycle Provider.

```python
provider = catalog.require(provider_key)
connection_options = provider.validate_connection_options(...)
target_spec = provider.validate_target_spec(
    connection_options=connection_options,
    specification=effective_provider_target_spec,
)

# Pure dependency binding; no provider I/O.
control = provider.create_control(
    connection_options=connection_options,
    credentials=current_credentials,
    runtime=fresh_runtime,
)

# Also pure; exact target already exists and is ready.
environment = provider.create_environment(
    connection_options=connection_options,
    target_spec=target_spec,
    state=current_target_state,
    credentials=current_credentials,
    runtime=fresh_runtime,
)
```

`EnvironmentControl` handles create, attach, observe, readiness, retain, and destroy.
It is a short-lived connection-bound client held only by Foundation's trusted
lifecycle path; Harness, Agent UI, and Agent code never receive it. `Environment` is
the existing `a13n-environment-provider` data-plane interface used by Harness. Its
`enter()` and `close()` connect to and release an exact ready target without changing
target lifecycle.

Foundation owns the persistent split between `EnvironmentProviderConnection` and
Environment revision or inline configuration. The shared package does not model
those resources: Foundation resolves them into Provider-owned connection options and
an effective target specification immediately before validation and invocation.
Provider validation and both constructors are deterministic and perform no external
I/O. Lifecycle methods run outside database transactions. A Control has no access to
Foundation ORM models, repositories, database sessions, Run policy, retention
windows, or tenant authorization.

## Provider Catalog and Workspace Selection

Foundation exposes a safe deployment-trusted catalog. Each entry includes:

- stable Provider key and display metadata;
- Provider-owned connection-option and target-option JSON Schemas and versions;
- managed, attached, retain, artifact, resource, network, and operation capabilities;
- required runtime credential binding names; and
- exact dependency/package lock and compatibility evidence.

Catalog reads import no Provider code and perform no credential or provider I/O.
Provider code comes from fixed
[distribution composition](02-distribution-composition-and-extensions.md) or an
immutable managed Environment Provider package revision. Managed Provider artifacts
reuse the upload, hashing, object storage, dependency validation, cache, and conflict
substrate defined for
[managed Harness plugins](36-managed-harness-plugins-and-runtime.md), but keep a
separate extension identity and entry-point group.

Operator-only package publication routes remain outside the tenant API:

```http
POST /internal/v1/environment-provider-package-revisions
GET /internal/v1/environment-provider-package-revisions/{package_revision_id}
```

Workspace Provider selection enables one exact catalog/package lock. Selection is a
trust and availability gate, not a credential or target configuration. Disabling a
selection blocks new connections, revisions, and Runs. It does not abandon cleanup:
Foundation may still load the exact retained lock and resolve the connection's
workspace-owned lifecycle credentials to reconcile or destroy existing managed
targets.

Provider code is trusted in-process worker code. Upload therefore grants code
execution authority and is deployment-operator-only. Untrusted Providers require a
separate out-of-process protocol and security boundary.

## Provider Connections

`EnvironmentProviderConnection` is a Workspace resource that binds a Provider
account or local control-plane endpoint to lifecycle credentials:

```python
class EnvironmentProviderConnection:
    id: EnvironmentProviderConnectionId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    name: str

    provider_key: str
    provider_package_revision_id: EnvironmentProviderPackageRevisionId | None
    provider_lock: DependencyLock
    schema_version: str
    configuration: JsonObject
    credential_bindings: Mapping[str, WorkspaceSecretCredential]

    enabled: bool
    version: int
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

The connection identifies an account, API endpoint, region/default profile, Docker
engine profile, or equivalent Provider control plane. It is not an E2B sandbox ID,
Docker container ID, target selection query, Environment preset, or Run binding. One
connection can operate many targets in the authorized Provider account.

`configuration`, Provider identity, exact package lock, and credential binding names
and Secret IDs are immutable. Changing account, endpoint, package lock, schema, or
credential source creates a new connection. The value of a referenced Workspace
Secret can rotate in place under the Secret contract without changing connection
identity. `name` and `enabled` are mutable under ETag/`If-Match` concurrency.

Lifecycle credentials must use `WorkspaceSecretCredential`; invoking-User Secrets
are rejected. Foundation may need the credential after the invoking User disappears
in order to retain or destroy a managed target. Provider connection values contain
no plaintext secret, live client, session, exact target ID, or short-lived endpoint.

Disabling a connection blocks new revision publication, inline selection, target
creation, and attachment. It does not revoke or erase already accepted Runs, and it
does not block required reconciliation or managed cleanup. Deleting an underlying
Secret causes new work to fail and cleanup to retry with a bounded operator-visible
error; Foundation never marks an unknown managed target destroyed merely because
credentials are unavailable.

## Environment and Revision Configuration

The public `Environment` resource is reusable static configuration identity and
metadata. It is not the shared process-local adapter and not a concrete target.

```python
class FoundationEnvironment:
    id: EnvironmentId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    name: str
    description: str | None
    version: int
    current_revision_id: EnvironmentRevisionId
    archived_at: datetime | None
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

Changing effective configuration publishes a new immutable revision and advances the
head. Name, description, and archive state are mutable metadata only.

### Common managed create configuration

```python
class ManagedEnvironmentConfiguration:
    mode: Literal["managed"]
    artifact: EnvironmentArtifact
    resources: EnvironmentResources | None
    runtime: EnvironmentExecutionRequirements
    network: EnvironmentNetworkConfiguration
    initialization: EnvironmentInitializationConfiguration | None
    provider_options: EnvironmentProviderOptions
    access: EnvironmentAccess
```

The common fields are deliberately bounded:

| Field            | Portable meaning                                                       |
| ---------------- | ---------------------------------------------------------------------- |
| `artifact`       | Immutable OCI image, Provider template, snapshot, or blueprint ref     |
| `resources`      | Requested CPU, memory, disk, and process ceilings                      |
| `runtime`        | Architecture, logical working directory, and envd runtime contract     |
| `network`        | Egress class and explicitly exposed provider-local ports               |
| `initialization` | Reviewed bootstrap profile and literal or Secret-backed runtime values |
| `access`         | Maximum Harness file, shell, process, output, and port permissions     |

Each Provider declares which common variants it supports and validates their mapping
with the selected connection. Unsupported values fail revision publication or inline
admission; they are never ignored or approximated.

`provider_options` is a tagged `{provider_key, schema_version, options}` object owned
by the Provider. It carries reviewed native fields that do not have honest portable
semantics—for example Docker pull policy and mounts, or E2B timeout and metadata. It
rejects unknown keys and raw SDK keyword dictionaries. Provider options cannot
override common ownership, Secret, network, access, operation-correlation, or cleanup
rules.

The artifact is where dependencies are installed. Foundation does not accept generic
apt, pip, npm, arbitrary Dockerfile, or unbounded shell setup lists and does not build
an image or E2B template during Run admission. Changing dependencies, image,
template, bootstrap profile, or Provider options publishes another revision.

### Attached configuration

```python
class AttachedEnvironmentConfiguration:
    mode: Literal["attached"]
    target: JsonObject
    expected_artifact: EnvironmentArtifact | None
    expected_runtime: EnvironmentExecutionContract
    provider_options: EnvironmentProviderOptions
    access: EnvironmentAccess
```

`target` contains the exact non-secret Provider reference, such as one sandbox ID,
container ID, or authorized local path. During attach, Provider validation may
normalize it into `EnvironmentState` when later access needs an independent portable
selector. Direct Local and Local Envd retain no state because validated configuration
already identifies their Host workspace. Attachment never transfers ownership.
Attached configuration rejects managed resource, create, and destruction fields.

An attached target must already be ready. Missing, stopped, paused, inaccessible,
incompatible, or ambiguous evidence fails without create, start, resume, replacement,
or destructive mutation.

### Immutable revision

```python
class EnvironmentRevision:
    id: EnvironmentRevisionId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    environment_id: EnvironmentId
    version: int
    provider_connection_id: EnvironmentProviderConnectionId
    configuration: ManagedEnvironmentConfiguration | AttachedEnvironmentConfiguration
    content_digest: str
    source_revision_id: EnvironmentRevisionId | None
    created_by: PrincipalRef
    created_at: datetime
```

`provider_connection_id` selects the immutable Provider account/endpoint and its
credential source; it never contains or denotes a concrete sandbox ID. For managed
mode, the revision is only a target create recipe. The actual native ID appears later
in `EnvironmentTarget.provider_state`. For attached mode, the exact external ID is
part of `configuration.target` because attachment itself is the declared preset.

Revision creation performs pure Provider validation and freezes exact Provider lock,
connection ID, normalized configuration, access ceiling, required Secret references,
and digest. It performs no provider I/O, creates no target, and stores no
`environment_target_id`.

Inline Environment selection uses the same discriminated managed-or-attached schema
and `provider_connection_id`, but creates no reusable Environment or revision. The
accepted Run freezes the canonical inline value and digest. This contract preserves
the current Agent/Run Environment selection surface; the independent question of
moving that selection to Thread topology is outside this change.

## Accepted Execution Configuration

`EnvironmentExecutionConfig` inside `EffectiveAgentConfig` freezes:

- source kind and named `environment_revision_id` or canonical inline configuration;
- `provider_connection_id`, Provider key, exact package/runtime lock, and schema
  versions;
- normalized managed-or-attached configuration and content digest;
- access ceiling and runtime Secret requirements; and
- ownership mode.

No Secret value, live client, Provider target ID, `EnvironmentState`, lifecycle
phase, operation receipt, or process-local adapter enters this snapshot. Current
authorization, connection eligibility, Secret eligibility, and package availability
are rechecked at admission and execution boundaries without changing the frozen
selection.

## Runtime Target Model

`EnvironmentTarget` is a Workspace-scoped internal runtime record. It represents one
concrete target for one root Run execution family, not a reusable global target and
not a public Environment.

```python
type EnvironmentTargetOwnership = Literal["managed", "attached"]
type EnvironmentTargetPhase = Literal[
    "pending",
    "provisioning",
    "ready",
    "idle",
    "destroying",
    "retired",
]
type EnvironmentTargetOperationKind = Literal[
    "create",
    "attach",
    "retain",
    "destroy",
]
type EnvironmentTargetOperationPhase = Literal[
    "claimed",
    "dispatched",
    "outcome_unknown",
]


class EnvironmentTarget:
    id: EnvironmentTargetId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    environment_root_run_id: RunId
    provider_connection_id: EnvironmentProviderConnectionId
    provider_key: str
    provider_lock: DependencyLock
    ownership: EnvironmentTargetOwnership

    environment_revision_id: EnvironmentRevisionId | None
    inline_environment_digest: str | None
    configuration_digest: str
    provider_state: EnvironmentState | None
    phase: EnvironmentTargetPhase
    active_binding_count: int
    target_generation: int

    idle_at: datetime | None
    retire_after: datetime | None
    retained_until: datetime | None

    operation_kind: EnvironmentTargetOperationKind | None
    operation_id: str | None
    operation_request_digest: str | None
    operation_phase: EnvironmentTargetOperationPhase | None
    operation_claim_generation: int
    operation_owner_worker_generation: str | None
    operation_lease_expires_at: datetime | None
    operation_started_at: datetime | None
    next_operation_at: datetime | None
    last_observed_at: datetime | None
    last_error: SafeProviderError | None

    created_at: datetime
    updated_at: datetime
```

Exactly one source reference is present. The target copies the immutable execution
configuration digest and exact Provider lock required for reconstruction. Managed
`provider_state` becomes non-null as soon as create confirms exact native identity.
An attached stateful Provider publishes state after validating and normalizing the
configured reference. Direct Local and Local Envd remain `provider_state=None`; their
frozen attached configuration and target row are the authoritative Foundation
identity. Any present state is protected data and the authoritative Foundation copy.
Null `provider_state` does not encode target absence or readiness; `phase` and the
confirmed Provider observation own that distinction.

There is no uniqueness constraint over provider-native target identity. Two attached
Run families can intentionally reference the same external sandbox while retaining
independent authorization and active-use accounting. Foundation never globally
deduplicates targets across Runs, Workspaces, or Organizations and never discloses
whether another tenant supplied the same native ID.

`active_binding_count` is non-negative. `ready` requires a positive count; `idle`,
`destroying`, and `retired` require zero. `pending` and `provisioning` normally have
the owner binding already counted while the accepted Run waits for readiness.

`retire_after` is set to `idle_at + 10 minutes`. Idle is a non-reusable cleanup grace,
not a warm pool: no new Run or child can acquire an idle target. Managed targets
transition through destroy and become `retired` only after absence is confirmed.
Attached targets become locally `retired` after the grace without a Provider destroy.

## Run Binding Instead of a Separate Use Lease

The existing `RunEnvironmentBinding` owns active-use membership; Foundation defines
no `EnvironmentUseLease` table.

```python
type RunEnvironmentBindingRole = Literal["owner", "shared_child"]


class RunEnvironmentBinding:
    id: RunEnvironmentBindingId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    run_id: RunId
    environment_target_id: EnvironmentTargetId
    role: RunEnvironmentBindingRole
    environment_revision_id: EnvironmentRevisionId | None
    environment_execution_digest: str
    bound_at: datetime
    released_at: datetime | None
```

There is at most one binding per Run. The owner binding belongs to
`environment_root_run_id`; a `shared_child` binding belongs to an async child using
`shared_root`. Binding insertion and count increment occur in the same short
transaction. The first terminal transition of a bound Run sets `released_at` and
decrements the count in the same transaction; replay is a no-op.

Bindings are immutable except for their one-way release timestamp. They contain no
heartbeat, renewable expiry, worker ownership, credential, or process-local adapter.
Run state, not a second lease row, determines whether use remains active.

## Admission and Materialization

Run admission performs pure validation and database writes only:

1. resolve and authorize the exact named revision or inline configuration;
2. require its Provider selection and connection to be enabled and compatible;
3. freeze `EnvironmentExecutionConfig` in the accepted Run;
4. create one `pending` `EnvironmentTarget` for a new root/dedicated Run; and
5. create its owner `RunEnvironmentBinding` and set count to one in the same short
   transaction.

Admission makes no provider API call and holds no transaction across package loading,
credential resolution, or other external I/O. A lifecycle Worker later claims the
target, resolves current workspace lifecycle credentials, constructs a fresh
Control, and performs managed create or attached attach. A RunAttempt cannot enter
Harness until the target is durably `ready`. It supplies the recorded
`provider_state`, including `None`, to the pure Environment constructor; the Provider
rejects missing required state or unexpected state for a stateless configuration.

For managed mode, `pending -> provisioning` records a create intent. The state moves
to `ready` only after create identity and readiness are confirmed and state is
persisted. For attached mode, attach performs no mutation and the same transition
occurs only after exact target readiness is confirmed. A lost response leaves the
target in `provisioning` with an operation slot for reconciliation; it never becomes
`ready` merely because a request was sent.

If the owner Run becomes terminal before materialization completes, the binding is
released. A managed create that is later confirmed is still recorded and proceeds to
idle cleanup and destroy. An attached late success proceeds to local idle retirement.
Foundation never drops a possibly-created target from durable accounting.

A confirmed permanent create, attach, compatibility, or authorization failure, or
expiration of the owning Run's fixed admission/recovery deadline, seals that Run as
failed and releases its binding in one transaction. An unknown Provider outcome is
not a confirmed permanent failure: Foundation first preserves operation identity and
reconciles it, because failing the Run does not remove cleanup responsibility for a
possibly-created managed target.

When failure evidence also confirms that no managed target exists, or an attach
finishes without establishing an attached target, Foundation moves the zero-count
record directly to `retired`. When a managed identity is already known, releasing
the last binding moves it to non-reusable `idle` so ordinary destroy policy removes
it. An unknown create outcome may remain `provisioning` with zero active bindings
until reconciliation establishes identity or confirmed absence.

## Attempt, Successor, Fork, and Child Semantics

- A replacement `RunAttempt` for the same Run reuses the same binding, target, and
  optional provider state. It creates a fresh process-local Environment adapter.
- A Retry, Continue, deferred-action successor, or fork is a new Run. If it inherits
  an Environment selection, it creates a new target and owner binding.
- An async child with `shared_root` inserts a `shared_child` binding to the root's
  current `ready` target and increments its count atomically. It cannot bind after
  the target enters idle.
- An async child with `dedicated` uses its frozen child Environment selection and
  creates its own target and owner binding.
- `none` creates no target or binding.
- Inline child execution borrows the already-entered parent Harness facade and
  creates no Run, target, binding, or count contribution.

`shared_root` requires an equal Provider target and an equal or narrower access
ceiling. A child cannot widen the root's Environment authority. Parent-first
completion releases only the parent's binding; the target remains ready while any
accepted or running shared child binding remains active.

## Runtime Entry and Finalization

For each claimed independent RunAttempt, the Worker:

1. reads the exact execution config, binding, target, Provider lock, and optional
   state and verifies all digests and fences;
2. reauthorizes Environment use and resolves fresh runtime and Provider credentials;
3. performs no Environment work when the Run has no binding;
4. constructs one fresh shared-package `Environment` for the exact target without
   external I/O;
5. supplies it as Harness mount `workspace` under the accepted access ceiling;
6. lets Harness enter, route operations, snapshot non-`None` fixed portable state,
   and close the adapter non-destructively; and
7. releases remaining process-local clients during unconditional finalization.

Harness `EnvironmentState` is recovery context, not target authority. Foundation
always reconstructs from the target's frozen configuration and optional
`EnvironmentTarget.provider_state`; a stale Harness copy cannot retarget the Run or
roll back a newer lifecycle observation. For Direct Local and Local Envd, an
authoritative `provider_state=None` suppresses any stale portable fallback.

Worker loss discards only process-local Environment and EIP sessions. A replacement
Attempt re-enters the same exact ready target. If observation proves the target
absent or incompatible, the Attempt fails and lifecycle reconciliation decides the
target outcome; data-plane Environment never creates a replacement.

Foundation does not supply `HostedProcessRunCapability`. When Environment actions
expose background shell, Harness owns logical process tracking within the Run and
kills/releases remaining process-local work before adapter close.

## Idle, Retention, and Retirement

When the last binding releases, the same transaction sets the target to `idle`,
records `idle_at`, and fixes `retire_after = idle_at + 10 minutes`. The target cannot
be rebound. The grace absorbs terminal-transition races, delayed provider
observations, and process cleanup; it is not reusable capacity.

While bindings are active, and through the idle grace when provider expiry could
precede cleanup, the lifecycle Worker monotonically calls Control `retain()` early
enough to cover its bounded scheduling margin. `retained_until` records only a
confirmed Provider observation. A Provider with no expiration returns a confirmed
no-op observation. An attached Provider that cannot guarantee retention reports that
capability at validation time; Foundation does not simulate the guarantee.

At `retire_after`:

- a managed target starts or reconciles `destroy`; only confirmed absence changes
  `destroying -> retired` and clears current state as allowed by the Provider codec;
- an attached target performs no destructive call and changes `idle -> retired`
  locally, preserving or omitting optional state according to its Provider; and
- an unknown managed destroy remains `destroying` with state and operation evidence
  retained for retry.

Retired rows are tombstones and audit/recovery evidence. Physical deletion is a
separate bounded retention policy and requires no remaining Run, binding, event, or
trace reference.

## Durable Operation Slot and Idempotency

`EnvironmentLifecycleOperation` is an in-memory shared Provider request value, not a
Foundation table. The current operation fields on `EnvironmentTarget` are sufficient
because only one lifecycle mutation may be in flight for one target.

The Worker protocol has three boundaries:

1. detached preparation selects an eligible target and loads the exact Provider lock
   without holding a database session;
2. a short transaction locks and revalidates the target, increments claim generation,
   and writes operation kind, stable operation ID, request digest, target generation,
   owner Worker generation, and lease; and
3. the Worker resolves credentials and calls Control outside every transaction, then
   commits confirmed evidence through compare-and-swap on target ID, target
   generation, operation ID, digest, claim generation, and owner.

`operation_phase="dispatched"` records that the request may have reached the
Provider. Timeout, cancellation, connection loss, or Worker death changes it to
`outcome_unknown` when possible but does not roll back target phase. A takeover after
lease expiry reuses the same operation ID and digest. Provider implementations use a
native idempotency key or deterministic operation correlation and must reconcile
before retrying mutation.

A confirmed result is applied in one fenced transaction. It validates and stores any
returned state and observation, advances `target_generation` when exact target
identity appears or is cleared, performs the corresponding target-phase transition,
records `last_observed_at`, safe error outcome, and any bounded retry
`next_operation_at`, and clears the current operation and claim fields. Domain
lifecycle events retain the committed transition; the target row does not keep an
unbounded call history.

A new operation ID is allocated only for a new logical request: initial create or
attach, a later retain deadline, or destroy. Reusing an ID with another digest or
target generation is rejected. A stale caller cannot commit after a newer claim even
if its monotonic retain call caused one bounded extra extension.

There is no `EnvironmentLifecycleOperation` history table. Durable target phase,
current operation evidence, Run bindings, domain events, and ordinary observability
provide the required operational audit. A future compliance-grade immutable provider
call ledger would be a separate cross-cutting audit feature, not part of target
lifecycle correctness.

## Lifecycle Worker

`EnvironmentLifecycleLoop` is a critical supervised Worker component with bounded
capacity independent from RunAttempt execution. It handles:

- pending create or attach;
- readiness observation and unknown-outcome reconciliation;
- active and idle-grace retention;
- due managed destruction and attached local retirement; and
- bounded orphan discovery for Provider-owned operation correlations.

In `on_demand`, `runner`, and `all` modes, only a process compatible with the exact
Provider lock can claim the target. Supervisor materialization treats a due lifecycle
candidate as work even when no RunAttempt is runnable. Worker drain stops new claims;
an in-flight operation can commit within its drain deadline, otherwise another
compatible Worker takes over after lease expiry.

Per-target Provider failures update only bounded safe target error and backoff. They
do not make the Worker globally unready. Unexpected lifecycle-loop exit is a critical
Worker failure. All SDK, Docker, filesystem, and subprocess work is async or kept off
the event loop and bounded by finite timeouts.

## Snapshots and Checkpoints

Foundation defines no `EnvironmentSnapshot` table. Provider images, E2B templates,
and provider-native snapshots referenced by an immutable revision are artifact
inputs, not per-Run lifecycle records.

If Foundation later captures mutable runtime filesystem or process state as a product
feature, the domain object is `EnvironmentCheckpoint`. It must define capture
consistency, ownership, retention, restore compatibility, encryption, billing, and
garbage collection before it can influence recovery. No current Run, Retry, Continue,
fork, or target cleanup behavior assumes such a checkpoint exists.

## Management API

The public `/api/v1` surface follows the shared
[Management API](16-management-api.md):

| Resource             | Route shape                                                                                                                                    |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Provider catalog     | `GET /environment-providers`, `GET /environment-providers/{provider_key}`                                                                      |
| Workspace selection  | `GET/PUT /workspaces/{workspace_id}/environment-providers/{provider_key}`                                                                      |
| Provider connections | `POST/GET /workspaces/{workspace_id}/environment-provider-connections`, `GET/PATCH /environment-provider-connections/{provider_connection_id}` |
| Connection test      | `POST /environment-provider-connections/{provider_connection_id}/test`                                                                         |
| Environments         | `POST/GET /workspaces/{workspace_id}/environments`, `GET/PATCH /environments/{environment_id}`                                                 |
| Revisions            | `POST/GET /environments/{environment_id}/revisions`, `GET /environment-revisions/{environment_revision_id}`                                    |

Connection test reauthorizes the exact connection, resolves fresh credentials,
constructs a Control, and calls its bounded non-mutating `probe_connection()`. It
creates, lists, attaches, retains, starts, or destroys no target and persists no
health session.

Revision publication performs only pure schema and capability validation. There is
no generic revision `test` route: testing a managed recipe by creating a target would
have real cost and lifecycle effects, while attached readiness is correctly observed
when a Run materializes its target.

`EnvironmentTarget` has no public list, create, mutation, retain, or destroy route.
An authorized Run detail can expose only a safe Environment summary: ownership,
phase, timestamps, and bounded safe error. It omits native target ID, complete state,
connection configuration, Secret references, operation receipts, claim/lease data,
and evidence about any other Run.

Provider catalog reads authorize `environment_provider.read`; Workspace selection
and connection lifecycle authorize `environment_provider.select`; Environment and
revision reads authorize `environment.read`; authoring and archive mutations
authorize `environment.manage`; connection test authorizes `environment.test`; Run
selection authorizes `environment.use` in addition to Agent invocation. Exact grants
remain in the IAM [registry](33-identity-and-access-management.md#stable-action-registry).

## Built-in Provider Mapping

| Provider     | Connection means                             | Managed recipe / attached reference             | Control behavior                                                         |
| ------------ | -------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------ |
| Direct Local | Allowed Host roots and operation policy      | Attached exact root only                        | Attach/observe; retain no-op; never destroy                              |
| Local Envd   | Allowed workspaces and envd runtime policy   | Attached exact workspace only                   | Attach/observe; retain no-op; never destroy workspace                    |
| Docker       | One local Engine and bootstrap profile       | OCI image recipe or exact existing container ID | Create/observe/retain no-op/destroy owned; attached never start/destroy  |
| E2B          | One account/API endpoint and API-key binding | Template recipe or exact existing sandbox ID    | Create/observe/native retain/destroy owned; attached observe/retain only |

Local Docker is accepted only in all-in-one or otherwise explicit single-host Worker
profiles where Control and data-plane placement share the same Engine. Multi-Worker
remote placement for a Unix-socket Docker target is outside this contract and is not
approximated with random Worker scheduling.

## Failure Semantics

| Failure                                              | Foundation outcome                                                         |
| ---------------------------------------------------- | -------------------------------------------------------------------------- |
| Invalid Provider, connection, options, or capability | Reject connection, revision, inline selection, or Run before effects       |
| Disabled selection or connection                     | Block new work; preserve exact-lock reconciliation and managed cleanup     |
| Missing lifecycle Secret                             | Keep target intent/state, record safe error, and retry; never fake destroy |
| Managed create response lost                         | Keep provisioning and the same operation ID; reconcile before retry        |
| Attached target absent, stopped, or incompatible     | Fail the owning Run without create, start, resume, or replacement          |
| Readiness fails after managed identity is known      | Persist state, fail or back off the Run, and retain cleanup responsibility |
| Provider target disappears during a Run              | Fail Attempt; Environment does not create a replacement                    |
| Retain outcome unknown                               | Preserve prior observation and retry the same logical operation            |
| Managed destroy outcome unknown                      | Remain destroying with state and operation evidence                        |
| Late operation result                                | Generation compare-and-swap rejects stale commit                           |
| Environment close fails                              | Report local cleanup failure; lifecycle remains under Control policy       |

## Verification

Implementation tests cover at least:

- pure revision/inline validation with no target creation;
- a distinct managed target for two Runs using the same revision;
- same-Run Attempt replacement reusing one target and fresh Environment objects;
- Retry, Continue, fork, `dedicated`, `shared_root`, `none`, and inline-child target
  behavior;
- parent-first completion and exactly-once binding release/count decrement;
- zero-count non-reusable idle, fixed ten-minute grace, managed destroy, and attached
  local retirement;
- create and destroy responses lost before commit, lease takeover, repeated operation
  IDs, digest mismatch, and stale fencing;
- state persisted before readiness failure and late create success after Run failure;
- Direct Local and Local Envd reaching `ready` and entering data-plane use with
  `provider_state=None` and no Harness state entry;
- connection disable/Secret rotation/Secret loss without abandoning managed cleanup;
- attached target protection against create, start, resume, replacement, stop, and
  destroy;
- provider option unknown fields and unsupported common fields failing closed;
- local Docker single-host placement enforcement; and
- target/private-state redaction from collection, Run, event, trace, and model-facing
  projections.

## Security and Compatibility

Provider publication, Workspace selection, Provider connection management,
Environment authoring, Environment use, Secret resolution, lifecycle execution, and
Agent tool access are separate authorities. Model content cannot select Providers,
connections, Secrets, targets, operation IDs, or runtime collaborators.

Connection configuration, target references, native IDs, provider state, operation
receipts, and safe Provider errors are protected data. Secret values and live clients
remain process-local. Runtime Secret delivery is distinct from Provider lifecycle
credentials and follows the exact accepted Run requirements.

This is a direct pre-release contract. Foundation supports only managed targets it
creates and owns and explicitly attached targets it never destroys. It carries no
attach-only compatibility adapter, deployment-global target deduplication,
Environment use-lease table, lifecycle-operation table, snapshot table, or
lifecycle-capable Environment shim.

## Invariants

01. A Provider connection selects an account or endpoint, never a sandbox/container.
02. An Environment revision is immutable desired configuration and has no target ID.
03. Provider target I/O begins only after a Run creates a target intent and binding.
04. Every independent root/dedicated Run receives a distinct managed target.
05. Replacement Attempts reuse; Retry, Continue, fork, and dedicated Runs do not.
06. `shared_root` is the only async Run policy that adds another binding to the same
    target.
07. `RunEnvironmentBinding` is the sole active-use record; no use-lease table exists.
08. Target phase advances only from confirmed Provider evidence.
09. One target stores at most one current lifecycle operation slot.
10. Managed targets are destroyed only by Control after active count reaches zero and
    idle grace expires.
11. Attached targets are never destroyed, replaced, started, resumed, paused, or
    stopped by Foundation.
12. Idle targets are non-reusable and retire after a fixed ten-minute cleanup grace.
13. Environment construction, entry, and close never mutate backing-target lifecycle.
14. Foundation frozen target configuration and optional provider state override any
    portable Harness copy.
15. Dependencies are baked into immutable artifacts; Run admission is not a package
    build pipeline.
16. No Environment snapshot semantics exist without a separately specified
    `EnvironmentCheckpoint` domain.
