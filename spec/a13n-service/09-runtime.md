# Runtime and composition

## Roles

One non-root image, one entry point:

```
a13n-service run --role all        API/control/worker, development default
a13n-service run --role control    API, maintenance, delivery
a13n-service run --role worker     claims and executes runs
a13n-service migrate              migration job
a13n-service bootstrap            initial organization/workspace/admin
```

All/control may auto-migrate under a bounded PostgreSQL advisory lock, unless deployment uses a dedicated migration job. Worker never migrates and checks schema compatibility before claiming. Core/extension schema and checkpoint format versions are checked separately: an up-to-date database does not imply a worker understands every checkpoint.

Worker reserves a local slot before claim. Heartbeat, cancellation and authorization refresh are supervised independently of the Harness task. Shutdown stops claim, requests handoff at safe boundaries and waits only to a configured drain deadline; unfinished attempts then rely on expiry. Attempt and handoff counts are bounded. A rolling deployment must retain workers compatible with outstanding checkpoints or explicitly migrate/reject those runs. `worker_build` is diagnostic metadata, not a sufficient compatibility test.

Health means process liveness. Readiness checks role dependencies with bounded probes; it does not perform migrations or run a provider call per request. Redis failure degrades live observation and may backpressure execution; it never disables the database authority checks or substitutes a worker-local checkpoint store.

## Sweeps

Background work is named bounded functions over durable business evidence. The scheduler provides intervals, jitter, timeouts, cancellation and metrics. Coordination is chosen by the operation, not imposed as one process-wide lock pattern.

| Work                    | Role    | Durable evidence / coordination                                                                                                                                                                |
| ----------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| claim                   | worker  | Due accepted runs; existing periodic scan plus Redis List wakeups; local capacity plus `SKIP LOCKED`; all replicas participate                                                                 |
| advance_threads         | control | Idle unarchived threads with eligible pending input; thread lock, bounded `SKIP LOCKED` batches                                                                                                |
| expire_leases           | control | Current expired attempts; recheck thread/run/attempt state under locks                                                                                                                         |
| deliver_outbox          | control | Due delivery rows; short tokenized claims, external I/O, conditional settlement                                                                                                                |
| retain_facts            | control | Retention eligibility; restricted role, bounded SQL transactions and event-cursor locks                                                                                                        |
| maintain_environments   | control | Managed sandbox lifecycle phases, idle and unmounted instances; fixed scan interval and per-instance operation claims; errors remain on the original phase; connect-only HTTP envd is excluded |
| expire_credentials      | control | Expired tokens/invites; service-account disable/revocation, without deleting historical principals                                                                                             |
| maintain_authorizations | control | Expiring tokens and outstanding OAuth/managed operation deadlines; single operation per authorization                                                                                          |

Intervals are settings, not separate loop implementations. Claim/delivery/operations have different ownership semantics; they do not pretend to share four identical lease columns. Row claims allow replicas to share external work. Short SQL-only maintenance can use a transaction advisory lock as an optimization, but **no advisory lock or database connection is held across external I/O**. Correctness remains in row predicates and transactions. Every scan has an index, maximum batch, deadline and starvation behavior. Retry timing belongs to each operation: environment maintenance uses the fixed interval defined in [06](06-environments.md#one-outstanding-external-operation), while run recovery, outbox delivery and Redis reconnection retain their own backoff rules. No generic jobs table is needed.

### Worker claim wakeups

Keep the existing periodic claim scan and its interval. Add Redis List notifications that trigger the same worker loop sooner; there is no second scan/claim path or claim by notification ID. PostgreSQL remains the task and ownership authority. This changes worker claim scheduling only; inbox acceptance, `advance_threads` and delivery to a running Harness keep their existing rules.

After a transaction creates or returns a run to `accepted`, its caller makes a bounded best-effort notification outside the transaction, covering every acceptance path and attempt recovery/handoff. The deployment-scoped List contains only the constant `wake`, never run IDs or task payloads. Its capacity is **1**: a short Redis Lua script atomically checks `LLEN` and `RPUSH`es only when empty. If full or Redis fails/times out, drop the notification without failing the committed operation. There is no notification retry, acknowledgement or outbox. `accept` itself remains SQL-only.

Workers with free local capacity wait on `BLPOP`; each popped marker wakes one worker, not every replica. That worker uses the existing bounded scan and database claim. One marker may lead to multiple claims: while a scan claims work and capacity remains, continue without another marker. When full, stop popping markers; slot release triggers the same loop immediately. Startup and Redis reconnection also trigger a scan. All triggers share one loop per worker, so they cannot start overlapping scans within that worker; shutdown cancels the wait as well as stopping claims.

The periodic trigger remains active even with notifications and is not postponed by them. It finds work after a lost/skipped notification, a consumer dying after pop, or Redis failure, and finds accepted runs when their `available_at` backoff becomes due. Redis failure must not cause a tight reconnect/scan loop; retain bounded backoff. No database session survives a Redis call or wait.

Capacity 1 bounds pending markers, not wakeup frequency or worker count. A burst can coalesce into one wakeup, so immediate use of every idle worker is not guaranteed; later notifications and the unchanged periodic scans discover remaining work. Empty scans are still possible.

## Assembly

```python
@dataclass(frozen=True)
class Distribution:
    name: str
    routers: tuple[APIRouter, ...] = ()
    tables: tuple[type[Base], ...] = ()
    migrations: tuple[Path, ...] = ()
    settings: tuple[type[BaseModel], ...] = ()
    sweeps: tuple[Sweep, ...] = ()
    providers: tuple[ProviderDefinition, ...] = ()
    authenticator: Authenticator | None = None
    grant_sources: tuple[GrantSource, ...] = ()
    roles: tuple[RoleDefinition, ...] = ()
    admission: AdmissionPolicy | None = None

def build_app(distribution: Distribution, *, role: ProcessRole) -> FastAPI: ...
```

`build_app` wires routers, metadata/migrations, settings, providers, delivery handlers, authentication/grants/roles, policy and lifespan. Built-ins use the same explicit lists. Duplicate identities/routes fail startup; an extension cannot shadow a core operation silently. Provider factories and policy methods are typed, not `Callable[..., object]`.

Distributions add their own package and migration directories without copying runtime. Their migration graph depends on a supported core revision and has one head after an explicit merge. Startup checks compatible schema and registered role/provider definitions; unknown grants fail closed and unavailable providers produce explicit errors. Schema changes use generated, reviewed migrations in the implementation repository; this design repository's SQL probes are disposable experiments, not migration revisions.

## Extension points

Core validation and authorization cannot be replaced by an extension. Additional policy is invoked after core checks and may restrict, never grant missing authority. The seam is defined by call context and transaction rules:

```python
class AdmissionPolicy(Protocol):
    async def accept(self, session: AsyncSession, intent: AcceptedIntent) -> None: ...
    async def proceed(self, session: AsyncSession, call: CallContext) -> None: ...
```

These methods are SQL-only, short, cancellation-safe and idempotent. They may use the shared transaction for their own tables, never commit it, contact another service, or invoke the Harness. External policy data must be prepared before the transaction with explicit validity and commit-time revalidation. Policy row locks follow core domain/ resource locks and are taken in a declared stable order; they precede event locks. Object I/O and conditional replacement are outside the transaction.

| Need                     | Concrete boundary                                                                                                                                                                  |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| #411 composition         | Distribution extends core assembly, migrations and role-specific lifespan                                                                                                          |
| Replace authentication   | Authenticator returns validated identity/confinement; independent management services remain                                                                                       |
| Grants/custom roles      | Additional grant sources plus validated role registry; no fixed database role enum                                                                                                 |
| Additional run admission | `accept` hook in every creation path: submit, feedback, fork, child and automatic advancement                                                                                      |
| #410 budget check        | `proceed` before **every** model/paid-tool dispatch, including inline agents and auxiliary model calls; it checks recorded usage and refuses when that usage has reached the limit |

`CallContext` contains organization/workspace/session/thread/run/attempt IDs, ancestor/root run identity, `call_id`, provider/model/tool identity and the price snapshot or unknown price. Establish `call_id` before dispatch and carry the same identity into its usage records, so a policy can correlate what it allowed with what was charged, including concurrent calls and late reports. Usage-record IDs still identify and deduplicate reports; an ID generated only after a response does not by itself establish this correlation. Children carry their root run identity; they cannot obtain fresh shared allowance by making a new run ID. Pre-dispatch reservation and settlement are not in v1.

For enabled capabilities, model calls include context-compaction summaries, media understanding and tool review as well as main, child and inline-agent inference. Each dispatch must pass the applicable Service cancellation, authority, limit and `proceed` checks. A nested Agent does not automatically inherit the host's checks merely because it inherits telemetry or reports usage. Prefer existing public hooks and Service adapters; add a focused generic shared-library capability only when needed to satisfy this contract. Business decisions remain in the Service.

Recorded-usage budgets are **soft limits**. Concurrent calls may all pass before any of their usage arrives; late reports and already dispatched calls can exceed the threshold by more than one call. Serializing the checks alone does not reserve allowance. Keep the accept/proceed hooks and reject subsequent dispatch once recorded usage reaches the limit, without promising a one-call or fixed monetary overshoot bound. This does not relax structural capacity limits such as worker slots or inbox count/bytes.

OSS implements existing run usage/count ceilings and no monetary ledger. The two hooks are a seam, not billing coverage. Integration must demonstrate the complete dispatch coverage and usage correlation above. #410 decides currency and how to treat unknown charges; it need not redesign dispatch or copy the run engine.

## Settings and operational limits

`A13N_` environment variables use double-underscore sections; optional configuration is named by `A13N_SETTINGS_FILE`. Unknown keys are rejected. Sections: server, database, objects, redis, auth, encryption, worker, control, environments, providers, telemetry, plus declared distribution sections. Configuration is validated once and injected as typed values.

Limits must be finite and visible: request/upload size, expanded archives, ordinary outstanding inbox count/bytes and separate control-feedback payload/result bounds as defined in [05](05-runs.md#inbox-capacity), boundary delivery batch size, per-run display and output bytes, stream count/bytes, provider response bytes, worker slots, tool concurrency, attempts/handoffs, child depth/count, subscriptions per workspace, outbox retention and scan batches. No value is justified by “the inbox is small”. Reaching a limit returns a typed error or stops work with retained evidence; it never silently drops accepted input. Defaults follow vertical-slice measurements.

A provider call, credential refresh, environment operation and object publication have different deadlines. A single 30-second timeout does not make all of them recoverable. Measure queue wait, accepted-to-claim delay, heartbeat lag, checkpoint/display lag, workspace event-lock wait, oldest pending internal delivery, OAuth unknowns and environment operations with persistent failures. Alerts point to durable operation/run IDs and an available recovery action.

## Observability and validation

Use namespaced `a13n-logging`; executables configure logging once. Trace correlation includes organization/workspace/run/attempt and call/operation IDs, without credentials. Model and provider spans are related to the attempt, including late completions. Database authority failures, integrity conflicts and unknown outcomes are distinct metrics.

The small extension fixture in [12](12-validation.md) must add a role, grant source, resource/table/migration, provider and admission policy without editing the core. A fake budget then refuses at `accept` and at `proceed` and is checked against late usage records. This verifies the seam; it does not implement an EE/Cloud billing product.
