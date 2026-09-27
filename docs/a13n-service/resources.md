# Resource basics

Resources are what a tenant configures for its agents: providers and models, agents and skills, connections, secrets, environment templates, subscriptions and assets. This page covers what they have in common. The following pages cover each kind: [Models](models.md), [Tools and connections](tools.md), [Skills and secrets](skills.md), [Environments](environments.md) and [Files and webhooks](files-and-webhooks.md).

## Scopes

Business resources belong to the current workspace and use paths such as `/api/v1/agents` and `/api/v1/models`. An API key supplies its workspace; a login session sends `X-Workspace-ID`. See [workspace selection and references](http.md#workspace-selection-and-references).

Providers keep organization collections, `/api/v1/organizations/{organization_id}/{kind}-providers`, with an explicit `workspace_id`:

- `null` shares the provider with every workspace of the organization. Creating or changing it needs organization `write`; workspace members can read and use it. Manage shared providers under **Organization settings → Providers**.
- A workspace ID confines the provider to that workspace. Creating or changing it needs workspace `write`. Use **Workspace settings → Providers**.

Models always belong to one workspace. They may use a shared provider or their workspace's provider, and are managed on the workspace **Models** page. The scope of a resource never changes. A resource of another workspace answers `404` through the current workspace, even for an administrator of both.

Provider lists accept `?workspace_id=...` to return shared providers plus that workspace's providers; without it they include every workspace the caller reads. Other business collections list only the current workspace.

Agents, skills, models, memories, environment templates and secrets have immutable keys, unique per kind and workspace. Archive and disable retain the key. Physical deletion, where the resource's lifecycle permits it, releases the key. A replacement always has a new ID; saved references to the old ID stay bound to the deleted instance.

## Lifecycles

Resources follow one of two lifecycles.

**Live** resources (providers, models, connections, environment templates, secrets, subscriptions) change in place. Each change increments the row's `version`, and runs use the current state when they need the resource: a disabled provider or connection stops new use at once.

**Revisioned** resources (agents and skills) have a head and immutable, numbered revisions. Each change adds a revision; the head's `default_revision_id` selects the one new work uses, and runs pin the exact revision they started with. Heads are archived rather than deleted. Publishing content identical to the head's current default revision creates nothing: the request returns that revision again with `201`, and the head's version, ETag and audit trail are unchanged, whether or not the request set `make_default: false`.

Each kind has its own retirement operation:

| Kind                                 | Stop using it                                                                          |
| ------------------------------------ | -------------------------------------------------------------------------------------- |
| Providers, models                    | `PATCH` with `{"enabled": false}`.                                                     |
| Connections, environment templates   | `PATCH {"enabled": false}`. A connection's credential is removed with `POST …/revoke`. |
| Agents, skills                       | `POST …/archive`; `POST …/unarchive` reverses it.                                      |
| Assets                               | `DELETE` retires the asset; its content stays readable for history.                    |
| Secrets, subscriptions, environments | `DELETE`.                                                                              |

## Common conventions

- **IDs and keys.** Every resource has a kind-prefixed ID, such as `ap_…` for agents. Agents, skills and models also have a `key` (`^[a-z0-9][a-z0-9_-]{0,127}$`) unique in their collection; agent and skill paths accept the ID or the key. Changing an agent's or skill's key breaks links that used the old key, while runs and revisions refer to IDs.
- **Versions.** Reads return an `ETag`; changes require it in `If-Match`. See [HTTP conventions](http.md#concurrency-control).
- **Labels.** Agents and skills carry up to 32 `labels`. List them with `?label=key:value` (repeatable, all must match), `q` (case-insensitive substring of key, name or description) and `archived=true|false`.
- **Authors.** Views carry `created_by_id`, `updated_by_id`, `created_at` and `updated_at`.
- **Audit.** Changes to resources are recorded in the [audit trail](identity.md#audit). An update that changes nothing keeps the version and records nothing.

## Providers

A provider is one configured account of an external service. There are five kinds:

| Kind          | Types                                                                                                                                                                                          | Used for                                               |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `model`       | `openai`, `anthropic`, `google_gemini`, `google_vertex`, `azure_openai`, `aws_bedrock`, `openrouter`, `ollama`, `alibaba_model_studio`, `deepseek`, `moonshot`, `minimax`, `zhipu`, `typesafe` | [Models](models.md)                                    |
| `web`         | `duckduckgo`, `brave`, `exa`, `parallel`, `tavily`, `firecrawl`, `jina`, `perplexity`, `serpapi`, `tinyfish`                                                                                   | The [web toolset](tools.md#web-search-and-scrape)      |
| `environment` | `docker`, `e2b`, `daytona`, `modal`, `vercel`, `sprites`, `runloop` (and `local` when the deployment enables it for development)                                                               | [Environments](environments.md)                        |
| `connector`   | `composio`                                                                                                                                                                                     | [Connector connections](tools.md#composio-connections) |
| `memory`      | `mem0_platform`, `mem0_oss`                                                                                                                                                                    | [Record memories](memory.md#record-memories)           |

`GET /api/v1/provider-types/{kind}` describes each installed type: its `configuration_schema` and `credential_schema` (JSON Schema), when a credential is required, and a `setup_url` for obtaining one. Model types add their model APIs and per-API settings schemas; web types list the operations they serve; environment types describe their environment schema and whether they support managed instances, stop and destroy. Console builds its provider forms from this endpoint.

Create a provider with its scope, type, name, configuration and credential:

```sh
curl -X POST "$A13N_URL/api/v1/organizations/$ORG/model-providers" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"workspace_id": "'"$WORKSPACE"'", "type": "openai", "name": "OpenAI", "config": {}, "credential": {"api_key": "sk-..."}}'
```

- **Credentials are write-only.** They are encrypted with the deployment's key ring and never returned; views show only `credential_configured`. In a `PATCH`, a `credential` value replaces it, `null` removes it, and leaving it out keeps it.
- **A credential is bound to its configuration.** A `PATCH` that changes `config` while a credential is stored must also replace or remove the credential (and every stored extra header of a model provider), or it is refused as `invalid_argument`. This keeps a credential from being sent to an endpoint it was not entered for.
- **Endpoints obey the deployment's outbound policy.** Base URLs and other endpoints must pass the [endpoint policy](configuration.md#outbound-requests); private and plain-HTTP endpoints need the operator's explicit allowance.
- **Disable** with `PATCH {"enabled": false}`. A disabled provider refuses new use with `disabled`; existing environments of a disabled environment provider are still maintained.
- **Test** with `POST …/{provider_id}/test` (needs `run`). It sends one inexpensive probe with the stored configuration and returns `succeeded`, `failed` (with a message) or `unsupported`. Web providers and the `google_vertex`, `aws_bedrock`, `typesafe` and `local` types have no test. An environment provider test only reads: it checks the Docker Engine or the Envd daemon's identity, and creates nothing. A memory provider test lists one page of a namespace no memory uses.

Providers have no delete operation; disable them instead.
