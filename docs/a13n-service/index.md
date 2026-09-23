# Service

The Service rewrite supports administrator bootstrap, secure login, workspace API keys, model-provider and model configuration, immutable agent revisions, and durable agent execution with Remote MCP tools through the Harness. Public reads combine canonical input content with durable execution observations. Console supports configuration, conversations and tool observations; additional resource capabilities remain under implementation.

Use a new PostgreSQL database, Redis and a shared local object directory accessible to the control and worker processes. Keep these separate from old Service deployments. With an explicit configuration file, run:

```sh
a13n-service --config service.toml migrate
a13n-service --config service.toml bootstrap --email admin@example.com
a13n-service --config service.toml run --role control
a13n-service --config service.toml run --role worker
```

Bootstrap prompts for a password of at least 12 characters. It creates one organization, default workspace and administrator; a second invocation refuses without changing data. The password is stored as Argon2id. Configure HTTPS directly through the server TLS settings or terminate HTTPS at trusted ingress: browser login uses secure cookies and CSRF protection.

Control serves the API and scans for expired attempts and queued successors. Workers claim accepted runs within local capacity, renew their leases independently of model execution, persist checkpoints and display, record correlated usage, and seal outcomes. `run --role all` combines these roles. All/control optionally migrate on startup under a bounded PostgreSQL advisory lock; workers never migrate and refuse an incompatible schema.

`/healthz` reports liveness. `/readyz` checks runtime tasks, database/schema and Redis within a deadline. `/api/v1/openapi.json` describes the implemented API. Configure a provider, model and agent, then submit a message to the workspace's thread collection; use the returned run ID to read its status and items. Repeating the same submission with its `Idempotency-Key` returns the original accepted input.

Run `make live-test` for a disposable CLI/bootstrap/HTTPS/control/two-worker journey with a deterministic HTTP model. This proves the implemented path, not the entire planned feature and recovery matrix. Environments, additional resources and remaining lifecycle operations are under development.

See [configuration](configuration.md), [generated settings](configuration-reference.md) and [HTTP reference](api-reference.md). Independent SDKs and the remote CLI need new-contract updates in their owning repositories.

## Remote MCP tools

Create a workspace Connection from Console's Connections page, or `POST /api/v1/workspaces/{workspace_id}/connections`. The current provider is `mcp`; authentication supports `none`, `bearer`, `headers` and [personal OAuth accounts](#personal-oauth-accounts). Supply a URL in `config.url` and optionally an exact `config.tools` allowlist. Bearer credentials use `{"token":"..."}`; custom authentication uses `{"headers":{"X-API-Key":"..."}}`. Credentials are encrypted with the configured key ring and never returned by resource reads. Editing requires the resource's current ETag in `If-Match`.

Test the saved Connection to discover its tools. For Connections without OAuth, the tools read endpoint caches discovery for 30 seconds by Connection version; an explicit test always refreshes it. Execution prepares tools from the live server and refreshes the same non-authoritative cache. OAuth discovery uses the current person’s grant and does not use the shared cache. Cache loss does not prevent execution. In the Agent editor, select the Connection and the exact tools the Agent may use, then save a revision. Revisions retain these references and selections; they do not copy credentials or endpoint configuration. Worker execution checks current permissions, cancellation and Connection availability before external work. Editing a Connection during an established execution stops its further use; a new attempt opens a fresh client with current configuration. Disabling a Connection also stops active use.

HTTP destinations obey the deployment's endpoint allowlist. MCP initialization and discovery have a 10-second deadline; calls have a 30-second deadline. Responses are bounded to 256 KiB, with no redirects, inherited proxies or compressed responses. Discovery accepts at most 128 tools, and an Agent selects at most 32 Connections. Each attempt owns separate clients and sessions.

Callers can submit `options.mcp_headers`, keyed by Connection ID, for noncredential context such as `X-Conversation`. Header names normalize to lowercase. Entries must belong to the selected Agent; names used by Connection authentication and reserved transport/security headers are rejected. Values must be printable ASCII without control characters. Each Connection accepts at most 32 headers and 8 KiB; the combined header names and values are limited to 16 KiB. This context is retained in run options and checkpoints, so authentication belongs in the encrypted Connection credential. Steers must match the active revision's effective context; irrelevant inherited context does not make them incompatible.

`config.recovery_retry_safe_tools` is an explicit writer assertion, defaults to empty, and must be a subset of explicit `config.tools`. An Agent cannot widen it. Remote tool hints cannot grant replay safety. Endpoint or credential changes clear prior declarations unless the writer explicitly reconfirms them in the same edit. After a worker dies with an unresolved tool call, ordinary tools receive a recorded failure explaining that their effects may have occurred. Only freshly declared retry-safe tools are eligible for automatic replay. A declaration does not guarantee exactly-once effects: a provider must actually enforce any deduplication contract. Service sends stable correlation in MCP request `_meta["a13n.service"]`, including `operation_id`, logical `run_id`, `connection_id` and original `tool_call_id`; the operation identity excludes the replacement attempt.

Console displays tool arguments and results from durable run observations and preserves them across reload. Remote MCP supports personal OAuth accounts as described below. Composio and other planned tool providers remain under development.

### Personal OAuth accounts

Remote MCP Connections support preregistered OAuth clients. Register the Service callback at the provider, then configure the Connection's issuer, client ID, requested scopes and public-client/Basic/POST authentication. Confidential client secrets are encrypted on the Connection. Each person uses **Connect account** to authorize their own account; a Connection writer cannot read another person's grant. Dynamic client registration and private-key client authentication are not supported.

Set `oauth.callback_url` to the externally reachable `/api/v1/oauth/callback` URL, and add exact Console Connection-page URLs to `oauth.return_urls`. Do not infer these values from incoming Host headers. Production uses HTTPS; disposable local deployments need explicit outbound HTTP exceptions. The initiating browser must retain its secure flow cookie through the provider redirect. API-key clients can initiate within their workspace but must retain that cookie too; this does not imply a companion CLI OAuth implementation.

The Service discovers protected-resource and issuer metadata, uses S256 PKCE, and sends a resource-bound code exchange. It supports optional refresh tokens and optional access-token expiry. Expired tokens refresh on use only when the issuer and grant support it. Concurrent users of one authorization join a single claimed refresh. If a rotating-token request loses its response, the Service requires reconnection instead of reusing the old refresh token. Control monitors operation deadlines even without keep-alive. Disconnecting, reconnecting or editing OAuth identity fences outstanding results. A failed tool call is never automatically replayed just because authorization failed.

Connection status shows whether your account is connected, pending, disconnected or requires reconnection. After returning from the provider, reopen the Connection to inspect the current status. Saved tests and Agent tool selection use your own grant. **Disconnect account** removes the local grant and blocks subsequent use; it does not claim to revoke access remotely at the issuer. The CLI disables raw HTTP access logging to keep callback codes/state out of logs; deployment proxies must also redact OAuth callback query strings.
