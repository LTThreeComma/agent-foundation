# Lifecycle Control and Runtime Access

## Design Position

The shared package separates provider-side target lifecycle I/O from data-plane
access:

- `EnvironmentControl` performs lifecycle calls explicitly authorized by Foundation.
- `EnvironmentState` optionally carries a portable exact-target reference between
  processes when a validated target specification is insufficient by itself.
- `Environment` enters one already-ready exact target and serves file, shell,
  process, output, and port operations.

Within this repository, only Foundation's trusted lifecycle path constructs and
holds `EnvironmentControl`. Harness, Agent UI, Agent code, and data-plane
`Environment` instances never receive it. The package defines and implements the
provider-facing client so every Provider exposes the same external-I/O behavior;
Foundation remains the sole owner of durable lifecycle policy and state.

## Portable Target State

```python
class EnvironmentState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: str
    state_version: str
    state: JsonValue
```

State can contain an exact Docker container ID or E2B sandbox ID, immutable artifact
identity, configuration fingerprint, and bounded non-secret recovery correlation.
It is a semantic soft reference, not a credential, lease, ownership claim, live
client, or proof that the target still exists.

Direct Local and Local Envd produce no `EnvironmentState`: their attached workspace
is selected completely by the validated target specification, and their
process-local clients or daemon generations do not survive the adapter. Optional
state is not a placeholder for configuration that already identifies the target.

State contains no:

- API key, bearer URL, bootstrap secret, SDK client, transport, callback, task, or
  lock;
- process handle, pipe, private temporary path, or unresolved ambient endpoint;
- Harness mount name, permission ceiling, working directory, Agent identity, or
  Thread relationship; or
- Foundation row ID, lifecycle phase, retention policy, operation claim, or fencing
  token.

When state is present, Providers validate its exact version, payload, provider key,
connection-option compatibility, target-specification fingerprint, and immutable
target evidence before use. Missing required state, unexpected state for a stateless
target specification, and unsupported or incompatible state fail; state is never
silently adopted or retargeted.

## Lifecycle Operation Context

Every lifecycle call that establishes state or can change Provider state receives an
immutable operation context:

```python
@dataclass(frozen=True, slots=True)
class EnvironmentLifecycleOperation:
    operation_id: str
    request_digest: str
    target_generation: int
```

This is an in-memory request value, not a prescribed database entity. Foundation
owns the durable operation slot and claim/fencing protocol. Repeating a call with
the same operation ID and digest means reconcile or retry the same logical
operation; changing the digest under the same ID is an error.

Providers use native idempotency keys when available. Otherwise they attach bounded
operation correlation to provider-native metadata or names and look it up before
retrying. A Provider must not create two managed targets for one logical create
operation merely because the first response was lost.

Target observations use one provider-neutral shape:

```python
type EnvironmentCondition = Literal[
    "pending",
    "ready",
    "stopped",
    "absent",
    "failed",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class EnvironmentObservation:
    condition: EnvironmentCondition
    state: EnvironmentState | None
    observed_at: datetime
    retained_until: datetime | None
    safe_code: str | None
    safe_message: str | None
```

Diagnostics are bounded and safe for Foundation logs; they never carry native
exception objects or secrets. `condition` alone reports whether the target is
pending, ready, stopped, absent, failed, or unknown. A stateful Provider normally
preserves exact state even when the target is stopped, failed, or absent; a stateless
Provider returns `state=None` for every condition. Therefore `state=None` alone never
means absence, and `condition="ready", state=None` is the normal Direct Local and
Local Envd observation.

## Control Contract

The conceptual Control contract is:

```python
class EnvironmentControl(ABC):
    async def probe_connection(self) -> EnvironmentProviderObservation: ...

    async def create(
        self,
        *,
        target_spec: ValidatedManagedProviderTargetSpec,
        operation: EnvironmentLifecycleOperation,
    ) -> EnvironmentLifecycleResult: ...

    async def attach(
        self,
        *,
        target_spec: ValidatedAttachedProviderTargetSpec,
        operation: EnvironmentLifecycleOperation,
    ) -> EnvironmentLifecycleResult: ...

    async def observe(
        self,
        *,
        target_spec: ValidatedProviderTargetSpec,
        state: EnvironmentState | None,
    ) -> EnvironmentObservation: ...

    async def ensure_ready(
        self,
        *,
        target_spec: ValidatedProviderTargetSpec,
        state: EnvironmentState | None,
        timeout_seconds: float,
    ) -> EnvironmentObservation: ...

    async def retain(
        self,
        *,
        target_spec: ValidatedProviderTargetSpec,
        state: EnvironmentState | None,
        retain_until: datetime,
        operation: EnvironmentLifecycleOperation,
    ) -> EnvironmentLifecycleResult: ...

    async def destroy(
        self,
        *,
        target_spec: ValidatedManagedProviderTargetSpec,
        state: EnvironmentState,
        operation: EnvironmentLifecycleOperation,
    ) -> EnvironmentLifecycleResult: ...

    async def close(self) -> None: ...
```

Exact language APIs may group request values, but the semantics remain fixed.
`target_spec` is a Provider-validated call value compiled from Foundation-owned
configuration. It is not an `EnvironmentRevision`, an inline Environment resource,
or a database record.

### Connection probe

`probe_connection()` performs one bounded non-mutating account/endpoint and
credential check. It reports safe Provider identity and available capability facts
without listing, selecting, creating, attaching, retaining, starting, or destroying
a target. It is advisory current evidence rather than a durable health resource.

### Create

`create()` is valid only for a managed target specification. It creates or
reconciles exactly one target from the immutable recipe, records operation
correlation where supported, and returns the resulting portable state as soon as
exact identity is known. A later readiness failure does not erase known state.

Creation does not install arbitrary dependency lists supplied at Run time. Immutable
dependencies belong in the selected image, E2B template, snapshot, or equivalent
Provider artifact. A bounded declared initialization action can start envd or verify
the runtime contract, but it is part of the revision fingerprint and is not an
unrestricted package-build service.

### Attach

`attach()` is valid only for an attached target specification. It validates and
normalizes the configured exact external reference, observes that target, and
returns optional state without creating, starting, resuming, replacing, stopping,
or destroying anything. Direct Local and Local Envd return no state. Repeated attach
is idempotent.

An inaccessible target is not absent. Attach fails rather than substituting a newly
created target.

### Observe and readiness

`observe()` returns the current provider-neutral condition of the exact target:
`pending`, `ready`, `stopped`, `absent`, `failed`, or `unknown`, plus bounded native
diagnostics and refreshed optional state when observation safely supplies compatible
non-secret evidence.

`ensure_ready()` performs bounded waiting and readiness probes. It does not select a
replacement or acquire ownership. For attached targets it never starts or resumes
the target. For managed targets, any provider startup needed to reach the initial
ready condition is part of the already-issued create operation, not an implicit new
lifecycle decision in data-plane entry.

### Retain

`retain()` monotonically requests that the exact target remain available until at
least `retain_until`. Providers map it to a native timeout extension, keepalive API,
or a documented confirmed no-op for targets without provider expiration. They never
shorten native lifetime.

Retain is permitted for both managed and attached targets when the Provider declares
support. For attached targets it does not transfer ownership and must not change any
other lifecycle property. Foundation must not promise a lifetime longer than the
provider confirms.

### Destroy

`destroy()` is valid only for a managed target specification and removes only the
exact target identified by compatible state. Prior authoritative absence is success.
Unknown identity or incompatible evidence fails without mutation.

Destroy also removes Provider-owned bootstrap material whose ownership is encoded in
the managed target specification or state. It never removes external bind sources,
user files, shared workspaces, or volumes that the Provider did not create for that
target. Attached targets can never reach this method through a valid typed request.

### Close

Control close releases process-local SDK clients, HTTP sessions, engine clients,
watchers, and credentials. It never changes the target. Foundation invokes it in
unconditional cleanup after every lifecycle scope.

### Persistence boundary

Control methods communicate only with the external Provider boundary. They never
open a Foundation database session, read or write an `EnvironmentTarget`, publish a
domain event, choose a lifecycle phase, or schedule a retry. Foundation records an
operation intent in a short transaction, closes that transaction, awaits Control,
and applies a confirmed result or typed unknown outcome in another fenced short
transaction.

## Results and Unknown Outcomes

```python
@dataclass(frozen=True, slots=True)
class EnvironmentLifecycleResult:
    observation: EnvironmentObservation
    operation_id: str
    provider_receipt: JsonValue | None
```

A successful result is confirmed provider evidence. Managed create returns an
observation with exact state. Attach returns exact state only when the Provider needs
one; Direct Local and Local Envd return `condition="ready", state=None`. A successful
destroy reports confirmed absence through `condition="absent"`. Provider receipts
are bounded, non-secret reconciliation evidence, not credentials or a durable audit
log.

Timeout, cancellation, transport loss, or worker death after dispatch can leave an
unknown outcome. A typed unknown-outcome error retains the operation ID and last
known state. Foundation keeps its durable operation slot and retries the same
logical operation; the Provider reconciles through native idempotency or operation
correlation. Only Foundation changes its target lifecycle phase, and only after a
confirmed observation.

This contract does not promise distributed exactly-once delivery. It promises that
one logical operation is identifiable, retryable, and never treated as rolled back
merely because the caller did not receive a response.

## Data-plane Environment Contract

```python
class Environment(ABC):
    @property
    def provider_key(self) -> str: ...

    async def enter(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str] = {},
    ) -> None: ...

    def dump_state(self) -> EnvironmentState | None: ...

    async def close(self) -> None: ...
```

Construction binds validated Provider connection options, one validated target
specification, optional state, credentials, and runtime collaborators without
external I/O. The Provider validates that the target specification plus state selects
one exact target. Every independent Run receives fresh Environment instances. An
instance is process-local, single-use, and never stored durably.

`enter()` validates and connects to the exact already-ready target, establishes
fresh operation clients, validates EIP or direct runtime descriptors, and returns
only when declared operations are usable. It never creates, replaces, starts,
resumes, retains, stops, or destroys the backing target. Absent, stopped,
incompatible, inaccessible, or unknown targets fail with typed errors.

`dump_state()` is an infallible synchronous read of a detached copy of the fixed
`EnvironmentState | None` supplied at construction. It performs no external I/O and
cannot publish lifecycle changes. Direct Local and Local Envd return `None`. Control
is the only shared API that can produce changed state after lifecycle mutation.

`close()` fences new operations and releases process-local clients, sessions,
streams, handles, and entry correlation. Context-manager exit has exactly these
non-destructive semantics. A closed Environment cannot be re-entered.

## Provider-neutral Operations

The entered Environment exposes typed operation facets:

| Family   | Operations                                                                                             |
| -------- | ------------------------------------------------------------------------------------------------------ |
| Files    | stat, bounded read/list/glob/search, streaming read/write, patch, create directory, move, copy, remove |
| Commands | bounded foreground execution, optional process start/control, working-directory validation             |
| Output   | independent stdout/stderr cursors, retained-range disclosure, truncation, bounded pages                |
| Ports    | provider-local port observation and bounded wait                                                       |
| State    | synchronous fixed optional portable state dump                                                         |

An immutable entered descriptor declares supported operation families and safe
Provider identity. Harness intersects that descriptor with its mount access ceiling.
The descriptor carries no credential, state payload, native handle, or lifecycle
authority.

## Concurrency and Foundation Authority

- Controls and Environments are fresh per bounded operation scope.
- Independent Runs never share process-local instances, even when Foundation policy maps
  them to the same backing target.
- Inline child execution borrows the already-entered parent Harness facade.
- Foundation persists authoritative target state before admitting data-plane access when
  the Provider produces state; stateless attached targets remain authoritative
  through their validated target specification and target record.
- A portable Harness copy is recovery context only; it cannot override an
  authoritative Foundation target record.
- Foundation owns lifecycle phases, active-use accounting, retention deadlines, operation
  claims, fencing, and orphan repair. None is encoded into this shared package's
  state envelope.

## Failure Semantics

| Condition                                   | Required behavior                                                           |
| ------------------------------------------- | --------------------------------------------------------------------------- |
| Invalid call options, target spec, or state | Fail before external effects                                                |
| Managed create response is lost             | Preserve operation identity and reconcile; do not issue a new target        |
| Attached target is absent or stopped        | Fail without create, start, resume, or replacement                          |
| Exact managed target is absent before use   | Report absence to Foundation lifecycle policy; Environment does not replace |
| Readiness deadline expires                  | Preserve known state and report typed timeout                               |
| Retain is unsupported                       | Fail capability validation; do not simulate a guarantee                     |
| Destroy outcome is unknown                  | Preserve state and operation slot for reconciliation                        |
| Environment close fails                     | Report local cleanup failure; never escalate to target destruction          |

## Invariants

01. Required managed state exists before Environment construction and data-plane
    entry; stateless attached Providers use `None`.
02. Only Control performs provider target lifecycle I/O.
03. Managed create and destroy require a Foundation-authorized managed target
    specification.
04. Attached targets may be observed and retained but never destroyed or replaced.
05. Lifecycle phase changes follow confirmed observation, not request dispatch.
06. Repeated operation IDs identify the same digest and target generation.
07. Environment entry and close are permanently non-destructive.
08. Present state contains no credentials, live clients, Foundation persistence
    model, or
    ownership claim.
09. Harness owns multi-mount routing but no Provider target lifecycle.
10. No separate operation table, Resource wrapper, runtime attachment, or lifecycle
    Provider is prescribed by this package.
