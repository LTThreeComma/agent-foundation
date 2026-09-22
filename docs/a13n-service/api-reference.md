# Service HTTP reference

This reference is generated from the current new Service OpenAPI export. It covers authentication, model, Connection and agent configuration, durable run submission and observation, and operational probes. See [the Service guide](index.md) for the implemented boundary.

Download [the complete OpenAPI JSON](../assets/reference/service-openapi.json). This contract does not advertise legacy API or event schemas.

## agents

### `GET /api/v1/workspaces/{workspace_id}/agents`

List Agents.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: AgentPage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/workspaces/{workspace_id}/agents`

Create Agent.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `AgentCreate`.

Responses:

- **201** — Successful Response (`application/json: AgentView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/agents/{agent_id}`

Get Agent.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `agent_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: AgentView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions`

List Revisions.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `agent_id`     | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: RevisionPage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions`

Create Revision.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `agent_id`     | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `RevisionCreate`.

Responses:

- **201** — Successful Response (`application/json: RevisionView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions/{revision_id}`

Get Revision.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `agent_id`     | path     | true     | string        | —                       |
| `revision_id`  | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: RevisionView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions/{revision_id}/set-default`

Set Default.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `agent_id`     | path     | true     | string        | —                       |
| `revision_id`  | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: AgentView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

## auth

### `POST /api/v1/auth/login`

Password Login.

Request body: required.

- `application/json`: `LoginInput`.

Responses:

- **200** — Successful Response (`application/json: LoginOutput`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/auth/logout`

Session Logout.

Responses:

- **200** — Successful Response (`application/json: object`).

### `GET /api/v1/auth/session`

Session Profile.

Responses:

- **200** — Successful Response (`application/json: SessionProfile`).

### `GET /api/v1/users/me`

Me.

Responses:

- **200** — Successful Response (`application/json: Profile`).

### `POST /api/v1/users/me/keys`

Create Key.

Request body: required.

- `application/json`: `KeyInput`.

Responses:

- **201** — Successful Response (`application/json: KeyOutput`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces`

List Workspaces.

| Parameter | Location | Required | Type / schema  | Constraints and default            |
| --------- | -------- | -------- | -------------- | ---------------------------------- |
| `limit`   | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`  | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: WorkspacePage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}`

Get Workspace.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Workspace`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/audit-events`

Audit Events.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: AuditPage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

## connections

### `GET /api/v1/workspaces/{workspace_id}/connections`

List Connections.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ConnectionPage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/workspaces/{workspace_id}/connections`

Create Connection.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ConnectionCreate`.

Responses:

- **201** — Successful Response (`application/json: ConnectionView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/connections/{connection_id}`

Get Connection.

| Parameter       | Location | Required | Type / schema | Constraints and default |
| --------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id`  | path     | true     | string        | —                       |
| `connection_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ConnectionView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `PATCH /api/v1/workspaces/{workspace_id}/connections/{connection_id}`

Update Connection.

| Parameter       | Location | Required | Type / schema | Constraints and default |
| --------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id`  | path     | true     | string        | —                       |
| `connection_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ConnectionUpdate`.

Responses:

- **200** — Successful Response (`application/json: ConnectionView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/workspaces/{workspace_id}/connections/{connection_id}/test`

Test Connection.

| Parameter       | Location | Required | Type / schema | Constraints and default |
| --------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id`  | path     | true     | string        | —                       |
| `connection_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ConnectionTest`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/connections/{connection_id}/tools`

Connection Tools.

| Parameter       | Location | Required | Type / schema | Constraints and default |
| --------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id`  | path     | true     | string        | —                       |
| `connection_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ConnectionTest`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

## models

### `GET /api/v1/organizations/{organization_id}/model-providers`

List Providers.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `workspace_id`    | query    | false    | string or null | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ProviderPage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/organizations/{organization_id}/model-providers`

Create Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ProviderCreate`.

Responses:

- **201** — Successful Response (`application/json: ProviderView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/organizations/{organization_id}/model-providers/{provider_id}`

Get Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `provider_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ProviderView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/organizations/{organization_id}/models`

List Models.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `workspace_id`    | query    | false    | string or null | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ModelPage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/organizations/{organization_id}/models`

Create Model.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ModelCreate`.

Responses:

- **201** — Successful Response (`application/json: ModelView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/organizations/{organization_id}/models/{model_id}`

Get Model.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `model_id`        | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ModelView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/provider-types/model`

Provider Types.

| Parameter | Location | Required | Type / schema  | Constraints and default            |
| --------- | -------- | -------- | -------------- | ---------------------------------- |
| `limit`   | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`  | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ProviderTypePage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

## other

### `GET /healthz`

Health.

Responses:

- **200** — Successful Response (`application/json: object`).

### `GET /readyz`

Ready.

Responses:

- **200** — Successful Response (`application/json: schema-defined value`).

## runs

### `GET /api/v1/workspaces/{workspace_id}/runs/{run_id}`

Get Run.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `run_id`       | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: RunView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/runs/{run_id}/events`

Get Events.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `run_id`       | path     | true     | string         | —                       |
| `cursor`       | query    | false    | string or null | —                       |

Responses:

- **200** — Successful Response.
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/workspaces/{workspace_id}/runs/{run_id}/interrupt`

Interrupt Run.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `run_id`       | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: RunView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/runs/{run_id}/items`

Get Items.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `run_id`       | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `input_cursor` | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: RunItems`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/sessions`

List Sessions.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: SessionPage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/sessions/{identity}`

Get Session.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `identity`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: SessionView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/threads`

List Threads.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `session_id`   | query    | false    | string or null | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ThreadPage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/workspaces/{workspace_id}/threads`

Create Thread.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `NewThread`.

Responses:

- **201** — Successful Response (`application/json: Submitted`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/threads/{identity}`

Get Thread.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `identity`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ThreadView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox`

Get Inbox.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `thread_id`    | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: InboxPage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `POST /api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox`

Append Input.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `thread_id`    | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `Submission`.

Responses:

- **201** — Successful Response (`application/json: Submitted`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/threads/{thread_id}/runs`

List Runs.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `thread_id`    | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=200; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: RunPage`).
- **422** — Validation Error (`application/json: HTTPValidationError`).

### `GET /api/v1/workspaces/{workspace_id}/usage`

Get Usage.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `run_id`       | query    | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: UsageView`).
- **422** — Validation Error (`application/json: HTTPValidationError`).
