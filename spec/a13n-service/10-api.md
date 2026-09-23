# The HTTP API

One namespace, `/api/v1`. Workspace resources use workspace paths. Provider resources with optional workspace confinement use one organization collection and an explicit `workspace_id` field/filter; there are no mirrored provider routes. No action catch-all or per-protocol error envelope.

## Layout

```
/api/v1/auth/login                         POST                    password login -> session
/api/v1/auth/logout                        POST
/api/v1/auth/password-reset                POST, POST .../confirm
/api/v1/users/me                           GET, PATCH              profile, email change
/api/v1/users/me/keys                      GET, POST (workspace_id required); DELETE .../{key}

/api/v1/organizations/{org}                GET, PATCH
/api/v1/organizations/{org}/workspaces     GET, POST
/api/v1/organizations/{org}/grants         GET, POST; DELETE .../{grant}
/api/v1/organizations/{org}/invitations    GET, POST; POST .../{inv}/revoke
/api/v1/organizations/{org}/model-providers, /models     GET/POST, GET/PATCH .../{id}
/api/v1/organizations/{org}/environment-providers, /web-providers, /connector-providers  same shape

/api/v1/workspaces/{ws}                    GET, PATCH, POST .../archive
/api/v1/workspaces/{ws}/grants             GET, POST; DELETE .../{grant}
/api/v1/workspaces/{ws}/invitations        GET, POST; POST .../{inv}/revoke
/api/v1/workspaces/{ws}/service-accounts   GET, POST; POST .../{sa}/keys; DELETE .../{sa}
/api/v1/workspaces/{ws}/audit-events       GET

/api/v1/workspaces/{ws}/agents             GET, POST
/api/v1/workspaces/{ws}/agents/{agent}     GET, PATCH; POST .../archive, .../unarchive, .../duplicate
/api/v1/workspaces/{ws}/agents/{agent}/revisions          GET, POST
/api/v1/workspaces/{ws}/agents/{agent}/revisions/{rev}    GET; POST .../set-default
/api/v1/workspaces/{ws}/agents/{agent}/export             GET;  POST .../import on the collection
/api/v1/workspaces/{ws}/skills ...                         same shape
/api/v1/workspaces/{ws}/environment-templates              GET, POST, GET/PATCH .../{id}; POST .../{id}/disable, .../enable
/api/v1/workspaces/{ws}/environments                       GET, POST (register external HTTP envd); GET .../{id}
/api/v1/workspaces/{ws}/connections                        GET, POST, GET/PATCH .../{id}; POST .../{id}/test, .../disable, .../enable
/api/v1/workspaces/{ws}/connections/{id}/authorize         POST -> redirect url; POST .../revoke
/api/v1/connections/callback                               GET   public, one-use state
/api/v1/workspaces/{ws}/secrets                            GET, POST; PUT/DELETE .../{id}
/api/v1/workspaces/{ws}/assets                             GET, POST; GET/DELETE .../{id}; GET .../{id}/content
/api/v1/workspaces/{ws}/subscriptions                      GET, POST, GET/PATCH/DELETE .../{id}
/api/v1/workspaces/{ws}/subscriptions/{id}/deliveries      GET; POST .../{delivery}/redeliver
/api/v1/workspaces/{ws}/uploads                            POST (multipart) -> upload_id
/api/v1/provider-types/{kind}                              GET  (model, environment, connector, web, trace)

/api/v1/workspaces/{ws}/sessions                           GET, POST, GET/PATCH .../{id}
/api/v1/workspaces/{ws}/threads                            GET, POST (create + first submission)
/api/v1/workspaces/{ws}/threads/{thread}                   GET, PATCH (labels, mcp_headers); POST .../archive
/api/v1/workspaces/{ws}/threads/{thread}/inbox             GET, POST (submit); PATCH/DELETE .../{entry}; PUT .../order
/api/v1/workspaces/{ws}/threads/{thread}/environments      GET, POST; DELETE .../{name}
/api/v1/workspaces/{ws}/threads/{thread}/runs              GET
/api/v1/workspaces/{ws}/threads/{thread}/stream            GET   SSE from Last-Event-ID
/api/v1/workspaces/{ws}/runs/{run}                         GET, PATCH (labels only)
/api/v1/workspaces/{ws}/runs/{run}/interrupt, /fork, /resume   POST
/api/v1/workspaces/{ws}/runs/{run}/items                   GET   committed display
/api/v1/workspaces/{ws}/runs/{run}/attempts                GET; GET .../{attempt}/trace
/api/v1/workspaces/{ws}/usage                              GET   aggregated from usage_records
```

Conventions:

- Collections are plural nouns; items are `/{id}`; an operation that is not CRUD is `POST /{id}/{verb}` with a verb in the imperative (`archive`, `interrupt`, `fork`, `resume`, `set-default`, `test`, `redeliver`). No colon verbs, no `{action}` parameters.
- Ids in paths are ids. Agents, skills and workspaces also accept their `key` in place of the id, resolved before authorization.
- Every response body is a JSON object. Collections are `{"items": [...], "next_cursor": "..." | null}`. Cursors are opaque; `?limit=` is 1 to 200, default 50.
- Timestamps are RFC 3339 in UTC with microseconds. Ids and enum values are lowercase.

External envd registration and use follow [06: envd over HTTP](06-environments.md#envd-over-http). This iteration exposes no envd pairing, connection-ticket, presence or WebSocket ingress API.

## Authentication

`Authorization: Bearer <api_key>` or the session cookie set by `/auth/login`. Unauthenticated requests to protected routes are `401 unauthenticated`. Login/reset and OAuth callback are narrowly public flows with their own one-use state checks; `/auth/*` is not a blanket exemption. Cookie mutations validate CSRF. Stream authorization completes in a short session before opening the response and is periodically refreshed.

Every API key is confined to exactly one workspace. `POST /users/me/keys` requires a non-null `workspace_id`; service-account key issuance derives it from the workspace route and checks the account's home workspace. Login sessions support organization management; API keys do not. Key management, shared-resource access and issuance obey [03's workspace confinement rules](03-tenancy.md#flows) and [authorization rules](03-tenancy.md#authorization), including when a route is outside `/workspaces/{ws}`.

## Preconditions

Mutable-resource PATCH/PUT/DELETE and state-changing operations such as set-default, archive and revoke require the resource's strong `If-Match`; missing is 428 and stale is 412. Thread inbox edits/reorder, desired mount edits and thread `PATCH` (labels, `mcp_headers`) use the thread ETag. Only pending entries can be edited/withdrawn; withdrawal retains a tombstone and request key. There is no delete-and-reuse-key loophole. Assigned/consumed/failed entries return conflict.

Appending a message requires no ETag. Resume names an exact waiting run and conflicts unless it is the thread's idle waiting head. Interrupt names an exact run and is idempotent by its state contract. Fork names an immutable completed/waiting origin. Tests/probes are repeatable operations without a mutation key.

## Idempotency

Only the following v1 commands promise request-key replay. Do not advertise generic idempotency on resource creates whose schema provides no evidence:

| Command                                                                   | Evidence / key namespace                                                                          |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| New thread plus first message, existing-thread message, fork plus message | Original entry, unique `(workspace_id, principal_id, request_key)` shared across these operations |
| Resume                                                                    | Successor run, unique `(workspace_id, resumed_by_id, request_key)`                                |
| Internal spawn                                                            | Child thread, unique origin run/tool call                                                         |
| Child result                                                              | Original result entry, unique sealed child run; outbox has the same dedupe identity               |

Evidence lives on the object the command creates. Execution creation commands require `Idempotency-Key`. Entry evidence stores operation kind, requested target and canonical request digest; resume evidence stores the canonical request digest on the successor, whose parent is the target. Same key/different intent returns 409 conflict. Authentication/current scope permission precede lookup; replay lookup precedes mutable state/precondition validation. Responses return the same created IDs with current status (200 instead of 201), not a promise to replay byte-for-byte an old response. Pending edits never change the original digest.

Preflight lookup is read-only. The final transaction arbitrates with the unique index. A concurrent loser rolls back all tentative rows, then loads/verifies the winner; never catch a uniqueness error and commit the loser-created thread. Objects staged by a loser remain stored under the [v1 object policy](07-facts-and-delivery.md#objects). A lost database acknowledgement is resolved by the same key on a fresh connection. Keys/tombstones remain with history; v1 does not purge it. Resource creation uses explicit resource uniqueness (for example `key`) and ordinary conflict/readback; adding universal replay later requires its own concrete contract.

## Errors

```json
{"error": {"code": "not_found", "message": "agent agent_01j9… not found",
           "details": {"kind": "agent", "id": "agent_01j9…"}}}
```

Shared codes, listed in [02-layout.md](02-layout.md#error-codes). Clients branch on `code` and `details`, never on `message`.

## Submission

```json
POST /api/v1/workspaces/{ws}/threads/{thread}/inbox
{
  "kind": "message",
  "delivery": "steer",
  "payload": {"content": [{"type": "text", "text": "..."}, {"type": "asset", "asset_id": "asset_…"}]},
  "agent_id": "agent_…",
  "agent_revision_id": null,
  "options": {"labels": {}, "max_usage": {"requests": 200}}
}
-> 201 {"entry": {...}, "run": {...} | null}
```

Inbox POST accepts one public kind, `message`, requiring the thread's `run` verb and an `Idempotency-Key`. It requires `payload` and `agent_id`; revision, options and `delivery` are optional. Replies to user questions are ordinary messages.

`POST /threads` takes the message body plus `session_id` (or none, to create a session) and optional `mcp_headers`, and returns the thread, the entry and the run. [04](04-resources.md#caller-headers) owns header validation; messages carry no headers.

Messages require an explicit agent. [05](05-runs.md#source-selection) owns source eligibility, including question-only and mixed waits; its [incorporation rules](05-runs.md#assignment-and-incorporation) own steer delivery and input state transitions. The receipt reports pending/assigned/consumed/failed/withdrawn and owning run, rather than promising immediate execution. Inbox-full responses use `rate_limited` with the count/byte limit and occupancy in details; pending edits that exceed the byte allowance leave the entry unchanged.

## Resume

```json
POST /api/v1/workspaces/{ws}/runs/{run}/resume
{"answers": [{"tool_call_id": "call_…", "action": "approve"}]}
-> 201 {"run": {...}}
```

Resume answers a waiting run's approval and client-tool requests. It requires the thread's `run` verb and an `Idempotency-Key`. Each answer carries `tool_call_id` and a typed action: `approve`, `reject` (optional reason), or `complete` (client-tool result); the list may be empty. The request accepts no message payload, agent selection, options or delivery mode, and has its own bounded size and answer count. Unless the run is the thread's idle waiting head, the response is `409 conflict` and nothing is stored. The response is the successor run; [05](05-runs.md#waiting-interrupt-and-fork) owns normalization and inheritance.

## Resource scope

Provider listing authorizes a requested workspace and returns its providers plus shared ones; organization-wide enumeration requires organization authority. Create requires an explicit workspace ID or explicit NULL with organization write permission. Path/filter scope is checked before object lookup, including on idempotent replay and content routes.

## Streams

`GET /runs/{run}/items` returns the items of the run's committed display object, the stream position it covers, the current attempt, the outcome and `complete` (execution sealed). A newly accepted run has an empty view. Input disposition is returned from PostgreSQL under [05's incorporation rules](05-runs.md#assignment-and-incorporation). Completed/waiting views use the display frozen at seal; failed/cancelled views show the last committed display, including a worker-written interrupted tail, with the database outcome, as defined in [07](07-facts-and-delivery.md#checkpoints-and-display).

`GET /threads/{thread}/stream` returns SSE for one thread: provisional output deltas of its runs, boundary markers, and the gateway's `changed`, `reset` and `gap` control frames. `Last-Event-ID` is the opaque stream cursor. A client reads the thread and the items of its active run, attaches at the covered position, and follows the frame rules in [07](07-facts-and-delivery.md#the-thread-stream). There is no run-level, session-level or workspace-level stream. Display, stream and collection cursors are distinct types and cannot be substituted for one another. Streaming endpoints hold no database session while idle.

## OpenAPI

The service serves `/api/v1/openapi.json`; `make service-contract-generate` writes it to `proto/a13n-service/openapi.json` and regenerates the Console client. Tags are the packages: `auth`, `tenancy`, `agents`, `skills`, `environments`, `models`, `connections`, `secrets`, `assets`, `subscriptions`, `runs`. [11](11-transition.md#package-placement-and-build-boundary) owns archival of the old exports and the generation boundary for the new HTTP/event contracts and wire fixtures.
