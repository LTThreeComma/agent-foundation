# The HTTP API

One namespace, `/api/v1`. Workspace resources use workspace paths. Provider resources with optional workspace confinement use one organization collection and an explicit `workspace_id` field/filter; there are no mirrored provider routes. No action catch-all or per-protocol error envelope.

Workspace audit reads require `admin` and return only events with that actual organization and workspace. Account-wide user/session events are never included through actor or target membership. There is no global-audit API.

## Layout

```
/api/v1/auth/login                         POST                    password login -> session
/api/v1/auth/session                       GET                     authenticated user + session CSRF
/api/v1/auth/logout                        POST
/api/v1/auth/password-reset                POST, POST .../confirm
/api/v1/users/me                           GET, PATCH              profile, email change
/api/v1/users/me/keys                      GET, POST (workspace_id required); DELETE .../{key}

/api/v1/organizations/{org}                GET, PATCH
/api/v1/organizations/{org}/workspaces     GET, POST
/api/v1/organizations/{org}/grants         GET, POST; DELETE .../{grant}
/api/v1/organizations/{org}/invitations    GET, POST; POST .../{inv}/revoke
/api/v1/organizations/{org}/model-providers, /models     GET/POST, GET/PATCH .../{id}
/api/v1/organizations/{org}/environment-providers, /web-providers  same shape

/api/v1/workspaces                         GET                     accessible workspaces + permission hints
/api/v1/workspaces/{ws}                    GET, PATCH, POST .../archive
/api/v1/workspaces/{ws}/grants             GET, POST; DELETE .../{grant}
/api/v1/workspaces/{ws}/invitations        GET, POST; POST .../{inv}/revoke
/api/v1/workspaces/{ws}/service-accounts   GET, POST; POST .../{sa}/keys; DELETE .../{sa}
/api/v1/workspaces/{ws}/audit-events       GET
/api/v1/workspaces/{ws}/events             GET (cursor), /events/live (WebSocket)

/api/v1/workspaces/{ws}/agents             GET, POST
/api/v1/workspaces/{ws}/agents/{agent}     GET, PATCH; POST .../archive, .../unarchive, .../duplicate
/api/v1/workspaces/{ws}/agents/{agent}/revisions          GET, POST
/api/v1/workspaces/{ws}/agents/{agent}/revisions/{rev}    GET; POST .../set-default
/api/v1/workspaces/{ws}/agents/{agent}/export             GET;  POST .../import on the collection
/api/v1/workspaces/{ws}/skills ...                         same shape
/api/v1/workspaces/{ws}/environment-templates ...          same shape
/api/v1/workspaces/{ws}/environments                       GET, POST (register external HTTP envd); GET .../{id}
/api/v1/workspaces/{ws}/connections                        GET, POST, GET/PATCH .../{id}, POST .../{id}/test
/api/v1/workspaces/{ws}/connection-catalog/composio        POST project-key or saved-Connection app/action catalogue
/api/v1/workspaces/{ws}/connections/{id}/authorization/complete POST authenticated managed completion
/api/v1/workspaces/{ws}/connections/{id}/authorize         POST -> redirect url; GET .../authorization; POST .../revoke
/api/v1/workspaces/{ws}/secrets                            GET, POST; PUT/DELETE .../{id}
/api/v1/workspaces/{ws}/assets                             GET, POST; GET/DELETE .../{id}; GET .../{id}/content
/api/v1/workspaces/{ws}/subscriptions                      GET, POST, GET/PATCH/DELETE .../{id}
/api/v1/workspaces/{ws}/subscriptions/{id}/deliveries      GET; POST .../{delivery}/redeliver
/api/v1/workspaces/{ws}/uploads                            POST (multipart) -> upload_id
/api/v1/provider-types/{kind}                              GET  (model, environment, tool, web, trace)

/api/v1/workspaces/{ws}/sessions                           GET, POST, GET/PATCH .../{id}
/api/v1/workspaces/{ws}/threads                            GET, POST (create + first submission)
/api/v1/workspaces/{ws}/threads/{thread}                   GET, PATCH; POST .../archive
/api/v1/workspaces/{ws}/threads/{thread}/inbox             GET, POST (submit); PATCH/DELETE .../{entry}; PUT .../order
/api/v1/workspaces/{ws}/threads/{thread}/environments      GET, POST; DELETE .../{name}
/api/v1/workspaces/{ws}/threads/{thread}/runs              GET
/api/v1/workspaces/{ws}/runs/{run}                         GET, PATCH (labels only)
/api/v1/workspaces/{ws}/runs/{run}/interrupt, /fork   POST
/api/v1/workspaces/{ws}/runs/{run}/items                   GET   display snapshot
/api/v1/workspaces/{ws}/runs/{run}/events                  GET   SSE from ?cursor=
/api/v1/workspaces/{ws}/runs/{run}/attempts                GET; GET .../{attempt}/trace
/api/v1/workspaces/{ws}/usage                              GET   aggregated from usage_records
```

Conventions:

- Collections are plural nouns; items are `/{id}`; an operation that is not CRUD is `POST /{id}/{verb}` with a verb in the imperative (`archive`, `interrupt`, `fork`, `set-default`, `test`, `redeliver`). No colon verbs, no `{action}` parameters.
- Ids in paths are ids. Workspace addressing first resolves an exact ID; otherwise a key must identify exactly one workspace globally before authorization. Zero matches return `not_found`; multiple matches return `conflict` asking for an ID, without exposing candidate metadata or selecting an accessible match. Agent and Skill keys resolve within the resolved workspace. Resolution is bounded and precedes ordinary permission/confinement checks. Accessible-workspace collection rows carry allowed verbs for UI hints; the server still authorizes each operation.
- Every response body is a JSON object. Collections are `{"items": [...], "next_cursor": "..." | null}`. Cursors are opaque; `?limit=` is 1 to 200, default 50.
- Timestamps are RFC 3339 in UTC with microseconds. Ids and enum values are lowercase.

External envd registration and use follow [06: envd over HTTP](06-environments.md#envd-over-http). This iteration exposes no envd pairing, connection-ticket, presence or WebSocket ingress API.

## Authentication

`Authorization: Bearer <api_key>` or the session cookie set by `/auth/login`. Unauthenticated requests to protected routes are `401 unauthenticated`. Login/reset and OAuth callback are narrowly public flows with their own one-use state checks; `/auth/*` is not a blanket exemption. Cookie mutations validate CSRF. Cookie-only `GET /auth/session` returns the authenticated profile and stable session CSRF token with `Cache-Control: no-store`; API-key authentication, including mixed key/cookie requests, cannot use it. Stream authorization completes in a short session before opening the response and is periodically refreshed.

Every API key is confined to exactly one workspace. `POST /users/me/keys` requires a non-null `workspace_id`; service-account key issuance derives it from the workspace route and checks the account's home workspace. Login sessions support organization management; API keys do not. Key management, shared-resource access and issuance obey [03's workspace confinement rules](03-tenancy.md#flows) and [authorization rules](03-tenancy.md#authorization), including when a route is outside `/workspaces/{ws}`.

## Preconditions

Mutable-resource PATCH/PUT/DELETE and state-changing operations such as set-default, archive and revoke require the resource's strong `If-Match`; missing is 428 and stale is 412. Thread inbox edits/reorder and desired mount edits use the thread ETag. Only pending entries can be edited/withdrawn; withdrawal retains a tombstone and request key. There is no delete-and-reuse-key loophole. Assigned/consumed/failed entries return conflict.

Appending a message or feedback requires no ETag. Interrupt names an exact run and is idempotent by its state contract. Fork names an immutable completed/waiting origin. Tests/probes are repeatable operations without a mutation key.

## Idempotency

Only the following v1 commands promise request-key replay. Do not advertise generic idempotency on resource creates whose schema provides no evidence:

| Command                                                                                        | Evidence / key namespace                                                                          |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| New thread plus first input, existing-thread submission (message or feedback), fork plus input | Original entry, unique `(workspace_id, principal_id, request_key)` shared across these operations |
| Internal spawn                                                                                 | Child thread, unique origin run/tool call                                                         |
| Child result                                                                                   | Original result entry, unique sealed child run; outbox has the same dedupe identity               |

Execution creation commands require `Idempotency-Key`. Entry evidence stores operation kind, requested target and canonical request digest. Same key/different intent returns 409 conflict. Authentication/current scope permission precede lookup; replay lookup precedes mutable state/precondition validation. Responses return the same created IDs with current status (200 instead of 201), not a promise to replay byte-for-byte an old response. Pending edits never change the original digest.

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
  "options": {"labels": {}, "max_usage": {"requests": 200},
              "mcp_headers": {"conn_…": {"X-Conversation": "C0123"}}}
}
-> 201 {"entry": {...}, "run": {...} | null}
```

Inbox POST accepts two public kinds, both requiring the thread's `run` verb and an `Idempotency-Key`:

| Kind       | Request fields                                                              | Purpose                                                                                                  |
| ---------- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `message`  | Required `payload` and `agent_id`; optional revision/options and `delivery` | Ordinary input, including replies to user questions                                                      |
| `feedback` | Required `waiting_run_id`; optional `answers` list, default empty           | Approval decisions and client-tool results for that exact control wait; execution settings are inherited |

Each supplied answer carries `tool_call_id` and a typed action: `approve`, `reject` (optional reason), or `complete` (client-tool result). Feedback accepts no message `payload`, agent selection, options or delivery mode. A question UI submits its answer as a message. The service validates and normalizes feedback into the stored entry payload under [05's waiting rules](05-runs.md#waiting-interrupt-and-fork); missing waiting targets are rejected, never inferred. For example:

```json
{
  "kind": "feedback",
  "waiting_run_id": "run_…",
  "answers": [{"tool_call_id": "call_…", "action": "approve"}]
}
```

`POST /threads` takes the message body plus `session_id` (or none, to create a session) and returns the thread, the entry and the run. Feedback resolves existing waiting work; it does not create a new thread.

Messages require an explicit agent. [05](05-runs.md#source-selection) owns source eligibility, including question-only and mixed waits; its [incorporation rules](05-runs.md#assignment-and-incorporation) own steer delivery and input state transitions. [04](04-resources.md#caller-headers) owns header validation and the effective-header comparison. The receipt reports pending/assigned/consumed/failed/withdrawn and owning run, rather than promising immediate execution. Ordinary inbox-full responses use `rate_limited` with the count/byte limit and occupancy in details; pending edits that exceed the byte allowance leave the entry unchanged. Control feedback uses the [capacity exception and its own payload bounds](05-runs.md#inbox-capacity).

## Resource scope

Provider listing authorizes a requested workspace and returns its providers plus shared ones; organization-wide enumeration requires organization authority. Create requires an explicit workspace ID or explicit NULL with organization write permission. Path/filter scope is checked before object lookup, including on idempotent replay and content routes.

## Streams

`GET /runs/{run}/items` composes a bounded page of canonical run-owned input entries with event-folded attempt segments. It returns `inputs`, `next_input_cursor`, execution segments, `display_version`, execution checkpoint cut, current attempt, outcome, opaque cursor and `complete`. It describes the last durable display plus database lifecycle. A newly accepted run exposes its canonical input with empty execution segments. The input page byte budget is an aggregate pagination target, not a smaller per-message limit: an accepted message larger than that budget occupies a page by itself, with complete content and a cursor for later entries. The supported inbox byte ceiling still bounds any single entry.

Snapshot sequences describe confirmed object writes, not inbox confirmation. The existing inbox read exposes the same typed input payload/status projection, identified by entry ID across attempts. It excludes execution-only options and secrets; row visibility does not imply incorporation. Input disposition is returned from PostgreSQL under [05's receipt rules](05-runs.md#assignment-and-incorporation). Completed/waiting views use the exact snapshots selected at seal; failed/cancelled views show retained observations with the database outcome, as defined in [07](07-facts-and-delivery.md#checkpoints-and-display-snapshots).

`GET /runs/{run}/events?cursor=` returns SSE. Data frames wrap AG-UI events with attempt and event sequence; `Last-Event-ID` is the opaque run/attempt cursor. Protocol control frames are `reset`, `retry_later` and `closed`, with a required view version/retry hint. The client replaces provisional state on reset, preserving the durable interrupted segments returned by `/items`. It never follows a stale producer based solely on its claimed attempt number. Gap/Redis-loss handling is specified in [07](07-facts-and-delivery.md#the-run-stream).

Workspace `/events` and `/events/live` expose the same workspace-scoped committed order. Retention gaps have an explicit boundary; they require resource refresh, not a silent jump. Lifecycle, display, checkpoint and collection cursors are distinct types and cannot be substituted for one another. Streaming endpoints hold no database session while idle.

## OpenAPI

The service serves `/api/v1/openapi.json`; `make service-contract-generate` writes it to `proto/a13n-service/openapi.json` and regenerates the Console client. Tags are the packages: `auth`, `tenancy`, `agents`, `skills`, `environments`, `models`, `connections`, `secrets`, `assets`, `subscriptions`, `runs`. [11](11-transition.md#package-placement-and-build-boundary) owns archival of the old exports and the generation boundary for the new HTTP/event contracts and wire fixtures.
