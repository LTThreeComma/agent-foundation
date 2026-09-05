# Provider Contracts and Catalog

## Design Position

An Environment Provider is selected by a stable namespaced key. The same inert
`EnvironmentProvider` plugin validates Provider-owned call options and constructs
either a Foundation lifecycle `EnvironmentControl` or an exact-target `Environment`
data-plane adapter.

There is no second lifecycle Provider abstraction. `EnvironmentControl` is a
short-lived external-I/O client created by the Provider and held by Foundation's
trusted lifecycle path. It is not another plugin, a durable domain object, or a
capability exposed to Harness, Agent UI, or Agent code.

## Provider Call Inputs

Foundation owns `EnvironmentProviderConnection`, `Environment`,
`EnvironmentRevision`, inline Environment configuration, and their IDs and
persistence. None is a shared-package domain model. Before invoking a Provider,
Foundation resolves those records into two Provider-owned input fragments:

- connection options needed to reach one account, endpoint, region, or local engine;
  and
- an effective target specification for one managed create or attached target.

These are versioned immutable call values, not resources. They contain no Foundation
record ID, name, Workspace, revision metadata, lifecycle phase, retention policy,
operation claim, or Secret reference. Foundation owns the provider-neutral
configuration schema and compiles it into the effective target specification. The
Provider owns only the exact option schemas and validation needed to map that input
to its native API.

Secrets are resolved separately into fresh process-local credentials and never
serialized in either input fragment. A Provider may represent the fragments with
specific immutable models after validation; the shared package prescribes no
Provider connection or Environment revision envelope.

Rules:

1. `provider_key` is a bounded lowercase namespaced key such as `a13n.docker`.
2. Provider-owned connection-option and target-option schema versions evolve
   independently.
3. Option payloads are canonical JSON and reject unknown fields through
   Provider-owned models.
4. Connection options contain no API key, access token, live client, session, exact
   target ID, or Foundation `provider_connection_id`.
5. A managed target specification contains desired create behavior, never the
   current target ID or an ownership assertion supplied by Provider data.
6. An attached target specification contains an exact external target reference but
   never transfers ownership.
7. Validation is deterministic and performs no filesystem, daemon, subprocess,
   network, or Provider API I/O.

## Provider Contract

The conceptual contract is:

```python
class EnvironmentProvider(ABC):
    @property
    def key(self) -> str: ...

    @property
    def capabilities(self) -> EnvironmentProviderCapabilities: ...

    def validate_connection_options(
        self,
        *,
        schema_version: str,
        value: JsonValue,
    ) -> ValidatedProviderConnectionOptions: ...

    def validate_target_spec(
        self,
        *,
        connection_options: ValidatedProviderConnectionOptions,
        specification: ProviderTargetSpec,
    ) -> ValidatedProviderTargetSpec: ...

    def create_control(
        self,
        *,
        connection_options: ValidatedProviderConnectionOptions,
        credentials: EnvironmentProviderCredentials,
        runtime: EnvironmentProviderRuntime,
    ) -> EnvironmentControl: ...

    def create_environment(
        self,
        *,
        connection_options: ValidatedProviderConnectionOptions,
        target_spec: ValidatedProviderTargetSpec,
        state: EnvironmentState | None,
        credentials: EnvironmentProviderCredentials,
        runtime: EnvironmentProviderRuntime,
    ) -> Environment: ...
```

`ProviderTargetSpec` is the effective managed-or-attached call input compiled by
Foundation from one accepted revision or inline Environment. Its exact transport
shape is intentionally not a Foundation persistence schema. A Provider may return a
more specific immutable validated model, but cannot reinterpret ownership, access,
Secret, or cleanup policy.

`EnvironmentProviderCapabilities` is immutable metadata. It declares whether the
Provider accepts managed and attached targets, which common artifact and resource
forms it supports, and whether it can retain an existing target. It does not grant
authorization or report the condition of a particular target.

These semantics are fixed:

- The Provider is inert after construction and holds no connection credentials.
- Both validation methods are pure, reject unknown Provider-owned fields, and return
  immutable values.
- `create_control()` and `create_environment()` are synchronous pure constructors.
  They perform no external I/O and mutate no target.
- A Control binds validated connection options, fresh credentials, and fresh runtime
  collaborators. Lifecycle methods receive one validated target specification and
  optional state explicitly. Only Foundation's trusted lifecycle path constructs and
  invokes it.
- An Environment binds one validated target specification and optional
  `EnvironmentState`. The Provider must prove that those values select one exact
  target. Direct Local and Local Envd require `None`; Docker and E2B require state
  after managed create or attached-target validation identifies the target. An
  Environment cannot discover, create, replace, start, resume, stop, or destroy a
  target.
- Every construction returns a fresh process-local object. Controls and Environments
  are never serialized or stored in durable records.
- The Provider and its products do not own Foundation resources, database access,
  retention policy, durable lifecycle state, Run association, or tenant
  authorization.

`create_control()` therefore means dependency binding only. For E2B it constructs an
SDK control client around resolved account options and the current API key; for
Docker it binds one Docker engine client; for Direct Local it returns a non-owning
Control that can only observe the attached local target. Provider API calls, Docker
inspection, daemon startup, or target mutation begin only when Foundation awaits an
explicit Control method.

## Catalog and Discovery

The package owns metadata-only discovery values, validated process-local
registrations, and one immutable selected catalog:

```python
@dataclass(frozen=True, slots=True)
class EnvironmentProviderReference:
    provider_key: str
    import_target: str
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class EnvironmentProviderRegistration:
    provider_key: str
    class_module: str
    class_qualname: str
    import_target: str | None
    distribution_name: str | None
    distribution_version: str | None
    builtin: bool


class EnvironmentProviderCatalog(Mapping[str, EnvironmentProvider]):
    @property
    def registrations(self) -> tuple[EnvironmentProviderRegistration, ...]: ...

    def require(self, provider_key: str) -> EnvironmentProvider: ...


def discover_environment_provider_references() -> tuple[EnvironmentProviderReference, ...]: ...


def build_environment_provider_catalog(
    *,
    builtin_keys: Iterable[str] = (),
    extension_keys: Iterable[str] = (),
    explicit_providers: Iterable[EnvironmentProvider] = (),
) -> EnvironmentProviderCatalog: ...
```

Discovery reads installed entry-point metadata without importing targets. Catalog
construction imports only explicitly selected extensions, checks that entry-point
name and `provider.key` match, rejects collisions, and returns no partial catalog on
failure. An empty selection performs no entry-point scan.

The built-in keys are:

| Key                 | Target model                                                    |
| ------------------- | --------------------------------------------------------------- |
| `a13n.direct-local` | Attached embedding operating system only                        |
| `a13n.local-envd`   | Attached Host-selected workspace served by a process-local envd |
| `a13n.docker`       | Managed or attached Docker container running envd               |
| `a13n.e2b`          | Managed or attached E2B sandbox running envd                    |

Third-party Providers register under
`a13n_environment_provider.providers`. One selected entry point loads one concrete
`EnvironmentProvider` class with safe no-argument construction. Preconstructed
objects are not valid entry-point targets. Explicit Provider objects remain
available for embedded applications, tests, and source development.

The catalog exposes no mutation, late loading, process-global registry, arbitrary
serialized import target, ambient activation, or module replacement. Changed
Provider code requires a fresh Host process.

## Selection and Authorization

Catalog presence is not authorization. Foundation:

1. selects and authorizes its own Provider connection resource;
2. resolves current credentials and Provider-owned connection options;
3. compiles one accepted revision or inline Environment into an effective target
   specification and validates the Provider-owned option fragments;
4. constructs a Control for Foundation-only lifecycle work or an Environment for one
   exact ready target; and
5. closes the process-local object after the bounded operation scope.

Model content, portable Harness state, an installed package, or an arbitrary
entry-point key cannot choose a Foundation connection or supply credentials.
Optional state cannot retarget the accepted specification: provider key,
configuration fingerprint, immutable artifact identity, and native target metadata
must remain compatible.

## Versioning and Failure Semantics

Provider-owned connection-option schema versions, target-option schema versions, and
any present `EnvironmentState.state_version` are independent. Foundation owns the
versioning of its resources and revisions. Providers reject unsupported option or
state versions explicitly; migration never silently selects another account or
target.

| Failure                                                    | Behavior                                                    |
| ---------------------------------------------------------- | ----------------------------------------------------------- |
| Unknown or disabled Provider key                           | Fail before validation or credential resolution             |
| Unsupported or invalid connection options                  | Fail with bounded diagnostics and no external effects       |
| Invalid effective target specification or Provider options | Fail before Control or Environment construction             |
| Provider does not support requested ownership              | Fail validation; do not approximate with another mode       |
| Missing required, unexpected, mismatched, or invalid state | Fail before external effects                                |
| Missing credential or runtime collaborator                 | Fail construction or explicit operation before mutation     |
| Entry-point import or Provider creation failure            | Fail catalog construction; never silently omit the Provider |

Errors never expose credentials, bearer URLs, Docker socket details, unsafe Host
paths, or unbounded native exception text.

## Invariants

01. `EnvironmentProvider` is the only shared plugin abstraction.
02. Foundation resources and persistence models are not shared Provider contracts.
03. Provider connection options select an account or endpoint, not one target.
04. A managed target specification is a create recipe; target identity belongs in
    required state after creation.
05. An attached target specification identifies an existing target without
    transferring ownership and may need no independent state.
06. Validation and object construction perform no external I/O.
07. `EnvironmentControl` performs external lifecycle calls for Foundation but owns no
    database access or durable lifecycle policy.
08. `Environment` owns only exact-target data-plane access.
09. Credentials and live collaborators remain process-local.
10. Catalog availability never grants Agent or tenant authority.
11. No compatibility alias recreates a second lifecycle Provider or lifecycle-capable
    Environment.
