# Layout and naming

## Package tree

```
a13n_service/
  app.py              build_app(distribution): routers, tables, sweeps, providers, in one place
  cli.py              `a13n-service run --role all|control|worker`, `migrate`, `bootstrap`
  settings.py         Settings, one section per concern
  distribution.py     Distribution: what a build contributes

  infra/
    db.py             Base, column mixins, transaction(), lock(), advisory_lock()
    ids.py            new_object_id("run") -> "run_7e2a9c0d4b6f1835a8c1d902ef47"
    clock.py          application timestamps; lease authority uses PostgreSQL clock_timestamp()
    errors.py         ServiceError and the code list
    http.py           pagination, If-Match preconditions, error envelope
    objects/          object-store contract and local/S3 implementations
      interface.py    owner-named keys, create-only writes, reads, prefix listing and deletion
      local.py
      s3.py
    redis.py          client, capped stream append/read and the claim wakeup marker
    audit.py          audit_events table, record()
    outbox.py         outbox table, enqueue(), claim(), settle()
    sweeps.py         bounded sweep scheduling and explicit coordination

  tenancy/
    organizations.py  workspaces.py  principals.py  credentials.py  invitations.py  grants.py
                      (credentials.py holds the passwords, api_keys and tokens tables)
    authenticate.py   Authenticator protocol; the local password/session/API-key implementation
    authorize.py      authorize(principal, resource, verb); roles
    routes.py

  resources/
    revisions.py      shared helpers for revisioned kinds: add_revision(), set_default()
    agents/  skills/  environment_templates/  environment_providers/
    model_providers/  models/  web_providers/  connector_providers/
    connections/  secrets/  assets/  subscriptions/
                      each package: tables.py  schemas.py  service.py  routes.py

  runs/
    sessions.py  threads.py  inbox.py  runs.py  environments.py
    attempts.py       leases, heartbeat, the one fenced-update predicate
    accept.py  resume.py  claim.py  execute.py  seal.py
    admission.py      AcceptedIntent, CallContext and built-in run limits
    checkpoints.py    state/display objects, the checkpoint commit and run-prefix cleanup
    display.py        folding Harness events into display items
    stream.py         thread Redis streams, gateway authority reads and control frames
    webhooks.py       lifecycle kinds and notify_subscribers()
    usage.py          usage records, ingest()
    traces.py         trace query over the trace providers
    routes.py

  providers/
    interfaces.py     typed provider contracts and plain DTOs (including web)
    registry.py       definitions keyed by (kind, type)
    models/           bridge to the Harness ProviderCatalog
    environments/     docker.py  e2b.py  envd/ (HTTP only)  ...
    tools/            mcp.py  composio.py
    web/              bridge to the Harness Web provider catalogue and HTTP transport
    traces/           langfuse.py  logfire.py

  migrations/         Alembic environment and versions
```

Four business packages answer four questions. `tenancy`: who is asking and what may they do. `resources`: what has the tenant configured. `runs`: how does one input become one sealed run. `providers`: what does a run call while it executes. Shared mechanisms live under `infra/`; startup, configuration and assembly entry points remain at the root.

Provider packages under `resources/` own tenant-configured backend records: identity, scope, configuration, encrypted credentials, enabled state and management APIs. Packages under `providers/` own backend definitions and adapters. For example, `resources/web_providers/` manages a search account; `providers/web/` connects to its backend.

The tree fixes responsibility and import boundaries, not a final file inventory. These starting modules may become packages when their responsibilities need separate implementations. Keep each split inside its owner and preserve a focused public interface: `infra/db.py` may become `infra/db/`, just as object-store implementations already belong in `infra/objects/`. This applies equally to `tenancy/`, `resources/`, `runs/` and `providers/`; neither a four-file resource template nor a single `execute.py` is a file-size constraint. Split by cohesive responsibility when implementation warrants it, without pre-creating empty layers.

## Import direction

```
runs  ->  resources  ->  tenancy  ->  infra
providers  ->  infra                   (and the Harness)
runs, resources  ->  providers.interfaces, providers.registry   (never a concrete provider)
app.py, distribution.py  ->  everything                  (nothing imports app.py)
```

Rules, each checked by import-linter in CI:

1. Layers: assembly above `runs` above `resources` above `tenancy` above `infra`. A lower layer never imports a higher one. `distribution.py` is a composition-root contract module alongside app, not an infrastructure mechanism. `infra` never imports the business packages or startup/assembly modules; callers provide configuration values. Generic outbox persistence and scheduling belong in `infra`; delivery handlers and scan predicates stay with their business owners and are wired at assembly.
2. Within the Service, provider implementations may import their own package, `infra` and `providers.interfaces`; they may also use the Harness and external libraries. They never import `tenancy`, `resources` or `runs`. A provider receives plain values (a frozen config, a credential) and returns a capability.
3. `runs` reaches providers only through `providers.registry`. Grep for `from a13n_service.providers.environments` inside `runs/` must find nothing.
4. Packages under `resources/` may call each other's `service.py` functions (an agent revision checks that the model it names exists and is enabled; a template checks its provider) and import each other's `schemas.py`. The graph of such calls must be acyclic, and import-linter checks it; `agents` can depend on referenced-resource services; those packages do not depend on `agents`. Cross-resource references are plain IDs/DTOs, not ORM copies.
5. Use another resource's service API/DTOs rather than importing its ORM. Owners may expose focused relational queries taking the caller's short session. Assembly/migrations register metadata explicitly; foreign-key declarations may use table names. Cross-domain transactions are orchestrated by the higher layer, not hidden behind service commits.

The contract file lives at the package root as `.importlinter` and is part of `make verify`.

## Infrastructure and assembly

| Module            | Owns                                                                                                                                                                                                                                                                                               |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `infra/db.py`     | Async engine/session factory, short transactions, column mixins, row locks and SQL-only advisory locks. `Stamped` includes a database-incremented version. Composite tenant foreign keys and explicit exceptions are validated from metadata. Lease decisions use fresh database time after locks. |
| `infra/ids.py`    | One shared `new_object_id(kind) -> str` allocator and the prefix/length registry below; reuse the existing allocation mechanism. Consumers treat IDs as opaque values.                                                                                                                             |
| `infra/clock.py`  | `now() -> datetime` (UTC, microseconds); replaceable in tests.                                                                                                                                                                                                                                     |
| `infra/errors.py` | `ServiceError(code, message, details)` and the code list below; `not_found(kind, id)`, `conflict(kind, id)`, `disabled(kind, id)` factories.                                                                                                                                                       |
| `infra/http.py`   | `Page[T]` and cursor encoding; `etag(row)` and `require_match(row, if_match)`; the error envelope; body size limits.                                                                                                                                                                               |
| `infra/objects/`  | create-only writes, reads, prefix listing and deletion under owner-named keys, digest verification, `ObjectRef(key, digest, size, content_type)` and local/S3 adapters. No conditional replacement. Only run owners delete, and only their unreferenced state/display objects.                     |
| `infra/redis.py`  | the client; capped stream append and multi-key read helpers with typed entries; `wake()` / `wait_for_wake()` for the claim marker.                                                                                                                                                                 |
| `infra/audit.py`  | the `audit_events` table and `record(session, *, actor, action, target, outcome, details)`.                                                                                                                                                                                                        |
| `infra/outbox.py` | the `outbox` table; `enqueue(session, kind, dedupe_key, target, payload)`; `claim(session, kind, limit)` with `SKIP LOCKED`; `settle(session, row, ok, error)`; backoff and dead-lettering.                                                                                                        |
| `infra/sweeps.py` | `Sweep(name, every, run)`; `register(sweep)`; bounded scheduling; each operation declares row-claim or short SQL-lock coordination, never holding a connection across external work.                                                                                                               |
| `settings.py`     | `Settings` built from environment variables and an optional file; sections `server`, `database`, `redis`, `objects`, `worker`, `control`, `auth`, `providers`, `telemetry`. A distribution may append sections.                                                                                    |
| `distribution.py` | the `Distribution` dataclass, see [09-runtime.md](09-runtime.md#assembly).                                                                                                                                                                                                                         |
| `app.py`          | `build_app(distribution) -> FastAPI` plus the process entry for each role.                                                                                                                                                                                                                         |

## A resource package

The four files below are a starting layout for a resource package. The package-wide splitting rule above applies; OAuth orchestration, for example, can have its own module while keeping service and route responsibilities distinct.

| File         | Contains                                                                                          | Never contains             |
| ------------ | ------------------------------------------------------------------------------------------------- | -------------------------- |
| `tables.py`  | the SQLAlchemy row classes, their constraints and indexes                                         | queries, business rules    |
| `schemas.py` | the Pydantic types the API accepts and returns, and the frozen config type where the kind has one | SQLAlchemy                 |
| `service.py` | the use cases as functions taking a session: `create_agent()`, `archive_agent()` ...              | FastAPI, HTTP status codes |
| `routes.py`  | the FastAPI router: parse, authorize, call the service, shape the response                        | SQL, business rules        |

A service function looks like this, and every one of them looks like this:

```python
async def set_default_revision(
    session: AsyncSession, actor: Principal, agent_id: str, revision_id: str, *, if_match: str
) -> Agent:
    agent = await lock(session, AgentRow, agent_id)          # not_found if missing
    authorize(actor, agent, "write")
    require_match(agent, if_match)                           # precondition_failed
    revision = await get_revision(session, agent, revision_id)
    if set_default(agent, revision):                         # False when already default
        touch(agent, actor)                                  # updated_at, updated_by_id
        record(session, actor=actor, action="agent.revision.set_default", target=agent)
    return Agent.from_row(agent)
```

Lock, authorize, precondition, act, stamp, audit. Service APIs enforce authority even when called by tools, sweeps or extensions. Authorize before external preparation too, then revalidate relevant versions/state under locks. Callers own transaction boundaries; SQL-only service functions never commit or hide external I/O. Providers/resources requiring I/O expose an explicit preparation/orchestration function outside the transaction.

## Naming rules

| Rule                                                                                                                                                                                                                                                      | Examples                                                                                                                    |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Tables are plural nouns. Join tables are `<owner>_<owned>`.                                                                                                                                                                                               | `runs`, `inbox_entries`, `thread_environments`                                                                              |
| Object IDs follow the implementation repository's `spec/data-conventions.md`: a stable kind prefix, an underscore and a cryptographically random lowercase hexadecimal suffix. Retain allocated prefixes; the registry below owns new Service allocation. | `run_7e2a9c0d4b6f1835a8c1d902ef47`, `apr_…`, `inb_…`                                                                        |
| Foreign keys are the singular table name plus `_id`. Self-references say the relation.                                                                                                                                                                    | `thread_id`, `agent_revision_id`, `parent_run_id`, `origin_run_id`, `source_entry_id`                                       |
| Timestamps are a past participle plus `_at`.                                                                                                                                                                                                              | `created_at`, `sealed_at`, `archived_at`, `revoked_at`, `expires_at`, `finished_at`                                         |
| A state machine is one column called `status` with lowercase word values and a CHECK.                                                                                                                                                                     | `runs.status IN ('accepted','running','waiting','completed','failed','cancelled')`                                          |
| An on/off switch is `enabled`.                                                                                                                                                                                                                            | `connections.enabled`                                                                                                       |
| Retirement follows the owning lifecycle: heads archive, providers and templates disable, credentials revoke, threads archive, assets retire; grants are explicitly removed. Secrets/subscriptions have audited deletion.                                  |                                                                                                                             |
| A discriminator column is `kind`. Never `type`, `*_type`, `*_kind` on the discriminated row itself.                                                                                                                                                       | `inbox_entries.kind`, `tokens.kind`                                                                                         |
| A provider implementation selector is `type`, because that is what the Harness calls it.                                                                                                                                                                  | `model_providers.type = 'openai'`                                                                                           |
| JSON columns are named for their content, never with a `_json` suffix.                                                                                                                                                                                    | `config`, `payload`, `output`, `failure`, `labels`, `settings`, `pending`                                                   |
| An immutable payload column ends in `_ref` and holds an object key; API values expand to ObjectRef. A run's state and display objects are selected by the typed pointers `checkpoint` and `display`.                                                      | `payload_ref`, `output_ref`, `package_ref`, `runs.checkpoint`                                                               |
| A content hash is `digest` (SHA-256, hex). A hashed secret is `secret_hash`.                                                                                                                                                                              | `agent_revisions.digest`, `api_keys.secret_hash`                                                                            |
| Monotonic counters: `number` for revisions and attempts, `version` for mutable-resource concurrency, `position` for inbox order, `seq` for checkpoint and per-attempt stream sequences.                                                                   | `agent_revisions.number`, `run_attempts.number`, `threads.version`, `inbox_entries.position`, `incorporated_checkpoint_seq` |
| Who: `principal_id` is the identity something executes as, `created_by_id` / `updated_by_id` are authors, `actor_id` is the audit subject.                                                                                                                |                                                                                                                             |
| Row classes end in `Row`. API types have the plain noun. Frozen config types end in `Config`.                                                                                                                                                             | `AgentRow`, `Agent`, `AgentConfig`                                                                                          |
| Functions are verb phrases. Create, get, list, update, archive, disable; never manage, handle, process.                                                                                                                                                   | `create_agent`, `list_runs`, `archive_skill`, `submit_input`, `accept`, `claim`, `execute`, `seal`                          |
| Modules are named for what they hold, never for a phase or a quality.                                                                                                                                                                                     | `accept.py`, `claim.py`; never `preparation.py`, `service_common.py`, `support.py`                                          |
| One word, one meaning. The glossary is normative; a new word needs a glossary entry.                                                                                                                                                                      |                                                                                                                             |

## Id prefixes

The platform's `spec/data-conventions.md`, sections Object Identity and Service ID Allocation, remains authoritative and is not part of the archived Service specifications. The new Service preserves its allocation rules and existing kind prefixes even when a package, table or public resource name changes. Prefixes are never reassigned to a different meaning. A prefix is 2-8 lowercase ASCII letters/digits, beginning with a letter; it need not spell the table's singular noun.

| Prefix              | Table                                          | Random suffix length (hex characters) |
| ------------------- | ---------------------------------------------- | ------------------------------------- |
| `org`               | organizations                                  | 20                                    |
| `ws`                | workspaces                                     | 20                                    |
| `usr`, `sa`         | principals (user, service account)             | 20                                    |
| `key`               | api_keys                                       | 32                                    |
| `ase`, `prt`, `ect` | tokens (session, password_reset, email_change) | 32                                    |
| `rb`                | grants                                         | 24                                    |
| `inv`               | invitations                                    | 24                                    |
| `audit`             | audit_events                                   | 32                                    |
| `ap`                | agents                                         | 20                                    |
| `apr`               | agent_revisions                                | 24                                    |
| `sk`                | skills                                         | 20                                    |
| `skr`               | skill_revisions                                | 24                                    |
| `envtpl`            | environment_templates                          | 20                                    |
| `envp`              | environment_providers                          | 20                                    |
| `mprov`             | model_providers                                | 20                                    |
| `mdl`               | models                                         | 20                                    |
| `wprov`             | web_providers                                  | 32                                    |
| `cnr`               | connector_providers                            | 20                                    |
| `conn`              | connections                                    | 20                                    |
| `sec`               | secrets                                        | 32                                    |
| `ast`               | assets                                         | 24                                    |
| `sub`               | subscriptions                                  | 32                                    |
| `sess`              | sessions                                       | 24                                    |
| `thread`            | threads                                        | 32                                    |
| `env`               | environments                                   | 24                                    |
| `inb`               | inbox_entries                                  | 28                                    |
| `run`               | runs                                           | 28                                    |
| `rat`               | run_attempts                                   | 28                                    |
| `obx`               | outbox                                         | 32                                    |

Use cryptographically secure random bytes encoded as lowercase hexadecimal (`0-9a-f`), with no timestamp or ordering component. The 20/24/28/32-character tiers provide 80/96/112/128 random bits and follow the platform's lifetime allocation budgets per prefix: `10**7`, `10**10`, `10**12` and `10**15` respectively. Unlisted or new kinds default to 32 characters until their owner explicitly assigns a shorter tier against a volume budget; callers cannot choose a shorter suffix. Claims, worker incarnations, publication generations and authentication workflows retain at least 128 random bits. A deployment must review capacity before exceeding a tier's budget; deleting records does not reset it. Database uniqueness is the final collision guard; a collision must never overwrite or reuse an existing object.

Allocation is narrower than acceptance. Preserve the existing Service object-ID acceptance shape of a valid prefix plus 16-64 lowercase alphanumeric suffix characters; do not reject an existing ID merely because it differs from today's allocation length or alphabet. Thread acceptance also preserves `thread-` plus 32 lowercase hexadecimal characters and existing host-supplied forms at their established boundaries. Existing references are never rewritten. This compatibility concerns identity values, not a requirement to restore legacy Service endpoints or migrate legacy data.

Users/service accounts and the token kinds intentionally share tables while retaining their own allocated prefixes. Harness usage-record IDs, native tool-call IDs and provider-owned IDs retain their owner's formats; do not generate a `usage_` replacement or re-encode an external ID. Join tables (`passwords`, `thread_environments`) do not need synthetic IDs. A metadata check verifies these explicit exceptions. API-key secret material, login cookies, OAuth state/verifiers, cursors, handles and digests retain their own contracts; the object-ID tiers do not shorten secrets or change their encoding.

Consumers do not infer authority, routing, ownership or order by parsing an ID. Ordinary collection pagination documents concurrent-change behavior and uses an indexed stable sort. Random IDs are not tail cursors; live observation uses the thread stream in [07](07-facts-and-delivery.md#the-thread-stream).

## Error codes

One exception class, `ServiceError(code, message, details)`, mapped to HTTP status in one table in `infra/http.py`. The codes:

| Code                    | Status | Details carry                                               |
| ----------------------- | ------ | ----------------------------------------------------------- |
| `invalid_argument`      | 400    | `field`, `reason`                                           |
| `invalid_cursor`        | 400    |                                                             |
| `unauthenticated`       | 401    |                                                             |
| `forbidden`             | 403    | `verb`, `resource`                                          |
| `not_found`             | 404    | `kind`, `id`                                                |
| `already_exists`        | 409    | `kind`, `key`                                               |
| `conflict`              | 409    | `kind`, `id`, `reason` (a state rule refused the operation) |
| `precondition_failed`   | 412    | `current_etag`                                              |
| `precondition_required` | 428    | required header                                             |
| `payload_too_large`     | 413    | `limit`                                                     |
| `disabled`              | 422    | `kind`, `id`                                                |
| `unavailable`           | 503    | `dependency` (database, redis, objects, a provider type)    |
| `rate_limited`          | 429    | `retry_after`                                               |
| `internal`              | 500    |                                                             |

The list is shared, not a fixed numerical target. A provider failure during execution is not an HTTP error; it is a run failure recorded in `runs.failure` with the provider's own reason string.

## Object store key layout

Keys name their producer, and every object is immutable. A run writes digest-keyed `orgs/{org}/runs/{run}/state/{digest}` and `orgs/{org}/runs/{run}/display/{digest}` objects; only the run's committed pointers make them reachable, and the run's owner deletes the rest. Other payloads use digest-qualified keys; uploads use `orgs/{org}/uploads/{upload}`. There is no other object reclamation in v1. The rules are in [07](07-facts-and-delivery.md#objects).

## What is deliberately absent

- No `common`, `support`, `management`, `utils` or `helpers` modules. A helper belongs to the root module of its concern or to the package that uses it.
- No generic `Resource[T]` base class or CRUD generator. Ten resources written out by hand in the same shape are easier to read and to diff than one generator.
- No per-feature error classes, cursor modules, audit wrappers or lock helpers.
- No `_json`, `_sha256`, `_type` suffixes; no `Record`, `Manager`, `Coordinator`, `Reconciler` class names.
- No function-local imports. If one seems necessary, the layering is wrong.
