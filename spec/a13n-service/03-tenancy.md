# Tenancy: who is asking, and what may they do

`tenancy/` owns organizations, workspaces, principals, credentials, invitations, grants, authentication and authorization. It has no opinion about agents or runs; it only knows that a resource carries an `organization_id` and, usually, a `workspace_id`.

## Nouns

- An **organization** is the billing and administration boundary. OSS runs one; the schema allows many.
- A **workspace** belongs to an organization and is the resource and work boundary. Sessions, threads and runs belong to exactly one workspace; selected provider resources can be shared within an organization.
- A **principal** is a user or a service account. Users are people with an email; service accounts are identities for programs (the bot app, CI). Both hold credentials and grants.
- A **credential** is what proves a request is a principal. There are three kinds with three shapes: a password (one per user, verified, never looked up by hash), an API key (long-lived, named, always confined to one workspace), and a token (a login session, a password-reset link, an email-change link: short-lived, looked up by hash, one-shot or revocable).
- A **grant** gives a principal a role in a scope. The scope is an organization or a workspace.
- An **invitation** is a pending grant for an email address that is not yet a principal.

## Tables

```
organizations
  id  key  name  settings  version  created_at  updated_at

workspaces
  id  organization_id  key  name  settings  archived_at NULL  version  created_at  updated_at
  UNIQUE (organization_id, key)

principals
  id  kind  name  email NULL  home_workspace_id NULL  status  version  created_at  updated_at
  kind   IN ('user', 'service_account')
  status IN ('active', 'disabled')
  UNIQUE (email) WHERE email IS NOT NULL
  CHECK ((kind = 'user') = (email IS NOT NULL))
  CHECK ((kind = 'service_account') = (home_workspace_id IS NOT NULL))

passwords
  principal_id  hash  updated_at
  PRIMARY KEY (principal_id)

api_keys
  id  organization_id  workspace_id  principal_id  name  secret_hash  expires_at NULL  last_used_at NULL
  revoked_at NULL  version  created_by_id  created_at  updated_at

tokens
  id  principal_id  kind  secret_hash  data NULL  expires_at  revoked_at NULL  created_at
  kind IN ('session', 'password_reset', 'email_change')

grants
  id  organization_id  workspace_id NULL  principal_id  role  created_by_id  created_at
  role                                  -- validated against Distribution role definitions
  UNIQUE (principal_id, organization_id, COALESCE(workspace_id, ''))

invitations
  id  organization_id  workspace_id NULL  email  role  token_hash  invited_by_id
  principal_id NULL  expires_at  accepted_at NULL  revoked_at NULL  version  created_at  updated_at

audit_events
  id  organization_id  workspace_id NULL  actor_id NULL  action  target_kind  target_id
  outcome  details  occurred_at
  outcome IN ('ok', 'denied', 'failed')
```

Notes on the shape:

- Users are global; service accounts have an immutable home workspace and can receive grants/keys only there. Revoking their last grant disables the account and revokes its credentials; it does not delete an identity referenced by historical facts. Workspace admins cannot disable a global user or revoke that user's credentials in other scopes.
- `passwords` is keyed by principal: one per user, replaced in place, never expired or looked up by hash. It is deliberately not in the same table as anything a sweep deletes.
- `api_keys` are long-lived bearer secrets with a user-visible `name`, an optional `expires_at`, and a required, immutable `workspace_id`. The server derives `organization_id` from that workspace and enforces the workspace composite foreign key. Every key belongs to a user or service account and is confined to that workspace regardless of the principal's other grants. There are no organization-scoped or global API keys, and no separate key-scope discriminator. `created_by_id` records the issuer; it does not replace the principal whose authority the key represents.
- `tokens` are the short-lived secrets: login sessions, password-reset links, email-change links. All three are looked up by hash, expire, and are revoked when consumed or logged out. `data` holds the one field a kind needs (`{"new_email": ...}` for `email_change`). Expiry deletes these credential rows, never a principal still named by history.
- `grants.workspace_id IS NULL` means the grant is at organization scope and applies to every workspace of that organization.
- `workspaces.settings` holds workspace-wide defaults that are not resources, today the media-understanding model defaults (`{"media": {"image_model_id": ..., "audio_model_id": ..., "video_model_id": ...}}`). This replaces the old one-row-per-workspace defaults table.
- `audit_events` is append-only (trigger, see [07](07-facts-and-delivery.md#immutability)). `action` is a dotted verb phrase owned by the package that records it: `agent.revision.set_default`, `grant.create`, `credential.revoke`.

## Authentication

```python
class Authenticator(Protocol):
    async def authenticate(self, request: Request) -> Principal | None: ...
```

A request carries either `Authorization: Bearer <api key>` or a session cookie. The local implementation in `authenticate.py`:

1. Looks the presented secret up by hash: a bearer token in `api_keys`, a cookie in `tokens` where `kind = 'session'`; not revoked, not expired.
2. Loads the principal; `status = 'disabled'` fails with `unauthenticated`.
3. Updates `last_used_at` at most once per minute per API key.
4. Returns a `Principal` value carrying `id`, `kind`, and, for an API key, its required organization/workspace confinement. Login sessions are a separate credential kind; they are not global API keys.

Replacing the authenticator (SSO, a cloud login) replaces step 1 and 2 only. Membership, API-key, service-account and profile management keep working, because they are functions in `tenancy/*` that take a `Principal`, not methods of a runtime that exists only when the local authenticator is installed. **Keeps:** the credential boundary, that an API key authenticates its principal and can only narrow, never widen, what that principal may do.

Password login (`POST /auth/login`) verifies the `passwords` row with Argon2id and issues a `session` token with a rolling expiry. Password reset and email change issue one-shot tokens delivered by the configured mailer; consuming one revokes it and, for a reset, revokes every `session` token of that principal.

## Authorization

One function:

```python
Verb = Literal["read", "run", "write", "admin"]

def authorize(principal: Principal, resource: Scoped, verb: Verb) -> None:
    """Raise forbidden unless some grant of the principal covers the resource with the verb."""
```

`Scoped` is anything with `organization_id` and an optional `workspace_id`: a row, a Pydantic value, or a `Scope(organization_id, workspace_id)` literal for collection reads and creates.

Roles are data, not code:

| Role    | Verbs                   |
| ------- | ----------------------- |
| viewer  | read                    |
| runner  | read, run               |
| builder | read, run, write        |
| admin   | read, run, write, admin |

A grant must include the requested verb in its role. Subject to that requirement, a grant covers a resource when the grant's organization matches and either the grant is at organization scope or its workspace matches the resource's workspace. A resource with `workspace_id IS NULL` is shared by the organization, and the rule splits by verb: `read` and `run` are covered by any grant in the organization, at either scope, because sharing means every workspace may use it; `write` and `admin` are covered only by organization-scope grants, because changing it changes it for everyone. The organization row itself is `admin`-only at organization scope. API-key authority is always intersected with its workspace confinement: a key for workspace A never authorizes anything in workspace B, even in the same organization. Against resources shared by A's organization, it permits only `read` and `run`, subject to the principal's grants; it cannot modify shared resources or perform organization administration. Login sessions (or a suitably authorized replacement authenticator) handle organization and account-wide management under the ordinary permission rules. A workspace API key is not an administrator credential merely because it belongs to an administrator.

What each verb means, by example:

| Verb  | Covers                                                                                                                                                                       |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| read  | list and get anything in scope, including other principals' sessions, threads and runs; read items, thread streams, usage, traces; read a secret's metadata, never its value |
| run   | submit input, steer, interrupt, fork, resume; create sessions and threads; add an environment to a thread                                                                    |
| write | create, update, archive resources: agents and revisions, skills, templates, models and providers, connections and their authorization, secrets, assets                       |
| admin | grants, invitations, service accounts and their keys, workspace settings, webhook subscriptions, audit reads, revoking other principals' API keys                            |

`admin` covers people, keys and webhook configuration. A builder can configure external models/tools too; the four-role model does not promise data-loss prevention against a builder or an authorized run. Deployment network policy constrains outbound destinations independently of roles. Private secrets and devices add an owner check to workspace permission; `read` is not permission to reveal any credential value.

**Keeps:** the merge order of the old authorizer (organization grants, then workspace grants, most permissive wins, unknown role values rejected at the boundary). What is gone is the second lattice for agent-scoped grants and the 84 named actions; see [01-goals.md](01-goals.md#what-is-dropped). Agent-level isolation is outside this product model, not a promised one-branch future change.

### Grant sources

`authorize` reads grants through one function:

```python
class GrantSource(Protocol):
    async def grants_for(self, principal: Principal) -> Sequence[Grant]: ...
```

The default source reads `grants`; distributions may add sources, whose results are unioned subject to credential confinement. Built-in/custom role definitions share one startup registry. Role columns are validated text, not a CHECK hard-coded to four names. Unknown roles fail closed; removal of a role requires an explicit data migration or revocation. Role-name conflicts fail assembly. Grant sources return only validated, tenant-scoped values. External-source refresh occurs outside a database transaction, with bounded cache age and explicit fail-closed behavior when stale/unavailable.

Grants are read once per request/attempt refresh and cached only for that bounded scope. Database grants and resource state are revalidated at the mutation's commit arbitration; external grants use the declared freshness contract, never an unbounded network call under a row lock.

## Tenant integrity

Every workspace-owned row has a composite foreign key `(organization_id, workspace_id)` to its workspace. References between workspace-owned rows include workspace identity in their foreign keys. Revisions/default pointers also include the owning resource ID. Same-thread run/input pointers add thread identity. This prevents cross-tenant writes even when an application query omits a predicate.

Organization-shared resources are the explicit exception: reference validation checks the same organization and either matching workspace or NULL workspace, with a database constraint trigger where an ordinary composite FK cannot express the disjunction. A resource's scope never changes in place. Do not use NULL as a wildcard in generic queries. Lists, exports, asset content, events, traces and replay lookups all authorize their scope before resolving a supplied ID. Globally unique IDs do not constitute access control.

The authorization service returns an `ExecutionAuthority` plain value: principal ID, organization/workspace confinement and a ceiling of allowed verbs. Persist it on entries and runs. Effective execution rights are this ceiling intersected with current grants and principal status. Child runs inherit a ceiling; they cannot widen it. Service tools use this value, never a worker's administrative identity. Steering and answering a run need the `run` verb on its thread and do not change the run's authority; see [05](05-runs.md).

Credential expiry/logout/key revocation prevents further authenticated requests. Accepted work is a durable delegation, not a session: stopping it uses interrupt, principal disable or grant revocation. This distinction must be shown in key-management help. Current grants are rechecked before dispatch and by the independent refresh task; revocation latency is bounded by that configured interval, not claimed instantaneous for calls already sent.

## Flows

**Bootstrap.** `a13n-service bootstrap --email --password` creates the organization, a default workspace, the first user, and an organization-scope `admin` grant. It refuses to run twice.

**Invite.** An admin posts an email and a role to `/organizations/{org}/invitations` or `/workspaces/{ws}/invitations`. The invitee opens the link, sets a password (or logs in if the email already exists), and acceptance creates the grant and stamps `accepted_at` and `principal_id`. Invitations expire; acceptance after expiry is `conflict`.

**Service accounts.** `POST /workspaces/{ws}/service-accounts` creates a principal of kind `service_account`, a `runner` (or requested) grant in that workspace, and returns the principal. `POST /workspaces/{ws}/service-accounts/{sa}/keys` issues a home-workspace-confined key, whose plaintext is returned once. Revoking the last grant disables the account.

**API keys for users.** `POST /users/me/keys` requires an explicit, non-null `workspace_id` and issues a key for that workspace under the calling user's identity. Its permissions remain the user's current grants intersected with that workspace. Workspace admins manage their service accounts and keys confined to that workspace, never keys in another workspace. API-key-authenticated list/read/revoke operations, including `/users/me/keys`, are restricted to the invoking key's workspace as well as the ordinary ownership/admin checks. The Console always identifies the selected workspace and offers no organization/global scope option.

Every key-issuance path resolves one workspace and checks both the target principal's grants and the invoking credential's confinement. A caller using an A-confined key may issue only an A-confined key; requesting B, in the same or another organization, is forbidden. User-key creation without a non-null workspace is `invalid_argument`; service-account issuance takes the workspace from its route and verifies the account's immutable home workspace. No path issues an organization/global key. A login session may issue keys for explicitly selected authorized workspaces, but each resulting key still covers exactly one workspace. Enforce this in the shared issuance service, including service-account and service-tool callers; no credential-parent chain or new table is needed.

**Disable.** A global operator or the user can disable a user; a workspace admin can disable its service account. Subsequent authentication fails and execution stops on refresh. Grants remain so re-enabling restores them. Disabling is audited with its actual authority.

Cookie sessions use Secure/HttpOnly cookies and CSRF validation on mutations. Password reset/email-change tokens are hashed, single-use and consumed under lock; mail is queued transactionally in the outbox. Login/reset/callback have bounded rate limits and generic account-existence responses. Replacing authentication declares which local login routes remain installed; it never removes independent principal/grant/key management services.

## Audit

Every service function that changes tenancy state records one `audit_events` row in the same transaction. Denied authorization attempts on `admin` verbs record `outcome = 'denied'`. Resource packages record their own actions with the same `record()`. Denial records use a separate bounded transaction after the rejected operation rolls back; raising an authorization error must not roll back the only denial evidence.

## Open points

- Multi-organization navigation (#411 E2) is a Console concern; the API is already organization-scoped by path, so the service needs nothing.
