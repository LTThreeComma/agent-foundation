# Built-in Environment Providers

## Design Position

`a13n-environment-provider` ships four Providers:

| Provider key        | Managed | Attached | Retain mapping              | Data plane |
| ------------------- | ------- | -------- | --------------------------- | ---------- |
| `a13n.direct-local` | No      | Yes      | Confirmed no-op             | Direct OS  |
| `a13n.local-envd`   | No      | Yes      | Confirmed no-op             | EIP        |
| `a13n.docker`       | Yes     | Yes      | Confirmed no-op             | EIP        |
| `a13n.e2b`          | Yes     | Yes      | Native timeout or keepalive | EIP        |

Every built-in implements one inert `EnvironmentProvider`, a fresh
connection-bound `EnvironmentControl`, and a fresh exact-target `Environment`.
Only Foundation's trusted lifecycle path holds the Control. Local Envd, Docker, and
E2B use `agent-envd` and EIP for Agent operations; they do not substitute vendor
exec, file, log, or copy APIs for the common data plane.

## Foundation Input Mapping

Foundation owns the normalized managed and attached Environment schemas, Provider
connections, revisions, inline configuration, Secret references, and access policy
defined by [Environment Management](../foundation-service/29-environment-management.md).
The shared Provider package does not reproduce those schemas. Foundation compiles an
accepted selection into Provider-owned connection options and one effective target
specification before calling the Provider.

A Provider validates the complete effective call input and maps its supported
artifact, resource, runtime, network, initialization, access, and Provider-specific
options to the native API. Unsupported values fail explicitly; they are not ignored.
Provider-owned options reject unknown fields and cannot weaken ownership, Secret,
network, access, operation-correlation, or cleanup policy fixed by Foundation.

Dependencies are part of an immutable artifact. Docker uses an OCI image; E2B uses
an E2B template or snapshot-capable template reference. Direct Local and Local Envd
use the already prepared workspace. The common contract intentionally has no
generic `apt_packages`, `pip_packages`, `npm_packages`, arbitrary Dockerfile, or
unbounded setup-script list. Foundation does not build provider artifacts as part of
Run admission.

Runtime secret values are not Provider connection credentials and are not embedded
in revisions. Foundation resolves explicitly authorized runtime Secret references
just before create or entry and passes them through the Provider's protected
bootstrap path. Neither revision configuration nor `EnvironmentState` contains
those values.

## Direct Local

### Provider call inputs

Connection-option schema version `1` defines the embedding boundary:

```python
class DirectLocalConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_roots: tuple[Path, ...]
    shell_profiles: tuple[DirectLocalShellProfile, ...] = ()
    allowed_executables: frozenset[Path] = frozenset()
    allowed_environment_keys: frozenset[str] = frozenset()
    allowed_ports: frozenset[int] = frozenset()
```

An attached target specification names one absolute existing root beneath an allowed
root, its read-only policy, and bounded operation limits. It cannot request managed
mode. Paths are normalized after user expansion. A read-only Environment cannot
expose a shell profile or executable that could mutate the root through the
embedding OS account.

### Lifecycle and data plane

Control attach validates the configured exact root and returns
`condition="ready", state=None`. Observe reports readiness or unavailability with no
independent state. Retain is a confirmed no-op because the Provider has no expiration
authority. Create and destroy are unsupported.

Environment construction requires `state=None`. Entry revalidates the exact root and
constructs fresh local file/process/output facets. `dump_state()` returns `None`.
Close terminates only processes and streams owned by that Environment instance. The
Provider never creates, locks, backs up, or deletes the root.

Direct Local is an explicit trust choice. It offers operation policy over a selected
root but no native sandbox, account isolation, network isolation, or protection from
a hostile same-account process.

## Local Envd

### Provider call inputs

Connection-option schema version `1` defines allowed workspace roots, the exact
trusted envd executable, required native isolation mode, and shell profiles. An
attached target specification selects one existing workspace beneath an allowed
root, read-only and network policy, and bounded EIP operation limits.

The Provider does not expose arbitrary envd JSON, transport selection, native
runtime paths, payload identities, or arbitrary environment variables. A separate
embedding convenience resolver may locate an envd executable, but pure Provider
validation and construction never search `PATH`, load `.env`, download a binary, or
probe the operating system.

### Lifecycle and data plane

The durable attached target is the workspace. Control attach and observe
validate that workspace and return `state=None`; retain is a confirmed no-op; create
and destroy are unsupported.

Environment construction requires `state=None`. Each fresh Environment launches one
process-local envd daemon generation during entry, performs the production isolation
probe, establishes an authenticated EIP session, and validates descriptor
compatibility and readiness. `dump_state()` returns `None`. The daemon PID, carrier,
credential, private runtime directory, and EIP session are process-local and never
enter state.

Close fences operations, closes EIP, terminates the complete owned daemon process
tree under bounded grace, closes pipes, and removes private runtime data. It never
deletes or mutates the attached workspace. Filesystem continuity comes from the
workspace, not daemon identity.

## Docker

### Provider connection options

Connection-option schema version `1` selects one Foundation-supplied local Docker Engine boundary
and protected bootstrap store. The serialized configuration can choose an allowed
engine profile but contains no socket path, remote daemon credential, Docker client,
container ID, or bootstrap secret.

The runtime must prove that the Engine is local and that the Provider process can
reach a loopback-published EIP port. An unprovable or remote topology fails before
image resolution, bootstrap allocation, or container mutation. Foundation supports
this Provider only where lifecycle Control and Run data-plane access share the same
single-host/all-in-one placement; the Provider does not invent a distributed Docker
socket placement protocol.

### Managed target options

The common `artifact` is an OCI image reference. The Provider resolves and records
its immutable image ID during create. Common resources map to Docker CPU, memory,
disk/filesystem, and PID limits where supported. Common network policy maps to an
allowlisted Docker network profile and loopback-only EIP publication.

Provider options schema version `1` has this conceptual shape:

```python
class DockerProviderOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pull_policy: Literal["if_missing", "always", "never"] = "if_missing"
    mounts: tuple[DockerMountConfiguration, ...] = ()
    platform: str | None = None
    stop_grace_seconds: int = 10
    labels: Mapping[str, str] = {}
```

Mounts distinguish Provider-owned writable layers from external bind sources and
pre-existing named volumes. External sources are validated but never become cleanup
targets. User labels are namespaced and cannot replace Provider identity,
configuration fingerprint, operation correlation, or ownership labels.

Schema version `1` rejects arbitrary daemon endpoints, raw Docker API payloads,
privileged mode, host PID/IPC namespaces, devices, added Linux capabilities, public
port publication, arbitrary command/entrypoint replacement, and arbitrary mount
syntax. A future Provider option version can add a reviewed portable field without
turning this payload into an escape hatch.

An attached target specification supplies an exact container ID and expected immutable
runtime contract. It can narrow data-plane access but cannot supply managed create
fields or claim ownership.

### State and lifecycle

Docker state version `1` contains:

```python
class DockerEnvironmentStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    container_id: str
    image_id: str
    configuration_fingerprint: str
    bootstrap_correlation: str | None
    create_operation_id: str | None
```

Managed Control create:

1. validates local topology and external mount sources;
2. applies pull policy and resolves the immutable image ID;
3. creates or recovers protected envd bootstrap material by operation ID;
4. looks up a prior exact labeled result before dispatching container creation;
5. creates and starts one completely configured labeled container;
6. returns state as soon as exact identity is known; and
7. waits for loopback EIP readiness through `ensure_ready()`.

Repeated create with the same operation ID reconciles the labeled container instead
of creating another. A compatible existing container under another operation is a
conflict, not an adoption candidate.

Attached Control attach inspects and validates the exact running container. It never
starts an exited container. Observe maps Docker state to the common conditions.
Retain is a confirmed no-op because Docker has no native expiration. Destroy is
available only for managed state: it validates exact ID, image, labels, mounts,
bootstrap evidence, and ownership before stopping and removing the container and
owned bootstrap allocation.

Environment entry only validates the already-running exact container, resolves its
authoritative loopback EIP route, opens a fresh authenticated EIP session, and checks
descriptor readiness. Close releases EIP and SDK resources without stopping the
container.

The Provider uses no Docker archive, copy, exec, or logs API for Agent operations.
Credentials never enter labels, image metadata, state, endpoint URLs, logs, traces,
or model-visible values.

## E2B

### Provider connection options

Connection-option schema version `1` selects an E2B control-plane profile, optional region
or domain settings supported by the SDK, and workspace-owned credential bindings.
It contains no API key, sandbox ID, live SDK client, or bearer endpoint. The same
connection can create and operate many sandboxes in the authorized account.

### Managed target options

The common `artifact` is an immutable E2B template reference. CPU, memory, and other
resource fields are accepted only when the selected E2B API and account support
them. Network, runtime, and initialization fields are validated against the template
contract.

Provider options schema version `1` contains only reviewed E2B-specific creation
fields, conceptually:

```python
class E2BProviderOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout_seconds: int
    metadata: Mapping[str, str] = {}
    auto_pause: bool = False
```

The exact accepted fields track a pinned E2B SDK/API version. Unsupported account
features fail validation or create with a typed capability error. The options do not
accept raw SDK kwargs. Runtime dependencies and envd belong in the template; changing
them creates a new immutable Environment revision.

An attached target specification supplies an exact E2B sandbox ID and expected template and
runtime compatibility evidence. It does not transfer ownership.

### State and lifecycle

E2B state version `1` contains the exact sandbox ID, immutable template identity when
available, configuration fingerprint, bootstrap correlation, and create operation
correlation. It contains no API key, bearer endpoint, live sandbox object, EIP
session, or daemon generation.

Managed Control create maps the operation ID supplied by Foundation to native
idempotency where available and otherwise to bounded sandbox metadata that can be
queried before retry. It creates one sandbox, records exact state, establishes
protected envd bootstrap, and observes readiness. Retain maps to the native timeout
extension or keepalive primitive and returns the provider-confirmed expiry. Destroy
terminates only a compatible Foundation-owned sandbox and cleans Provider-owned
bootstrap material.

Attached Control attach reconnects to and validates the exact sandbox. Observe and
retain are allowed; create, replacement, stop, and destroy are not. Expired,
inaccessible, rate-limited, or ambiguous evidence never triggers speculative create.

Environment entry connects only to the exact ready sandbox, authenticates a fresh
EIP session, and validates the descriptor. Close releases E2B and EIP clients without
terminating the sandbox.

## Harness Semantics

Harness receives already constructed Environments. It never receives a Provider or
Control, resolves Provider credentials, or decides target retention or destruction.

A singular Environment becomes mount `workspace`; named mounts can combine
Providers. Harness supplies Run-local access ceilings and working directories,
enters adapters atomically, routes operations, snapshots each non-`None` fixed state
into `HarnessState.environment_states`, and closes adapters non-destructively. Direct
Local and Local Envd contribute no state entry. Portable state supports cross-process
resume but does not override a Foundation-managed target record.

Async child Runs receive fresh Environment objects. Under Foundation policy they may point
to the same target as the root Run; inline children borrow the current entered
Harness facade.

## Public Surface

The package root exports:

- `EnvironmentProvider`, `EnvironmentControl`, `Environment`, and
  `EnvironmentState`;
- Provider-owned call-option codecs, capability, lifecycle request, observation,
  result, and typed error models;
- provider-neutral data-plane operation contracts;
- the trusted Provider catalog and built-in Provider constructors; and
- explicit Host convenience resolvers and bounded Provider discovery used for
  authorized orphan repair.

It does not export Docker or E2B SDK objects, EIP native sessions, Harness mount
types, Host persistence models, runtime attachment types, or unscoped native-client
escape hatches.

## Invariants

01. Built-in validation and construction are inert.
02. Direct Local and Local Envd accept attached targets only.
03. Direct Local and Local Envd produce no `EnvironmentState` and require
    `state=None`.
04. Docker and E2B keep create/retain/destroy in Control and Agent operations in EIP.
05. Managed state contains exact target identity and operation compatibility evidence.
06. Attached state never grants ownership.
07. Confirmed absence and unknown evidence are distinct.
08. Immutable artifacts carry dependencies; Run admission never performs package
    builds.
09. Environment close never removes a container or terminates a sandbox.
10. Destroy validates and removes only an exact Provider-owned target.
11. External workspaces, bind sources, and named volumes are never Provider-owned
    cleanup targets.
