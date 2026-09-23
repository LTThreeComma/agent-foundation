# Resources: what a tenant configures

`resources/` holds everything a tenant sets up before submitting work. Each kind is one package using the default layout in [02-layout.md](02-layout.md#a-resource-package). This document lists the kinds, their tables, and the few rules they share.

## Two lifecycles

Every resource is one of two things, decided by one question: **does a run freeze it?** A run freezes only the content that execution, including recovery, re-reads to define the agent's behavior: its agent revision and the skill and subagent revisions that revision pins. Everything else is live. A consumer that needs a value to stay fixed copies it at the point of use, as a webhook outbox row copies its subscription's URL and key; what is physically built into an environment instance stays with that instance.

| Lifecycle                                                                                                                                               | Shape                                                 | Kinds                                                                                                                                                               | Retire                                                     |
| ------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| **Revisioned.** Agent selection happens at acceptance; skill and subagent edges pin revisions.                                                          | head plus immutable revisions; head points at default | agents, skills                                                                                                                                                      | `archived_at` on head                                      |
| **Live.** A run or environment operation resolves the current row each time it is used, because credentials rotate, endpoints move and policies change. | one mutable row, optimistic concurrency by ETag       | environment templates, environment providers, model providers, models, web providers, connector providers, connections, subscriptions; secrets are live credentials | disable resources; explicitly delete secrets/subscriptions |

Assets are immutable content; retirement hides new use without deleting retained content.

Mutable rows have a monotonic bigint `version`, incremented by the database on changes. Strong ETags encode identity/version and representation variant, checked under the row lock. Timestamps are for display, not concurrency. Expanded representations must include their dependencies in the tag or expose separate resource reads; a tag cannot promise byte-equivalence for fields changing independently of its row.

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

Creating a revision validates every reference: the model exists and is enabled, each skill revision exists and belongs to this workspace, each connection is enabled, the template exists, with tenant/owner checks from [03](03-tenancy.md#tenant-integrity). Inline edges must be acyclic; async depth/count limits apply at spawn. Pinning direct edges prevents changed defaults altering recovered execution without copying a second graph. The template is referenced by ID, not pinned: it is read only when an environment is created or started, never during execution. Credentials are resolved only at execution; live references are revalidated.

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

See [06-environments.md](06-environments.md). Both are live. There are no template revisions: an environment is created from the template's current configuration and reapplies the current runtime settings when it starts or opens.

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

### connector_providers

Same table shape and scope rules as `model_providers`. A connector provider is a hosted integration platform such as Composio, configured once per organization or workspace: `type` selects its registered definition, `config` holds its non-secret settings and `credential` its platform API key. Connections choose one and bind a single external account through it.

Four provider tables with shared columns is deliberate: each has typed foreign keys from the kind that uses it, and the shared shape is a mixin, `ProviderColumns`, not a shared table with a `kind` column.

### connections

```
connections
  id  organization_id  workspace_id  type  name  config  auth  credential NULL
  connector_provider_id NULL  status  generation  failure NULL  expires_at NULL
  authorization NULL  oauth_state_hash NULL
  operation_id NULL  operation_kind NULL  operation_deadline NULL
  enabled  version  created_by_id  updated_by_id  created_at  updated_at
  auth IN ('none', 'bearer', 'headers', 'oauth', 'account')
  status IN ('pending', 'ready', 'reauthorization_required')
  operation_kind IN ('setup', 'complete', 'refresh', 'revoke')
  CHECK ((connector_provider_id IS NULL) = (auth <> 'account'))
  CHECK ((type = 'mcp') = (connector_provider_id IS NULL))
  CHECK (status <> 'ready' OR auth = 'none' OR credential IS NOT NULL)
  UNIQUE (operation_id) WHERE operation_id IS NOT NULL
```

A connection is a source of tools with **one credential**. `type = 'mcp'` names a Remote MCP server (`config.url`, `config.tools` selection). Any other `type` is a connector type served by the chosen `connector_provider_id` (`config.app`, `config.actions`); the connection binds exactly one external account of that app. Two accounts of the same app are two connections. There is no per-principal credential: anyone with `run` on the workspace can let an agent use a connection's account, so a private account belongs in a personal workspace.

`auth` says how the credential is obtained. `none` has no credential. `bearer` and `headers` are entered by a writer and stored write-only. `oauth` runs an OAuth authorization-code flow for an MCP server; the credential is the token bundle. `account` runs the connector provider's hosted account setup; the credential is the provider's account reference, and the provider keeps and refreshes the external tokens. `status` is `ready` when the connection can be used, `pending` before a browser authorization completes, and `reauthorization_required` when an unknown or rejected operation left no usable credential.

`POST /connections/{conn}/authorize` starts the browser flow for `oauth` and `account` connections and requires `write` on the connection, because the resulting credential serves every run in the workspace. It stores the one-use state hash and the encrypted `authorization` (PKCE verifier, redirect/return targets, the connector's pending account reference) and returns the redirect URL; the current credential stays usable until the flow replaces it. The callback completes the flow, stores the new credential, sets `ready` and clears `authorization`. Revoking clears the credential, sets `pending` and asks the remote side to revoke where the definition supports it.

The connection service owns every remote authorization step: connector account setup and completion, OAuth code exchange and refresh, and revocation. Before dispatch, lock the connection, claim its **single operation**, increment `generation` and persist operation ID/kind/deadline. Only the winner sends the external request; concurrent callers await its result outside the database with a bounded deadline. On success, publish only for the same generation/operation, then clear the operation. Revoke, a new authorization and auth-relevant configuration changes increment generation and invalidate outstanding operations; generations never reset. Changing the server, issuer, client, connector provider, app or auth settings invalidates the credential; another endpoint cannot inherit tokens.

A request that may have been sent but lost its response has no generic exactly-once retry. Deadline recovery sets `reauthorization_required`, keeps the failed operation identity in `failure`, and refuses to reuse the old refresh token. Only a definition with explicit replay/retrieval guarantees can recover it, for example by inspecting the connector account it created. A late response cannot revive a revoked or reauthorized connection. Occasional reauthorization after a crash is preferable to corrupting credentials. A CAS only **after** two calls cannot prevent refresh-token-family revocation; see [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html#name-refresh-token-protection).

Callback state is random, hashed, one-use and bound to connection/generation, PKCE, expiry and an allowlisted redirect URI. Claim before exchange; duplicate callbacks read/join the operation instead of exchanging again. Public reads expose no tokens, verifiers or account references. Refresh on use and optional keep-alive use exactly this service. Adapters receive plain values and return credentials; they never mutate business tables or blindly retry token POSTs. Operation deadlines are monitored even when keep-alive is disabled.

#### Caller headers

`threads.mcp_headers` is a map from connection ID to header name/value pairs. It is how a bot tells its MCP server which conversation a tool call belongs to, so it belongs to the thread, not to individual messages. It is set when the thread is created and edited with `PATCH /threads/{thread}` under the thread `If-Match`; acceptance freezes the current map into the run's options, so an edit affects later runs only.

Validate the map when it is written: every key is an enabled `mcp` connection in the thread's workspace, header names follow the same reject list as `static_headers` (hop-by-hop, cookie, authorization, proxy, MCP protocol and session headers), and values contain no control characters. There is no per-connection allow list and no check against any agent revision, because different messages in one thread may select different agents. A caller header colliding case-insensitively with the connection's own authentication headers is rejected; recheck collisions when resolving live credentials at use.

A child thread copies its parent run's frozen map; a fork copies its origin thread's map. Context is not a tool grant: a call must still use a connection declared for its executing agent and pass the ordinary live-resource and credential checks. The header factory returns only the map entry for that exact connection ID; other entries are unused. Values remain plain context, stored and returned like other thread settings, never credentials. Headers take no part in steer compatibility: every run of a thread already carries that thread's context.

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

Immutable content. Uploaded through the same `/uploads` staging as skills, then `POST /assets {upload_id, name}`. `source` records provenance when a run produced it: `{"run_id", "run_attempt_id", "tool_call_id"}`. Assets are referenced by id from inbox entry payloads and run outputs. DELETE retires it for new use; authorized readers of retained input and output can still read its content. No physical deletion or retention purge is implemented in v1.

### subscriptions

```
subscriptions
  id  organization_id  workspace_id  name  url  signing_secret  kinds  filter  enabled
  version  created_by_id  updated_by_id  created_at  updated_at
```

A webhook subscription. `kinds` is the list of lifecycle kinds it wants (`["run.completed", "run.failed"]`, any of the twelve run and attempt kinds); `filter` optionally narrows by `agent_id`, `session_id` or `thread_id`. `signing_secret` is encrypted; deliveries carry an HMAC signature header. Delivery is described in [07-facts-and-delivery.md](07-facts-and-delivery.md#lifecycle-webhooks). There are no revisions: the outbox row copies the URL and re-encrypts the secret for its own identity, so changing a subscription affects future deliveries only.

## Rules every kind follows

- **Lock, authorize, precondition, act, stamp, audit**, in that order, in one short transaction. Anything that needs the network (fetching a GitHub repository, probing a provider) happens before the transaction and is re-checked inside by comparing `version` of the rows it depended on. Authorization precedes external preparation too.
- **References are validated at write time, live references again at execution time.**
- **Retirement is one column per lifecycle**, and retired things stay readable: an archived agent is listed with `archived_at` set and refuses new runs; a disabled provider is listed and refuses execution; a disabled template refuses new environments while existing ones keep reading it. Assets retire while referenced; secrets/subscriptions can be deleted with audited intent. Removing a credential can fail later execution but changes neither historical facts nor already frozen webhook deliveries.
- **Every mutation records an audit event** with `action = "<kind>.<verb>"`.
- **Labels** are a free-form string map on heads, sessions, threads and runs, edited through the resource's `PATCH`, filterable on list with `?label=k:v`.
