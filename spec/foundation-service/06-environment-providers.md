# Environment Provider Integrations

## Design Position

Foundation Service owns the durable configuration and execution integration that turns an operator-approved Environment provider selection into fresh process-local Harness bindings. It does not add a Foundation Sandbox domain, serialize a Harness provider object, or make an Environment provider a Harness plugin or Pydantic Capability.

An immutable Agent definition establishes the initial topology template and a ceiling for provider keys, operations, and binding count. Each Execution owns a monotonic durable desired topology derived from that template and any later authorized Host mutations. The execution worker resolves exact provider integration locks, materializes fresh `EnvironmentProviderBinding` objects, supplies the initial `EnvironmentRunBinding`, and retains its paired `EnvironmentTopologyController` while the logical Harness run is entered.

Dynamic topology is an ordinary Host reconciliation path. Foundation can durably request an add, refresh, or removal during an active run; the current fenced Attempt materializes any fresh provider bindings and applies one complete topology request through the retained controller. Desired acceptance, process-local topology publication, model notification, and checkpoint selection remain separate facts.

The Harness [Environment Integration](../agent-harness/08-environment-integration.md) owns aggregate entry, immutable routing, scoped readiness, operation leases, retirement, portable Environment state, and controller semantics. This document owns Foundation provider selection, registry resolution, desired topology, launch-state custody, Attempt integration, and durable reconciliation.

## Ownership

| Concern                                                                  | Owner                                                    |
| ------------------------------------------------------------------------ | -------------------------------------------------------- |
| Initial topology template and immutable authority ceiling                | Foundation definition revision                           |
| Exact provider integration and artifact revision                         | Foundation dependency lock and operator catalog          |
| Execution desired topology and revision                                  | Foundation execution lifecycle                           |
| Provider parameters and canonicalization                                 | Selected Environment provider integration                |
| Current credentials, policy, and provider account/region selection       | Foundation worker and provider integration               |
| Vendor provision, attach, refresh, recreate, suspend, and destroy policy | Selected provider integration                            |
| Live `EnvironmentProviderBinding` objects                                | Current worker process only                              |
| Aggregate provider scope, routing publication, leases, and retirement    | Harness Environment core                                 |
| Active-run topology mutation handle                                      | Host-retained Harness `EnvironmentTopologyController`    |
| Provider operation and idempotency ledger                                | Foundation execution lifecycle                           |
| Launch envelope and resource-incarnation evidence                        | Foundation execution lifecycle and encrypted storage     |
| Opaque provider launch/reattachment payload                              | Provider codec with encrypted Foundation storage custody |
| Portable backend-local state                                             | `HarnessState.environment_state` and provider codec      |
| Model-facing File/Shell tools, dynamic context, and notices              | Optional Harness `DynamicEnvironmentCapability`          |

`provider_key`, `environment_id`, `binding_id`, and `alias` remain distinct. `provider_key` chooses installed integration code. `environment_id` identifies one logical provider resource. `binding_id` identifies one durable topology slot across compatible Attempts and Harness state. `alias` is the model-facing selector. None grants authority by possession.

## Definition Configuration

The following Foundation-owned schemas are conceptual durable values. The API document owns any transport representation.

```python
class FoundationEnvironmentProviderSelection(BaseModel):
    provider_key: str
    parameters: Mapping[str, JsonValue]


class FoundationEnvironmentBindingTemplate(BaseModel):
    binding_id: str
    alias: str
    provider: FoundationEnvironmentProviderSelection
    permission_ceiling: frozenset[str]
    default_working_directory: str | None = None


class FoundationEnvironmentStatePolicy(BaseModel):
    max_binding_entries: int
    max_binding_encoded_bytes: int
    max_aggregate_encoded_bytes: int
    export_timeout_seconds: float
    restore_timeout_seconds: float


class FoundationEnvironmentPolicy(BaseModel):
    allowed_provider_keys: frozenset[str]
    action_catalog_version: Literal["environment-actions/2"]
    operation_ceiling: frozenset[str]
    max_bindings: int
    max_topology_changes_per_run: int
    state: FoundationEnvironmentStatePolicy
    allow_dynamic_topology: bool = False


class FoundationEnvironmentRequest(BaseModel):
    initial_bindings: tuple[
        FoundationEnvironmentBindingTemplate, ...
    ] = ()
    default_binding_id: str | None = None
    policy: FoundationEnvironmentPolicy
```

`FoundationAgentDefinition.environment` refers to this request. It is an authoring template and authority ceiling, not an allocation request executed during materialization and not a process-local Harness `EnvironmentTopologyRequest`.

`action_catalog_version` locks the exact Harness [Environment action catalog](../agent-harness/08-environment-integration.md#identity-and-core-values) used by every definition and binding ceiling. `operation_ceiling` and each `permission_ceiling` contain only complete members of that catalog and are intersected by exact value; family names, prefixes, wildcards, model tool IDs, EIP JSON-RPC method names or descriptor `available_methods` entries, and unknown `environment.*` strings are rejected during definition materialization. A provider-specific extension requires its own locked typed compatibility contract and cannot enter this core v1 set by naming convention. A catalog migration creates another immutable definition revision and validates every persisted value before provider I/O.

Every provider key allowed by the policy, including one not used by the initial bindings, has exactly one `environment_provider_integration` dependency lock in the materialized definition revision. This allows an authorized active-run addition without loading unlocked code. Adding another provider key or widening operation/binding ceilings requires another immutable definition revision and another Execution under normal compatibility policy.

`parameters` is validated and canonicalized by the locked integration's strict typed codec. It contains resource intent such as provider-neutral size class, region class, image/profile selector, or attachment mode only when that integration defines the field. It contains no plaintext secret, arbitrary import path, Python object, live client, callback, raw request passthrough, ambient environment substitution, or provider endpoint credential.

Initial binding IDs and aliases are unique. A stable initial `binding_id` lets compatible checkpoints match portable backend data across fresh Attempts. Dynamic binding IDs are allocated by Foundation under the Execution and become durable before they can be applied. `max_topology_changes_per_run` is the hard dynamic-publication and observer-journal ceiling for each logical Harness run; a replacement Attempt receives a fresh bounded journal while durable desired revision remains monotonic for the Execution. It and the Environment-state limits are positive finite definition ceilings intersected with stricter deployment limits when the worker constructs `EnvironmentRunBinding`; they cannot be widened during an Execution. Definition materialization performs no provider I/O or resource allocation.

## Provider Registry and Contract

Foundation uses an operator-approved registry keyed by the exact pair of provider key and locked integration revision. Deployment configuration may populate that registry from explicitly selected `converge_agent_harness.environments` distribution entry points after verifying the selected distribution and artifact lock. Installed metadata alone grants nothing, unselected targets are not imported, and definition/API/database values contain only an allowed `provider_key` plus the locked integration revision—never an import path.

```python
type EnvironmentProviderOperationKind = Literal[
    "materialize",
    "discard_unentered",
    "release",
    "suspend",
    "destroy",
]


class EnvironmentProviderOperationIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation_id: str
    idempotency_key: str
    kind: EnvironmentProviderOperationKind


class EnvironmentProviderLaunchState(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment_id: str
    state_version: str
    data: JsonValue


type EnvironmentProviderLaunchLifecycleState = Literal[
    "active", "suspended", "released", "destroyed", "discarded"
]


class EnvironmentProviderLaunchEnvelopeEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    binding_revision: int
    provider_key: str
    provider_integration_revision: str
    provider_type: str
    environment_id: str
    lifecycle_state: EnvironmentProviderLaunchLifecycleState = "active"
    launch_state: EnvironmentProviderLaunchState | None


class EnvironmentProviderMaterializationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation: EnvironmentProviderOperationIdentity
    action_catalog_version: Literal["environment-actions/2"]
    binding_id: str
    binding_revision: int
    canonical_parameters: Mapping[str, JsonValue]
    permission_ceiling: frozenset[str]
    default_working_directory: str | None
    previous_launch_entry: EnvironmentProviderLaunchEnvelopeEntry | None


class EnvironmentProviderLifecycleRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation: EnvironmentProviderOperationIdentity
    action: Literal[
        "discard_unentered", "release", "suspend", "destroy"
    ]
    launch_entry: EnvironmentProviderLaunchEnvelopeEntry


@dataclass(frozen=True, slots=True)
class EnvironmentProviderMaterialization:
    environment_id: str
    provider_type: str
    resource_incarnation: Literal[
        "initial", "retained", "replacement"
    ]
    launch_state: EnvironmentProviderLaunchState | None
    binding: EnvironmentProviderBinding


class EnvironmentLaunchObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["reachable", "missing", "unknown"]
    operation_id: str | None = None
    environment_id: str | None = None
    launch_state: EnvironmentProviderLaunchState | None = None
    reason_code: str | None = None


class EnvironmentProviderLifecycleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation_id: str
    action: Literal[
        "discard_unentered", "release", "suspend", "destroy"
    ]
    outcome: Literal["applied", "retryable_not_applied", "unknown"]
    environment_id: str
    post_reachability: Literal[
        "reachable", "unreachable", "missing", "unknown"
    ]
    post_launch_state: EnvironmentProviderLaunchState | None = None
    reason_code: str | None = None


class FoundationEnvironmentProvider(Protocol):
    provider_key: str
    integration_revision: str
    provider_type: str

    def canonicalize_parameters(
        self,
        parameters: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]: ...

    async def materialize(
        self,
        request: EnvironmentProviderMaterializationRequest,
        context: EnvironmentProviderHostContext,
    ) -> EnvironmentProviderMaterialization: ...

    async def reconcile_materialization(
        self,
        request: EnvironmentProviderMaterializationRequest,
        context: EnvironmentProviderHostContext,
    ) -> EnvironmentLaunchObservation: ...

    async def discard_unentered(
        self,
        materialization: EnvironmentProviderMaterialization,
        request: EnvironmentProviderLifecycleRequest,
        context: EnvironmentProviderHostContext,
    ) -> EnvironmentProviderLifecycleResult: ...

    async def apply_lifecycle(
        self,
        request: EnvironmentProviderLifecycleRequest,
        context: EnvironmentProviderHostContext,
    ) -> EnvironmentProviderLifecycleResult: ...

    async def reconcile_lifecycle(
        self,
        request: EnvironmentProviderLifecycleRequest,
        context: EnvironmentProviderHostContext,
    ) -> EnvironmentProviderLifecycleResult: ...

    async def observe_launch_entry(
        self,
        entry: EnvironmentProviderLaunchEnvelopeEntry,
        context: EnvironmentProviderHostContext,
    ) -> EnvironmentLaunchObservation: ...
```

The selected entry-point target is first validated through the Harness Environment provider factory catalog. A Foundation-capable distribution additionally exposes or adapts to `FoundationEnvironmentProvider`; its `provider_key` equals the selected entry-point name and its `integration_revision` matches the locked revision. Foundation never treats the simple pre-entry-inert `EnvironmentProviderFactory.create_provider_binding()` result as durable materialization evidence. Provider packages that allocate, attach, retain, or reconcile before Harness entry must use the complete protocol below.

The schemas are conceptual. Operation identities, provider launch state, launch-envelope entries, requests, and observations are detached typed Host values; `EnvironmentProviderMaterialization` is process-local because it also groups a live binding. Before any provider call that can allocate, discard, release, suspend, or destroy a resource, Foundation durably creates the corresponding operation record under the current Execution and Attempt fence. Its stable `operation_id`, operation kind, and non-secret `idempotency_key` are then copied into the request. The operation record also binds the exact provider lock, binding and revision, canonical request digest, and lifecycle action where applicable. It survives Attempt and worker loss; a replacement fenced worker or durable maintenance reconciler claims it while the stale worker loses authority. Reconciliation discovery is Execution-wide rather than filtered to current desired intents. Foundation cannot create a new allocative operation for the same durable `binding_id` while any earlier materialization or lifecycle outcome for that slot remains unresolved, even when the earlier revision was superseded or removed.

A retry or reconciliation of that logical operation reuses the same complete request and identity; a new key is never generated merely because a response was lost. Materialization requests require `operation.kind="materialize"`; lifecycle request action and operation kind must match exactly. A response from `reconcile_materialization()` or any lifecycle method must repeat the request's `operation_id`; `observe_launch_entry()` returns no operation ID because it is not completion evidence for a mutation. An integration is eligible for the Foundation registry only when every allocative or lifecycle mutation can honor the stable key or reconcile it through durable provider evidence; a provider that can do neither fails setup rather than receiving at-most-once assumptions from the worker.

Foundation constructs one encrypted launch-envelope entry for every materialized slot even when the integration returns no reattachment state, preserving non-authoritative resource-incarnation evidence without inventing a provider codec payload. `binding` is a fresh unentered Harness protocol object. The provider can provision, attach, or reconstruct vendor resources before returning it, but the Harness aggregate or controller enters and owns the binding scope. The worker never enters that scope a second time.

Before transfer into initial aggregate entry or `controller.apply()`, abandonment creates a durable `discard_unentered` lifecycle operation and invokes `discard_unentered()` through a serialized worker guard. Any retry after `retryable_not_applied` uses the same request identity, while an unknown result is reconciled before another call. `discard_unentered()` can use the still-live materialization for immediate cleanup; after process loss, `apply_lifecycle()` or `reconcile_lifecycle()` executes the same persisted `discard_unentered` action from its launch entry without requiring that Python object. Transfer moves process-local ownership of every candidate to the Harness, including candidates that later fail validation or entry. After transfer, Foundation never calls `discard_unentered()`, `binding.discard()`, or the binding context manager: Harness exits entered scopes and discards every other candidate, while Foundation reconciles the separately staged vendor launch state and executes any later Host lifecycle operation through `apply_lifecycle()`. A present launch state must carry the same `environment_id` as the materialization and bound provider.

`EnvironmentProviderHostContext` carries trusted Execution, Attempt, Agent instance, policy, region/account routing, secret-resolution, telemetry, and lifecycle collaborators. It is process-local and never passed to model tools or stored in parameters. Credentials are resolved just in time and do not enter launch state unless represented by an opaque non-bearer reference whose current use is reauthorized.

`provider_type` is the stable Harness compatibility and portable-state discriminator. It need not equal `provider_key`: several locked Foundation integrations can implement one compatible provider type, while one provider key can require a new provider type when its portable semantics become incompatible.

`resource_incarnation="initial"` is valid only when no prior effective launch-envelope entry exists for the slot. `retained` requires the materialized `environment_id` to equal that prior entry; `replacement` requires a different authenticated ID and means another logical resource was created or attached. A null provider launch-state payload never erases this comparison because the Foundation envelope still retains encrypted incarnation identity. The live binding is not associated with a Harness binding revision until its later `bind(..., binding_revision=...)` call, so Foundation can durably advance an incarnation revision before transferring it.

The integration owns its launch-state schema and version. `reconcile_materialization()` reconciles by the pre-persisted operation identity and canonical request, including when an allocation response was lost before any `environment_id` became known to Foundation. `observe_launch_entry()` receives the complete typed Host entry after trusted worker decryption and is observation-only: it can report current reachability under fresh authority but cannot suspend, destroy, release, or otherwise mutate the resource. Possession of an operation identity or `environment_id` alone still grants nothing.

A `reachable` observation includes an authenticated `environment_id`; a present launch state repeats that ID. `missing` or `unknown` may omit the ID when the provider cannot establish which resource, if any, was allocated. When reconciliation establishes a reachable resource after a lost materialization response, Foundation calls `materialize()` again only with the same operation identity; the integration must reattach or reconstruct a fresh process-local binding for that same resource rather than allocate another. A `missing` result can be retried only under that same idempotency identity and the integration's declared semantics. An unresolved `unknown` result stops setup and remains durable reconciliation work.

`apply_lifecycle()` is the only general side-effecting post-materialization lifecycle seam. `release` relinquishes the Host's provider attachment or lease without asserting destruction; `suspend` and `destroy` have their explicit meanings; retention is a Host policy decision requiring no provider call. Every lifecycle action is reconciled through `reconcile_lifecycle()` with the same operation identity.

Lifecycle completion is action evidence, not inferred reachability. `outcome="applied"` conclusively establishes that the exact requested action took effect under the stable idempotency identity. `retryable_not_applied` conclusively establishes that it did not take effect and leaves the operation pending for retry with that same identity. `unknown` leaves it pending for reconciliation. `post_reachability` is a separate observation: a successful release can remain reachable, a successful suspend can be unreachable, and missing alone never proves destroy. `post_launch_state` is permitted only for an applied suspend and repeats the result's `environment_id`; retryable, unknown, release, destroy, and discard results carry none, so stale codec data cannot look like a selected post-action envelope.

Accepting `applied` atomically completes the operation, appends its bounded lifecycle fact, and transitions the encrypted launch envelope under the current reconciliation fence. Suspend replaces the selected entry with `lifecycle_state="suspended"` and the returned post-action launch state, if any; a later authorized materialization can use that suspended entry. Release, destroy, and unentered discard write `released`, `destroyed`, or `discarded` tombstones with `launch_state=None` and de-select the entry from all future attachment or materialization input, even when post-action reachability is still `reachable`. The tombstone retains bounded identity and operation correlation for reconciliation but grants no reattachment. `retryable_not_applied`, `unknown`, a mismatched operation ID/action/environment, or a lost fence changes neither the authoritative envelope nor operation completion. An unresolved lifecycle operation blocks reattachment or another allocative operation for that durable binding slot.

The integration must distinguish retry-safe attachment from unknown allocation, expose bounded reconciliation, and discard every untransferred materialization. When a transferred materialization is not published, Harness owns its process-local close/discard and the integration reconciles only the staged launch state and vendor resource outcome. A provider that needs opaque data beyond authenticated `environment_id` to preserve one logical resource across Attempts returns non-null launch state containing that same ID. A null payload means no provider codec data is available; the integration can reattach by an active or suspended envelope identity under fresh authority or classify a policy-authorized different ID as `replacement`, but it cannot assume sameness. Released, destroyed, and discarded entries are never reattachment input. The integration cannot rely on `DynamicEnvironmentCapability` for keepalive, cleanup, or recovery.

## Durable Desired Topology

Each Execution has one current immutable desired-topology revision:

```python
class ExecutionEnvironmentBinding(BaseModel):
    binding_id: str
    binding_revision: int
    alias: str
    provider_key: str
    provider_integration_revision: str
    canonical_parameters: Mapping[str, JsonValue]
    permission_ceiling: frozenset[str]
    default_working_directory: str | None


class ExecutionEnvironmentTopology(BaseModel):
    topology_ref: str
    execution_id: str
    revision: int
    action_catalog_version: Literal["environment-actions/2"]
    bindings: tuple[ExecutionEnvironmentBinding, ...]
    default_binding_id: str | None
    definition_revision_ref: str


class ExecutionEnvironmentEffectiveObservation(BaseModel):
    execution_id: str
    attempt_id: str
    attempt_generation: int
    harness_run_id: str
    desired_topology_ref: str
    harness_topology_version: int
    launch_state_ref: str | None
```

The accepted Execution starts with a canonical revision derived from the definition template. Its action catalog version is copied from and fixed by that immutable definition revision; every materialization request repeats it, and no active mutation can change it. `revision` is monotonic for the Execution and is also used as the Harness topology version for that Attempt. `binding_revision` is a durable intent and resource-incarnation revision within the Execution. It changes when alias, provider selection, parameters, permission ceiling, default directory, or logical provider resource changes. A new Attempt always creates a fresh process-local binding object, but that fact alone does not increment `binding_revision` when it reattaches the same desired slot and compatible logical resource. A materialized replacement is already represented when the desired binding revision is greater than that slot's last effective revision; otherwise Foundation must commit a higher incarnation revision before Harness entry. Removing a binding omits it from the next complete topology. Re-adding the same durable slot uses a higher binding revision.

An effective observation is immutable and accepted only under the current Attempt fence. It ties one successfully published Harness topology to the exact logical Harness run and staged launch-state envelope. `launch_state_ref` is non-null whenever that topology contains an Environment binding; a zero-binding topology can use `None`. Initial publication and every later dynamic publication create distinct observations; neither desired acceptance nor a model notice can substitute for one.

`environment_id` is returned only by authenticated provider materialization or reconciliation and is stored in that binding's encrypted `EnvironmentProviderLaunchEnvelopeEntry`; optional provider launch state repeats the same ID. It is durable logical identity, not a credential and not part of the inspectable desired topology. The fresh Harness provider binding exposes the same identity privately so entry can verify that selected launch state and live attachment agree.

A topology mutation is accepted only when all of the following hold:

- the target Execution and immutable definition revision match;
- current Principal and product policy authorize the change;
- the definition Environment policy allows dynamic topology and the selected provider key;
- provider and operation ceilings, binding count, alias/path rules, and requested default remain valid;
- the caller compares the expected desired revision;
- every provider selection resolves to an exact existing dependency lock.

Acceptance commits a new desired revision and a durable command or lifecycle observation before notifying a worker. It does not claim that an active Harness run has applied the revision. Queue delivery and worker wakeup are coordination only.

A provider-incarnation revision is a separate internal reconciliation transition, not a client request and not authority to alter alias, provider selection, parameters, permissions, default, or binding set. It requires authenticated provider replacement evidence, the current Attempt fence, expected Execution and desired versions, and current recreate policy. Its transaction preserves every desired field except the affected binding revision, advances the topology revision, selects the staged replacement launch-envelope entry and any optional provider state, and appends a bounded lifecycle fact. A concurrent authorized desired mutation wins its compare-and-swap; the worker disposes or reconciles its untransferred candidate and restarts from that winner.

## Attempt Materialization

At Attempt start, the worker:

01. loads the immutable Agent definition, current desired topology, selected checkpoint, last effective topology, selected encrypted launch-state envelope, and every unresolved provider mutation for the Execution, including operations whose binding revision is no longer desired;
02. verifies every provider integration and artifact lock;
03. resolves each provider from the operator registry;
04. validates canonical parameters and the provider launch-state codec version;
05. claims unresolved operations for bounded reconciliation under the current Attempt fence or hands them to the durable maintenance reconciler; no new allocative key is allowed for a durable `binding_id` while any earlier materialization or lifecycle outcome for that slot remains unresolved;
06. if an obsolete materialization resolves `missing`, completes it without an envelope; if it resolves `reachable`, first persists its encrypted launch evidence and then creates and completes the policy-selected `discard_unentered`, `release`, or `destroy` lifecycle operation before allowing a newer allocation for that slot; deletion of the desired slot does not cancel this cleanup work;
07. only when the slot has no unresolved predecessor does it durably create one pending `materialize` operation record and stable idempotency identity per desired entry, before any allocative provider I/O;
08. materializes one fresh unentered provider binding per desired entry using that exact operation identity, current Identity, policy, credentials, and prior decoded active or suspended launch-envelope entry, and validates each reported resource-incarnation outcome;
09. on a lost or ambiguous materialization response, marks that operation unknown and performs bounded `reconcile_materialization()` by the same request identity even when no `environment_id` is known; it continues only after the provider establishes a retry-safe missing outcome or a reachable exact resource from which the same operation can reconstruct a fresh binding;
10. constructs and durably writes immutable encrypted launch-envelope entries; for every `replacement` not already represented by a desired binding revision greater than its last effective revision, atomically selects that entry and commits a provider-incarnation desired topology with higher binding and topology revisions under the current Attempt fence and expected Execution/desired versions;
11. stages every remaining launch-envelope entry under the current Attempt fence;
12. re-reads the current desired topology before transfer; if another change won, durably creates the required abandonment operations, discards all untransferred materializations, reconciles staged vendor outcomes, and restarts bounded setup against the winner rather than publishing stale revisions;
13. builds the initial complete Harness topology using the selected durable topology and binding revisions;
14. creates one single-use `EnvironmentRunBinding` with the intersected immutable topology and state limits, retains its paired controller, and passes only the run binding in `RunBindings.environment`;
15. starts controller activation waiting before stream entry, enters one Harness run, and binds the resulting Harness `run_id` to the current Attempt;
16. after initial state restore and `controller.wait_until_active()` succeed, records the initial fenced `ExecutionEnvironmentEffectiveObservation` for the published initial topology before accepting any checkpoint from that run;
17. releases the retained controller after terminal fencing and teardown.

A materialization failure creates a stable abandonment operation before calling `discard_unentered()` for every already created but untransferred materialization, persists and reconciles any provider allocation whose outcome is not known, and stops before model or tool work. If the allocative call itself remains unknown, Foundation reconciles its original materialization operation rather than pretending it has a materialization to discard. There is no fallback to another provider key, unlocked adapter, native host path, or empty Environment when the desired topology is required.

The Harness restores `HarnessState.environment_state` only after these fresh bindings are entered. Foundation launch state establishes reachability; Harness portable state can then restore only compatible backend-local data. The two paths are never merged or substituted for one another.

## Active-run Reconciliation

The current `AttemptRunner` retains the controller while consuming the Harness stream. It starts its reconciliation task before awaiting stream entry and uses `controller.wait_until_active()` instead of polling; activation occurs only after successful initial topology publication and portable-state restore, but still before the input factory. Success therefore proves the initial effective topology and prevents initial restore from racing with a refresh or removal. The current fenced Attempt records that initial observation with its Harness run ID and staged launch-state reference before a checkpoint can select it. The runner can then receive a newer durable desired topology during input factory execution, an inner model attempt, tool work, or recovery backoff. It permits only one materialization/apply sequence at a time and never applies a lower revision after a higher one. Pending revisions for which materialization has not started can coalesce to the latest durable desired topology; every skipped accepted revision remains explicitly not effective. Once materialization for a revision starts, the runner completes or reconciles that attempt before selecting the then-latest newer revision. Every added or refreshed binding follows the same pre-persisted materialization-operation and unknown-outcome algorithm as Attempt setup; active reconciliation never allocates before its stable identity is durable. After process loss, a newer desired revision cannot bypass the unresolved-operation fence for that binding slot, and removing the slot does not remove its reconciliation or lifecycle cleanup. It builds each selected complete Harness request as follows:

- an unchanged binding ID and binding revision is retained without another provider object;
- an added or higher-revision entry receives a fresh materialized `EnvironmentProviderBinding`;
- an omitted entry is removed;
- the Foundation desired revision becomes the Harness topology version.

Before `controller.apply()`, the runner validates `initial` only for a slot with no effective resource and requires any active-run `replacement` to be represented by the request's already higher binding revision. New or changed encrypted launch-envelope entries are then durably staged under the current Attempt fence. Passing the request transfers every candidate to the Harness. The controller then prepares and enters fresh scopes, validates descriptors, facets, and readiness paths, and atomically publishes or leaves the prior topology untouched. Its returned `EnvironmentTopologyChange` is the cancellation-safe process-local commit receipt. Foundation validates its version and digest, then records a later effective observation with the Attempt generation, Harness run ID, desired topology ref, committed Harness topology version, and selected launch-state reference. This observation is fenced and is separate from desired acceptance, Harness observer/event delivery, and model notice delivery.

Cancellation or failure before Harness commit leaves the desired revision durable but not effective. Before transfer, Foundation first persists the selected abandonment lifecycle operation and then calls `discard_unentered()`; after transfer, Harness alone closes or discards process-local candidates and Foundation reconciles staged launch-envelope entries. Host policy can retry the same desired revision, accept a superseding revision, continue temporarily on the last effective topology, or end the Attempt when the change is required. It never rewrites an unsuccessful apply as a model-visible mount or silently rolls the desired revision backward.

If the logical run reaches its terminal fence first, the controller rejects the apply. The worker records terminal-before-apply rather than reusing the controller. A replacement Attempt materializes the then-selected desired topology from durable state and receives a new controller. Process loss after topology publication but before the effective observation does not permit the old run to commit; the new Attempt reconstructs desired state and provider launch evidence instead of inferring process memory from Operation history.

This contract leaves the public command surface optional. Any Foundation API, internal workflow, or product adapter that exposes topology mutation must use the same desired/effective split, authorization, revision compare-and-swap, Attempt fencing, and failure semantics; it cannot call a Harness controller from another process or hand that controller to a client.

## Launch State and Checkpoints

The Environment portion of a Foundation launch-state envelope is partitioned by `binding_id`, provider key, integration revision, provider type, binding revision, and provider codec version. Every selected binding has an active or suspended `EnvironmentProviderLaunchEnvelopeEntry` identifying the authenticated logical `environment_id`; its optional provider launch state can contain opaque provision/attach/suspend/recreate data and reconciliation evidence. Released, destroyed, and discarded tombstones remain bounded reconciliation records but are never selected into a checkpoint or passed as materialization input. A null provider state means no reattachment codec value is available, not that the resource incarnation was never observed. It cannot contain a live client, socket, task, context manager, controller, credential, readiness event, operation lease, process handle, or model-rendered topology.

Portable Harness state is additionally governed by each binding entry's `resource_compatibility`. A `same_logical_resource` entry can be offered only when current materialization and launch evidence let the provider validate the same private resource identity or fingerprint. If the selected envelope has no provider launch-state payload and materialization reaches a different resource incarnation, Foundation can restore only a codec-declared `portable` entry. Cross-resource import remains subject to fresh authority and provider validation; neither mode reconstructs launch state.

An Execution checkpoint selects:

- the complete `HarnessState` candidate;
- the effective Environment topology reference observed by that run;
- a compatible provider launch-state envelope reference;
- the ordinary definition, input, and delivery correlation owned by the execution lifecycle.

Checkpoint selection validates that the effective topology and launch-state envelope describe every binding required to start a later Attempt and that their provider codec revisions match the locked definition. `HarnessState.environment_state.observed_topology_version`, when present, must correspond to the Harness snapshot exported by that run; it is diagnostic and cannot override the selected Foundation topology reference.

A newer desired topology can exist beyond the checkpoint's effective topology. A replacement Attempt starts from current authorized desired state under recovery policy, restores portable entries only where current bindings remain compatible, and ignores removed entries. It does not resurrect the checkpoint topology merely because portable state mentions an old binding.

Provider release, suspension, or destruction after removal or terminal completion is driven by provider policy and Host launch state, not by deleting Harness state. Foundation persists one fenced lifecycle operation before invoking `apply_lifecycle()` and reconciles an ambiguous result through `reconcile_lifecycle()` with the same identity. Only an accepted `applied` result performs the atomic operation-completion and launch-envelope transition above. `observe_launch_entry()` never hides these effects. Cleanup that must outlive the worker remains durable fenced Host work and is rediscovered by replacement Attempts or the maintenance reconciler even after its desired binding disappears; no background Python task escapes the Attempt.

## Security and Failure Semantics

| Failure                                                   | Outcome                                                                                                          |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Unknown provider key or missing exact lock                | Definition acceptance or Attempt setup fails closed                                                              |
| Parameters fail the locked typed codec                    | Field-local definition/topology rejection                                                                        |
| Secret or arbitrary import path appears in parameters     | Reject before revision or desired topology commit                                                                |
| Launch-state version is unsupported                       | Attempt fails before provider use unless explicitly migrated                                                     |
| Provision/attach outcome is unknown                       | Reconcile the pre-persisted operation identity, even without an Environment ID; never allocate under a new key   |
| Unknown allocation's desired revision is superseded       | Keep Execution-wide reconciliation and the slot block; clean any reachable obsolete resource before reallocating |
| Incarnation outcome contradicts prior/effective evidence  | Reject before transfer; reconcile the materialization                                                            |
| Provider-incarnation compare-and-swap loses               | Persist abandonment operation, reconcile/discard candidate, and restart from desired winner                      |
| Release, suspend, destroy, or discard outcome is unknown  | Retain the fenced operation and attachment/allocation block; reconcile the same identity, never reachability     |
| Provider binding cannot establish trusted identity/facets | Harness closes/discards it; Foundation reconciles staged launch entry                                            |
| Active handle cannot be fenced safely                     | Apply fails with topology-in-use; old topology remains effective                                                 |
| Controller is stale, terminal, or from another run        | Reject; never retarget to a replacement Attempt                                                                  |
| Initial portable-state restore fails                      | Controller never activates; no initial effective observation                                                     |
| Worker loses its Attempt fence                            | No effective observation, checkpoint, or terminal commit from that worker                                        |
| Desired revision is accepted but not applied              | Remains explicitly pending/failed, never represented as effective                                                |
| Portable state names an unauthorized or removed binding   | Ignore with bounded diagnostics; never recreate access                                                           |

Provider diagnostics and launch data are bounded and redacted before durable events. Definition and desired-topology read APIs expose no secret or bearer launch field. Registry code, provider clients, and current credentials execute inside the trusted worker boundary.

## Compatibility

The following versions evolve independently:

- Foundation definition schema and Environment policy;
- Harness Environment action catalog selected by the definition;
- provider integration revision and artifact lock;
- provider parameter codec;
- provider launch-state codec and Foundation launch-envelope schema;
- Harness Environment public API;
- provider type and portable `EnvironmentBindingState` codec;
- Foundation desired-topology and checkpoint envelopes.

An exact dependency lock selects reconstruction code but does not by itself migrate durable launch or portable state. An upgrade either declares and runs the owning codec migration or rejects the Attempt before provider or model work. No unknown provider type falls back to direct local execution.

## Invariants

01. Foundation stores provider selectors and typed canonical data, never live provider or Harness objects.
02. Every allowed provider key has one exact dependency lock and operator-approved registry entry.
03. Desired topology, initial and dynamic effective run observations, Harness event delivery, model notice delivery, and checkpoint selection are separate facts.
04. The current worker alone retains the controller paired with its entered Harness run.
05. Dynamic add, refresh, and removal remain possible throughout the active logical run, including input preparation and recovery backoff.
06. Added or refreshed bindings are prepared before atomic publication; failure leaves the old routing snapshot effective.
07. `environment_id`, `binding_id`, alias, and provider key are never conflated or treated as bearer authority.
08. Harness portable Environment state never substitutes for Foundation desired topology or provider launch state.
09. A new Attempt always materializes fresh bindings and receives a new controller under current credentials and policy.
10. Every allocative or destructive provider call follows a durably fenced operation identity that survives worker loss; replacement and maintenance reconciliation discover unresolved operations even after their desired revision or binding is removed.
11. An unresolved materialization or lifecycle operation blocks a fresh allocative key for the same durable binding slot; obsolete reachable allocations receive launch evidence and explicit lifecycle cleanup before that block clears.
12. Unknown outcomes require provider evidence or reconciliation of that same identity, never blind replay or a fresh key.
13. Materialization reconciliation works without a known `environment_id`; a reachable result identifies one exact resource before the same operation can reconstruct a live binding.
14. A fresh Attempt binding object does not by itself increment durable `binding_revision`; a newly materialized logical resource does, unless an already higher not-yet-effective desired revision represents that replacement.
15. Foundation discards materializations only before Harness ownership transfer; after transfer it reconciles launch state while Harness owns every process-local candidate.
16. `observe_launch_entry()` is side-effect free; lifecycle completion comes only from a matching `applied` result and atomically updates the envelope or tombstone.
17. Released, destroyed, and discarded launch entries cannot be selected for attachment; an unresolved lifecycle operation also fences attachment and allocation for its slot.
18. Initial `wait_until_active()` success follows portable-state restore and is recorded with the exact Harness run ID before any checkpoint can select that topology.
19. Setup never publishes a replacement resource under its last effective binding or topology revision; the fenced launch-envelope and incarnation-revision transition precedes Harness entry.
20. Reconciliation never applies a lower desired revision after a higher one; only not-yet-materialized pending revisions can coalesce, and skipped revisions remain non-effective.
21. Every durable Environment operation ceiling is validated and intersected as an exact member of its locked Harness action catalog; family, prefix, wildcard, Toolset, and provider-capability strings grant nothing.

## Trade-offs

### Provider Registry vs. Arbitrary Factories

An operator-approved keyed registry and exact locks add catalog maintenance, but make durable definitions inspectable and prevent untrusted import paths or ambient factory behavior. Embedded Hosts remain free to construct the Harness provider protocol directly.

### Durable Desired State vs. Direct Process Commands

Separating desired acceptance from effective controller publication adds revision and reconciliation records. It allows commands to survive worker loss and makes a new Attempt converge without pretending process-local publication was durably committed.

### Harness-owned Binding Scope vs. Worker-owned Scope

Letting the Harness aggregate enter provider scopes closes operation-lease, dynamic retirement, and teardown ownership for both embedded and hosted callers. Foundation must return fresh unentered bindings and keep vendor launch lifecycle separate, but it avoids duplicating keepalive and cleanup in a generic executor.
