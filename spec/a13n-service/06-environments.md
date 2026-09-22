# Environments: mounts and external lifecycle

A thread chooses environments; a run uses the mount set accepted for it. Several threads may share one instance. That makes lifecycle coordination a property of the **instance and all its active users**, not of a single thread or worker's last-used timestamp.

## Tables

```
environment_providers
  id  organization_id  workspace_id NULL  type  name  config  credential NULL  enabled
  version  created_by_id  updated_by_id  created_at  updated_at

environment_templates             revisioned head columns from 04
environment_template_revisions    immutable config from 04

environments
  id  organization_id  workspace_id  provider_id  provider_identity  template_revision_id NULL
  owner_principal_id NULL  name  status  handle NULL  generation
  operation_id NULL  operation_started_at NULL  operation_deadline NULL
  lease_owner NULL  lease_token_hash NULL  lease_expires_at NULL
  failure NULL  last_used_at NULL  version  created_at  updated_at
  status IN ('creating','starting','ready','stopping','stopped','deleting','deleted')
  CHECK (status NOT IN ('ready','starting','stopping','stopped') OR handle IS NOT NULL)
  UNIQUE (operation_id) WHERE operation_id IS NOT NULL

thread_environments
  thread_id  environment_id  organization_id  workspace_id  name
  working_directory NULL  created_at
  PRIMARY KEY (thread_id, name)
```

Composite foreign keys enforce workspace consistency. The mount set a run uses is frozen at acceptance into `runs.environment_mounts`, like its revision and options. While the run is accepted or running, that column is the durable active-use evidence, across worker loss, handoff and backoff; a GIN index on it answers “which active runs use environment X”. No heartbeat or release callback is needed.

`template_revision_id` is NULL only for registered external devices. Device ownership is private by default (`owner_principal_id`); workspace-managed sandboxes have NULL ownership. Attaching/using a private device requires its owner, not merely a workspace runner role. Retired environments retain tombstones while history refers to them.

The template config specifies provider, base image, resources, network policy and storage semantics. Mount `workspace` appears at `/workspace`, extras at `/mnt/{name}`. Names and working directories are validated by the sandbox contract; path traversal cannot select the worker's host filesystem. The production `local` adapter is not an isolation boundary and is disabled outside explicit development mode.

`provider_identity` freezes the non-secret account/project/region/backend locator that gives the handle meaning. Credentials may rotate, but a provider edit cannot silently redirect an existing handle to another account/endpoint. Resolve current credentials and validate this binding before each lifecycle operation; incompatible edits block use with an actionable reason. An operation's identity includes this binding for reconciliation.

## Select once, use an immutable mount set

Acceptance locks the thread. If its agent needs a primary sandbox and no `workspace` mount exists, it selects the template's then-default revision and reserves a creating environment plus desired mount in that transaction. No external instance is created yet. It locks all selected environments in ID order, validates scope/ownership/state, and copies the desired mounts into `runs.environment_mounts`. This is the template selection point.

Later agent/template changes do not rebuild an existing sandbox. Desired mount edits are thread operations with `If-Match`, valid during execution but affecting only later acceptance. Removing a desired mount cannot hide an active run's use. A caller can explicitly replace the primary mount for later runs; there is no automatic replacement after a provider failure. Fork/child copy the desired mount set under the origin thread lock unless a fresh fork was requested.

Shared environments deliberately share mutable files and can have concurrent tools from different branches. Run history/checkpoints do not snapshot or roll back those files. Choose fresh environments when file isolation is required. No claim of branch isolation follows from having separate threads.

## One outstanding external operation

The managed lifecycle below applies to service-managed sandboxes. Registered HTTP envd devices are connect-only and follow [envd over HTTP](#envd-over-http); they are excluded from automatic create/start/stop/destroy and orphan cleanup.

Every lifecycle operation has a durable ID before I/O. Status identifies the operation: creating, starting, stopping or deleting, including when progress encounters an error. Store that error in `failure`; do not replace the phase with a generic `blocked` status or add an `operation_kind` column. Under the environment row lock, set the phase, increment generation, allocate `operation_id`, set its call deadline and commit. Workers/control claim dispatch with token/expiry, then release the database before calling the provider. Concurrent callers join that operation; they never issue a conflicting one. A new claim refreshes the bounded call deadline, preserving the operation ID, generation and original `operation_started_at`.

The completion transaction compares environment ID, generation, operation ID and claim token. A late caller cannot publish ready after deletion or after an operation changed. Claim expiry permits reconciliation of **the same operation**, not an unconditional new external call. A row lock or Redis mutex cannot fence a remote request already in flight.

| State    | Next action and evidence                                                                                              |
| -------- | --------------------------------------------------------------------------------------------------------------------- |
| creating | Create with stable instance ID and operation ID; inspect/recover the same operation after lost response               |
| ready    | Open a client using the existing handle; opening performs no lifecycle mutation                                       |
| stopping | At each maintenance scan continue the same stop operation; known success becomes stopped; nobody resumes concurrently |
| stopped  | A waiting active run requests a new starting operation                                                                |
| starting | Resume the same handle; reconcile until known ready                                                                   |
| deleting | Reconcile destruction; forbid new mounts or starts                                                                    |
| deleted  | Terminal tombstone; never recreate this instance ID                                                                   |

`maintain_environments` uses its existing **fixed scan interval** for all unfinished phases. For example, a stopping row is claimed, the adapter continues that same stop ID, and confirmed success changes it to stopped. Pending, timeout or error leaves it stopping with the same operation ID and updated failure details; the next scan visits it again. Each visit has a bounded batch/call deadline and a claim prevents concurrent callers. There is no per-environment backoff schedule or `reconcile_after` field. Scanning never creates a new operation ID merely because a call timed out.

The adapter owns safe repetition/confirmation: it may inspect the existing operation or replay the same ID only when the backend supports that guarantee. Timeout alone does not prove failure or authorize another remote request. Ordinary network failures remain automatically recoverable on later scans. Invalid credentials or an outcome the provider cannot safely establish remains visible in `failure`, with recovery instructions; scans do not invent a conflicting operation. Provider identity/credential errors also refuse use of an otherwise ready handle without overwriting its known lifecycle phase.

An acknowledged completion clears the operation's claim fields and stale failure. Initiating a different operation requires proof that the previous one can no longer execute. A completed stop cannot execute again after a later start because of a delayed duplicate; this is part of the adapter contract, not something a PostgreSQL CAS can guarantee remotely. No silent instance replacement.

## Stop, resume and destroy arbitration

The stop transaction locks the environment, then checks last use and the absence of **accepted or running runs whose `environment_mounts` name it**, and marks stopping. Acceptance locks that same row before installing new active use. If stop wins first, acceptance can reference it, but execution waits for stop completion then starts it. If acceptance wins, stop observes active use and declines. All checks use fresh READ COMMITTED statements after lock acquisition.

Destroy likewise requires no desired thread mounts and no active run mounts, checked under the environment lock. Mount creation and acceptance take that lock and refuse deleting/deleted targets and unresolved permanent provider/identity failures. Thread archive removes desired mounts; an active run's frozen use delays destruction until cancellation/seal commits. Historical mounts retain metadata but do not keep a sandbox running forever.

Opening a ready handle only establishes a client; it cannot secretly resume or recreate. `start` is an explicit operation. If stop destroys ephemeral files by the chosen template policy, this is shown before the operation. Lost instances produce `environment_unavailable`; replacement is explicit and uses a new environment identity. No worker-local fallback.

## Provider contract

Plain DTOs live in `providers/interfaces.py`, not in resource ORM modules:

```python
class EnvironmentProvider(Protocol):
    async def create(self, instance_id: str, operation_id: str, template: Template) -> OperationResult: ...
    async def start(self, handle: Handle, operation_id: str) -> OperationResult: ...
    async def stop(self, handle: Handle, operation_id: str) -> OperationResult: ...
    async def destroy(self, handle: Handle, operation_id: str) -> OperationResult: ...
    async def inspect(self, instance_id: str, operation_id: str) -> OperationResult: ...
    async def inventory(self, cursor: str | None) -> InstancePage: ...
    async def open(self, handle: Handle) -> Sandbox: ...
```

Results distinguish pending, known success/failure and unknown, including handle and evidence. An adapter declares which operations are safely repeatable by ID and how it proves completion. A label supports discovery; it does not by itself guarantee idempotent creation, unique lookup or cancellation of delayed requests. Inventory can return multiple handles for a label so duplicates are visible. Cleanup adopts only a matching live operation; orphan/deleted identities are destroyed with the same uncertainty rules.

Providers unable to meet these guarantees are not advertised as supported managed environment backends. Start with a backend whose semantics can be demonstrated, rather than promising every old provider from the presence of an interface. Reuse the Harness definition's `supports_managed` distinction: connect-only providers cannot back managed templates or receive lifecycle calls; they use the registered handle through `open`.

## envd over HTTP

This iteration supports only the existing Harness `http_envd` provider. The operator deploys envd and supplies an HTTP(S) endpoint and credential. Control performs authorized registration; each executing worker connects directly through the HTTP adapter. The endpoint must be reachable from the service processes that use it. Reuse the existing EIP client and its transport/authentication policy; do not add a second protocol. Reverse WebSocket ingress, device pairing, connection tickets, Redis presence, reconnect takeover and the Control/Worker relay are out of scope. No tables, sweeps or extension hooks are reserved for them.

The provider resource stores endpoint configuration and the encrypted, write-only credential; the existing `environments` row stores the registered device handle and native identity, with no template revision. Registration validates the remote identity outside a database transaction, then revalidates authority and provider configuration before recording it. Endpoint/native identity cannot silently retarget that environment; credential rotation follows the ordinary provider rules. Private-device ownership and frozen run mounts still apply. No envd-specific registration table is needed.

HTTP envd is connect-only: the service neither creates the machine nor starts, stops or destroys the daemon. A registered device is `ready` for selection, which is not a promise of current network reachability. An authorized run opens its own adapter/Session against the registered identity; closing it releases those client resources without stopping the daemon or deleting files. An unreachable device produces `environment_unavailable` during preparation; it does not enter managed starting/stopping phases or trigger a replacement device. Retirement removes service access under the existing mount/reference rules without destroying remote infrastructure.

Keep the existing EIP Session, device-generation and operation-identity checks. A timeout or broken HTTP connection after dispatch does not authorize replaying an unknown non-idempotent command. Recovery must distinguish failure before dispatch from an unknown outcome; reconnecting alone is not proof that the previous operation did not execute.

**Keeps:** explicit environment identity, no silent substitution, active-use protection, short transactions, inspectable unknown outcomes and recoverable external operations. Required crash schedules are in [12](12-validation.md).
