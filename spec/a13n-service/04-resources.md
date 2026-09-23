# Resources: what a tenant configures

`resources/` holds everything a tenant sets up before submitting work. Each kind is one package using the default layout in [02-layout.md](02-layout.md#a-resource-package). This document lists the kinds, their tables, and the few rules they share.

## Two lifecycles

Every resource is one of two things, decided by one question: **does a run freeze it?**

| Lifecycle                                                                                                                                                        | Shape                                                 | Kinds                                                                                                                   | Retire                                                     |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| **Revisioned.** Agent selection happens at acceptance; skills/subagent edges pin revisions; template selection happens when acceptance first reserves a sandbox. | head plus immutable revisions; head points at default | agents, skills, environment templates                                                                                   | `archived_at` on head                                      |
| **Live.** A run resolves the current row each time it executes, because credentials rotate and endpoints move.                                                   | one mutable row, optimistic concurrency by ETag       | environment providers, model providers, models, web providers, connections, subscriptions; secrets are live credentials | disable resources; explicitly delete secrets/subscriptions |

Assets are immutable content; retirement hides new use without deleting retained content.

Mutable rows have a monotonic bigint `version`, incremented by the database on changes. Strong ETags encode identity/version and representation variant, checked under the row lock. Timestamps are for display, not concurrency. Expanded representations must include their dependencies in the tag or expose separate resource reads; a tag cannot promise byte-equivalence for fields changing independently of its row.

Sessions separate metadata concurrency from durable conversation activity. Their database stamp increments `version` exactly once when any business column changes, excluding only `version` and `updated_at` from change detection. An activity-only touch preserves the metadata version and takes the greater of the previous and supplied activity times; a metadata change takes at least the previous time and fresh database time. Supplied versions and no-op writes cannot manufacture a change. This exception applies only to Sessions; other resource stamps remain unchanged. The expanded Session read includes independently changing Run summaries and Agent names, so it exposes its metadata version without a strong ETag. See [Session activity](05-runs.md) for activity ownership and lock ordering.

### Shared columns

```
Tenant:   organization_id  workspace_id
Stamped:  version  created_at  updated_at
Authored: created_by_id  updated_by_id
```

Revisioned heads:

```
<heads>
  id  organization_id  workspace_id  key  name  description  default_revision_id NULL
  labels  archived_at NULL  version  created_by_id  updated_by_id  created_at  updated_at
  UNIQUE (workspace_id, key)

<revisions>
  id  organization_id  workspace_id  <head>_id  number  config  digest  note NULL
  created_by_id  created_at
  UNIQUE (<head>_id, number)
```

`key` is a caller-chosen, URL-safe handle unique in the workspace (`code-review`), used in the Console URL and by the SDKs. `number` starts at 1 and increments per head. `config` is the complete frozen configuration of that revision; `digest` is its SHA-256 and is what a run's checkpoint records. Revision rows are immutable by trigger. `default_revision_id` is NULL only between creating a head and its first revision; runs cannot start on such a head.

`resources/revisions.py` holds the two helpers every revisioned kind calls:

```python
async def add_revision(session, head: HeadRow, config: BaseModel, *, actor, note) -> RevisionRow
def set_default(head: HeadRow, revision: RevisionRow) -> bool   # False when already default
```

`add_revision` allocates the next `number` under the head's row lock, computes the digest, and sets the default if the head had none. Whether a new revision becomes the default is the caller's choice (`make_default: bool` on the request, default true).

## Kinds

### agents

```
agents            head columns
agent_revisions   revision columns; config: AgentConfig
```

`AgentConfig` is the Harness agent definition as the service accepts it: model selection (a `model_id`), instructions, skills (`[{skill_id, revision_id}]`, pinned), connections (`[connection_id]`), web tool selection, tool permissions and reviewer settings, subagents (named edges to other agents, inline or async, each with a pinned `agent_revision_id`), `environment_template_id`, secret requirements, output schema, usage limits. Its exact schema lives in `resources/agents/schemas.py` and follows the Harness contract.

Creating a revision validates every reference: the model exists and is enabled, each skill revision exists and belongs to this workspace, each connection is enabled, the template exists, with tenant/owner checks from [03](03-tenancy.md#tenant-integrity). Inline edges must be acyclic; async depth/count limits apply at spawn. Pinning direct edges prevents changed defaults altering recovered execution without copying a second graph. Credentials are resolved only at execution; live references are revalidated.

Operations: create, get, list, update (name, description, labels), archive, unarchive, duplicate (creates a head and copies one revision), export and import (YAML of one revision), create revision, set default revision, list revisions. There is no "current revision set" separate from "set default"; the old package had both.

#### The configuration assistant

The assistant is an ordinary agent whose head has `source = 'builtin'` (`agents.source IN ('custom', 'builtin')`, one extra column on this head only). At startup, `all` and `control` synchronize it per workspace under a head lock and unique builtin key. Compare digests after locking to avoid duplicate revisions across replicas; workspace bootstrap uses the same path. The agent is visible and duplicable; the builtin definition changes only through deployment configuration.

Its tools are read tools over resources the caller may read (agents, models, skills, connections, templates) plus two write tools, `create_agent` and `create_agent_revision`, which call the same service functions the API calls, under the invoking user's authority, gated by the Harness tool-approval flow. There is no draft, no candidate state, no separate session kind. The conversation is the editing session; the revision is the result.

### skills

```
skills            head columns
skill_revisions   revision columns; config: SkillManifest; package_ref (object key of the zip)
```

A package is uploaded through `/uploads`, which stores it under a tenant-bound upload key and returns an `upload_id`. Revision creation validates the manifest, expanded-size/file-count limits and archive paths, then references the upload key as `package_ref`. GitHub import accepts `{repository, ref, path}` and fetches before the transaction; validation records the resolved commit. Unreferenced uploads remain stored in v1; there is no expiry or inventory cleanup. Upload size/rate limits still apply. An upload handle never authorizes an arbitrary object-store key; see [07](07-facts-and-delivery.md#objects).

### environment_templates and environment_providers

See [06-environments.md](06-environments.md). Templates are revisioned; providers are live.

### model_providers and models

```
model_providers
  id  organization_id  workspace_id NULL  type  name  config  credential NULL  enabled
  version  created_by_id  updated_by_id  created_at  updated_at

models
  id  organization_id  workspace_id NULL  provider_id  key  name  config  pricing NULL  enabled
  version  created_by_id  updated_by_id  created_at  updated_at
  UNIQUE (provider_id, key)
```

`type` selects a Harness `ProviderDefinition` from the registry; `config` and `credential` are validated against that definition's schemas. `credential` is write-only: encrypted at rest (see [Secrets](#secrets)), never returned, replaced whole or removed with `null`. `workspace_id IS NULL` makes a provider (and its models) shared by every workspace of the organization: a matching workspace grant may exercise its permitted `read`/`run` verbs; only an organization-scope grant may `write` or `admin` it (the rule in [03-tenancy.md](03-tenancy.md#authorization)).

`models.config` holds the model's settings bounds and characteristics (context window, modalities); `pricing` holds the price table used for cost display and, later, budgets. Operations on providers: create, get, list, update, disable, test (one probe call, never inside a transaction), list catalogue (the definition's known models, for the Console to offer). Operations on models: create (manual or from catalogue), update, disable.

Provider `test` uses non-billable authentication/metadata probes. Billable model/tool verification goes through an ordinary run and the admission policy; a resource test must not become an unmetered execution path.

### web_providers

Same table shape and scope rules as `model_providers` (`type`, `config`, `credential`, `enabled`). Each row configures a backend account for search or scrape; its implementation lives in `providers/web/`. Multiple accounts may use the same implementation. Fetch and download use the host HTTP transport and require no provider resource.

Three provider tables with shared columns is deliberate: each has typed foreign keys from the kind that uses it, and the shared shape is a mixin, `ProviderColumns`, not a shared table with a `kind` column.

### connections and connection_authorizations

```
connections
  id  organization_id  workspace_id  type  name  config  auth  credential NULL  enabled
  version  created_by_id  updated_by_id  created_at  updated_at
  type                                  -- registered tool-source definition
  auth IN ('none', 'bearer', 'headers', 'oauth', 'managed')

connection_authorizations
  id  organization_id  workspace_id  connection_id  principal_id  status  credential NULL
  generation  operation_id NULL  operation_kind NULL  operation_deadline NULL
  oauth_state_hash NULL  redirect_uri NULL  return_uri NULL  failure NULL  expires_at NULL
  version  created_at  updated_at
  status IN ('pending', 'active', 'revoked', 'reauthorization_required')
  operation_kind IN ('exchange', 'refresh', 'setup', 'complete', 'revoke')
  UNIQUE (connection_id, principal_id)
```

A connection is a source of tools. `mcp` names a Remote MCP server (`config.url`, `config.tools` selection); `composio` names a Composio app (`config.app`, `config.actions`). `auth` says how requests to it are authenticated. For `none`, `bearer` and `headers` the credential is workspace-level and lives on the connection. For `oauth`, every principal who uses the connection authorizes it once: `POST /connections/{conn}/authorize` creates a `pending` authorization holding the PKCE verifier in `credential`, returns the redirect URL, and the callback exchanges the code, stores the token bundle, and sets `active`. Revoking sets `revoked`; authorizing again reuses the row and moves it back to `pending`.

Remote MCP configuration also accepts a finite exact-name `recovery_retry_safe_tools` subset of explicitly selected `config.tools`, defaulting to empty. An authorized Connection writer asserts that repeating those operations is acceptable after an unknown outcome. Remote annotations, names and idempotency hints cannot grant replay eligibility; only the Service adapter projects these host-owned declarations into the Harness recovery metadata. Agent revisions freeze Connection references and tool selection, and may only narrow the live Connection's tool selection; they never freeze credentials or recovery declarations. Current enabled resources, authority and declarations are checked again for recovery.

An endpoint, authentication mode or credential edit clears previous recovery assertions unless the writer explicitly reasserts them in the same ETag-protected update. The Console explains that reconfirmation. A declaration grants neither execution authority nor exactly-once effects. Namespaced MCP request metadata can carry original call identity and correlation, but is not by itself a deduplication contract. A qualified peer using deduplication must enforce a stable identity scoped by logical Service run, Connection and original tool call, excluding attempt identity, and reject semantic-request conflicts. Generic MCP makes no such guarantee.

Remote MCP OAuth uses a preregistered client. `config.oauth` pins the advertised issuer, client ID, explicit scopes and token endpoint authentication (`none`, `client_secret_basic` or `client_secret_post`). Confidential client secrets use the encrypted Connection credential; public clients have none. Protected-resource metadata and OAuth/OIDC issuer discovery must establish the configured resource/issuer binding, S256 support and client authentication before authorization. Missing metadata does not authorize an origin-based fallback. Dynamic client registration, hosted client metadata and private-key client authentication are outside this enrollment boundary.

Operator settings fix one callback URL and an exact allowlist of browser return URLs. A separate expiring HttpOnly Secure SameSite=Lax flow cookie binds state to its initiating user agent; concurrent Connections retain independent bindings. The callback takes no owner selector, rechecks current principal/target eligibility under the initiation's workspace confinement and delegation ceiling, and never forwards code, state or tokens to the Console return URL. API-key callers can initiate within their confined workspace and must retain the flow cookie. Reauthorization replaces the same row without resetting its generation. Public authorization views expose status, expiry and a classified failure, never a verifier or token.

OAuth orchestration belongs to the owning Connection resource services, including callback and refresh. Before code exchange or refresh, lock the authorization, claim its **single operation**, increment generation and persist operation ID/deadline. Only the winner sends the external request; concurrent callers await its result outside the database with a bounded deadline. On success, publish only for the same generation/operation/status, then clear the operation. Revoke, reauthorize and auth-relevant connection changes increment generation and invalidate outstanding operations. Generations never reset when rows are reused. Changing issuer, client or auth settings invalidates authorizations; another endpoint cannot inherit tokens.

A request that may have been sent but lost its response has no generic exactly-once OAuth retry. Deadline recovery sets `reauthorization_required`, retaining the failed operation identity, and refuses to reuse the old refresh token. Only an adapter with explicit replay/retrieval guarantees can recover it. A late response cannot revive revoked or reauthorized state. Occasional reconnection after a crash is preferable to corrupting credentials. A CAS only **after** two calls cannot prevent refresh-token-family revocation; see [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html#name-refresh-token-protection).

Callback state is random, hashed, one-use and bound to principal/connection/generation, PKCE, expiry and an allowlisted redirect URI. Claim before exchange; duplicate callbacks read/join the operation instead of exchanging again. Public reads expose no tokens or verifiers. Refresh on use and optional keep-alive use exactly this service. Adapters receive plain values and return tokens; they never mutate business tables or blindly retry token POSTs. Operation deadlines are monitored even when keep-alive is disabled. Unused pending flows expire too. A missing refresh token or absent refresh capability does not invalidate an otherwise valid authorization-code result; access-token expiry then requires reauthorization. An omitted expiry remains unknown rather than being invented. Remote 401/403 invalidates the affected current authorization without automatically replaying a tool call. Live native MCP sessions keep their authorization generation and cannot silently switch to another principal, token generation or endpoint. OAuth tool discovery remains principal-specific and cannot use workspace-shared discovery hints.

#### Composio managed accounts

Composio uses `auth=managed`. The Connection holds an encrypted write-only project credential `{api_key}`; public configuration pins `app`, finite `actions`, an existing enabled `auth_config_id`, a dated `toolkit_version`, and catalogue-validated nonsecret `connection_data`. The project key authorizes transport, not personal account use. Service filters synthetic `create:*` configurations from catalogue schemas and rejects them before setup. Operators provision auth configurations in Composio Dashboard. No personal credentials or caller-chosen account/user IDs belong in public configuration.

Each enrollment reuses the principal's authorization row with a new generation and fresh opaque remote user correlation. Its encrypted private bundle binds the exact remote account, principal, Connection identity, auth configuration/scheme, original execution ceiling, browser session when applicable, verifier and allowlisted return target. Setup, completion and remote revoke each claim one bounded operation before HTTP. Every request and publication checks current authority, Connection version/identity and generation; database sessions never cross provider I/O. Dated catalogue pages, sparse details and actions use the saved version even after current app metadata advances. Missing or mismatched versions fail without fallback.

The operator configures a fixed Console `/managed/verify` URL in both Service and the Composio project. OAuth requires its one-use `session_uri` verifier. Before navigation, Console retains only workspace/Connection/authorization/generation in tab-local session storage. An independent Secure HttpOnly SameSite cookie proves browser binding. The verifier document synchronously removes the URI from history before application bootstrap, sets early `no-referrer`, and holds the URI only in memory for an authenticated CSRF-protected completion POST. Current principal, original login session when recorded, cookie, generation, original ceiling and exact account must match. API-key initiation remains confined; browser completion must authenticate the same principal. Signed-out or switched sessions restart enrollment. Missing selectors never select an ambient latest flow. Static servers and proxies must redact verifier query strings.

OAuth cannot activate from an early ACTIVE read alone: a legitimate completion claim must precede redemption. Non-OAuth hosted setup uses an explicit confirmation button after return, followed by authenticated completion with the same binding checks. Its documented `status` and `connected_account_id` query parameters are synchronously stripped and discarded; failed or mixed OAuth callbacks are refused. A returned account ID never replaces the saved binding. Concurrent completion observes the owned operation outside SQL and never redeems twice; stale completed requests are refused. Unknown completion permits only exact saved-account inspection, never resending the URI; known rejection requires reconnect. Lost setup without a returned account ID cannot be searched or replayed. Revoke clears the local binding before a single detached remote attempt, and reports unknown or unsupported remote outcomes honestly. Deadline recovery never revives revoked grants or retries mutations. Managed action recovery is always unsafe after an unknown result; request IDs provide correlation only. Discovery and inspection remain principal-private and uncached.

#### Caller headers

A public submission may carry `options.mcp_headers`, a map from connection ID to header name/value pairs, frozen on the run. This is how a bot tells its MCP server which conversation a tool call belongs to. Validate submitted keys against enabled `mcp` connections available to the applicable agent revision and its pinned inline agents: the compatible active revision for a steer, or the requested/default revision for a new run. Async children have their own run and connection scope. Revalidate the resolved revision at acceptance when the default may have changed. Header names follow the same reject list as `static_headers` (hop-by-hop, cookie, authorization, proxy, MCP protocol and session headers), and values contain no control characters. There is no per-connection allow list. A caller header colliding case-insensitively with the connection's own authentication headers is rejected; recheck collisions when resolving live credentials at use.

Internal child creation inherits the **complete frozen map**, including connections the child does not use, and may pass it on to further children. Do not reapply the public submission's connection-membership check to unused inherited keys. Context is not a tool grant: a call must still use a connection declared for its executing agent and pass the ordinary live-resource and credential checks. The header factory returns only the map entry for that exact connection ID; other entries are unused. Values remain plain context, stored and returned like other options, never credentials.

For steer matching, normalize options and header-name casing once and compare all non-header options normally. Compare `mcp_headers` only after projecting each map onto the MCP connections available to the current run's selected agent and pinned inline agents. Unused inherited headers cannot prevent a steer; differing effective headers do. Steering never changes the run's frozen map or its descendants' inherited context. Use the same connection-scope resolution for submission validation, execution and matching.

There is no OAuth client registration table, no shared-setup lock, no discovery cache table. Discovery results (the server's tool list) are fetched on `test` and at execution, and cached in Redis with a short TTL.

### secrets

```
secrets
  id  organization_id  workspace_id  principal_id NULL  key  ciphertext
  version  created_by_id  updated_by_id  created_at  updated_at
  UNIQUE (workspace_id, COALESCE(principal_id, ''), key)
```

A secret is an opaque value a caller stores and never reads back. `principal_id IS NULL` means workspace-owned; otherwise it is private to that principal. An agent revision declares `secret_requirements: [{key, scope: workspace | user}]`; at execution the worker resolves each requirement to a row (workspace-owned by key, or owned by the run's principal by key), and hands the Harness a credential broker that answers by requirement key. The model never sees a value; a tool that needs one names its requirement.

All encrypted columns (`secrets.ciphertext`, every `credential`) use one primitive, `protect(plaintext, aad) -> ciphertext` and `reveal(ciphertext, aad)`, AES-256-GCM under one operator-configured key ring. The envelope carries a key ID; authenticated data binds organization, table, column and row ID. Copying into an outbox row requires reveal/reprotect with the new AAD. Rotation retains decryptability until referenced envelopes are re-encrypted. Plaintext brokers never enter checkpoints, display, audit or errors. **Keeps:** write-only credential semantics. Private secret access requires its owner as well as workspace rights.

### assets

```
assets
  id  organization_id  workspace_id  name  content_type  size  digest  content_ref
  source NULL  retired_at NULL  version  created_by_id  created_at  updated_at
```

Immutable content. Uploaded through the same `/uploads` staging as skills, then `POST /assets {upload_id, name}`. Unique `(workspace_id, content_ref)` makes one upload materialize at most one Asset. Same upload and normalized name returns that Asset with its current retirement state; a conflicting name is rejected. A new upload identity can create a distinct Asset even for identical bytes. Asset identity, metadata, content and provenance are immutable, and retirement cannot be reversed. `source` records provenance when a run produced it: `{"run_id", "run_attempt_id", "tool_call_id"}`. Assets are referenced by id from inbox entry payloads and run outputs. DELETE retires it for new use; authorized readers of retained input and output can still read its content. No physical deletion or retention purge is implemented in v1.

### subscriptions

```
subscriptions
  id  organization_id  workspace_id  name  url  signing_secret  event_kinds  filter  enabled
  version  created_by_id  updated_by_id  created_at  updated_at
```

A webhook subscription. `event_kinds` is the list of event kinds it wants (`["run.completed", "run.failed"]`, any of the twelve run and attempt kinds); `filter` optionally narrows by `agent_id`, `session_id` or `thread_id`. `signing_secret` is encrypted; deliveries carry an HMAC signature header. Delivery is described in [07-facts-and-delivery.md](07-facts-and-delivery.md#outbox-and-webhooks). There are no revisions: the outbox row copies the URL and re-encrypts the secret for its own identity, so changing a subscription affects future deliveries only.

## Rules every kind follows

- **Lock, authorize, precondition, act, stamp, audit**, in that order, in one short transaction. Anything that needs the network (fetching a GitHub repository, probing a provider) happens before the transaction and is re-checked inside by comparing `version` of the rows it depended on. Authorization precedes external preparation too.
- **References are validated at write time, live references again at execution time.**
- **Retirement is one column per lifecycle**, and retired things stay readable: an archived agent is listed with `archived_at` set and refuses new runs; a disabled provider is listed and refuses execution. Assets retire while referenced; secrets/subscriptions can be deleted with audited intent. Removing a credential can fail later execution but changes neither historical facts nor already frozen webhook deliveries.
- **Every mutation records an audit event** with `action = "<kind>.<verb>"`.
- **Labels** are a free-form string map on heads, sessions, threads and runs, edited through the resource's `PATCH`, filterable on list with `?label=k:v`.
