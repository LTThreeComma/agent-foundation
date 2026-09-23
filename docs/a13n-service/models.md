# Models

An agent calls a **model**: a named upstream model of a **model provider** account, with the model API to call it through, its capabilities and optional pricing. Providers and models are either shared by the organization or confined to one workspace; see [scopes](resources.md#scopes).

## Model providers

In Console, open **Models → Add model → Connect a new provider**, or manage providers under **Workspace settings → Providers** (workspace) and **Organization settings → Providers** (shared). Through the API, create a provider in `/api/v1/organizations/{organization_id}/model-providers` as described in [Providers](resources.md#providers).

| Type                                                               | Model APIs (default first)                                                        |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `openai`                                                           | `openai.responses`, `openai.chat_completions`                                     |
| `anthropic`                                                        | `anthropic.messages`                                                              |
| `google_gemini`, `google_vertex`                                   | `google.generate_content`                                                         |
| `azure_openai`                                                     | `openai.responses`, `openai.chat_completions`                                     |
| `aws_bedrock`                                                      | `bedrock.converse`, `bedrock_mantle.responses`, `bedrock_mantle.chat_completions` |
| `openrouter`                                                       | `openrouter.chat_completions`                                                     |
| `ollama`                                                           | `ollama.chat_completions`                                                         |
| `alibaba_model_studio`, `deepseek`, `moonshot`, `minimax`, `zhipu` | `openai.chat_completions`                                                         |
| `typesafe`                                                         | `typesafe.system_one`                                                             |

Each type's configuration and credential fields come from the Harness; `GET /api/v1/provider-types/model` returns them as JSON Schema. Most types take an optional `base_url` and an `api_key` credential. See [Harness models](../a13n-harness/models.md) and [model authentication](../a13n-harness/model-authentication.md) for each type's options.

A model provider may also carry up to 32 **extra request headers**, for gateways that route or bill by header. Header values are secrets: they are encrypted, never returned (views list only `header_names`), and edited per name in a `PATCH` (`"X-Team": "..."` sets a value, `null` removes it, omitted names are kept). Transport, authentication and protocol header names are refused.

## Add a model

In Console, open **Models → Add model**, choose the provider, then pick a model from the provider's catalog or enter its model ID.

Through the API, create the model in `/api/v1/organizations/{organization_id}/models`. Either name a catalog entry:

```sh
curl "$A13N_URL/api/v1/organizations/$ORG/model-providers/$PROVIDER/catalog" -H "Authorization: Bearer $A13N_API_KEY"

curl -X POST "$A13N_URL/api/v1/organizations/$ORG/models" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"workspace_id": "'"$WORKSPACE"'", "provider_id": "'"$PROVIDER"'", "key": "gpt", "name": "GPT", "catalog_key": "openai:gpt-5.5"}'
```

or describe it yourself with `config`:

```json
{
  "workspace_id": "ws_...",
  "provider_id": "mprov_...",
  "key": "local-llama",
  "name": "Llama (local)",
  "description": "Served by the team's Ollama host",
  "config": {
    "model_name": "llama3.3",
    "model_api": "ollama.chat_completions",
    "characteristics": {"capabilities": ["image_understanding"], "context_window_tokens": 131072},
    "max_tokens": 4096
  }
}
```

- `key` is unique per provider; agents select models by ID. Catalog keys have the form `<channel>:<model name>`, such as `openai:gpt-5.5`.
- `config.model_name` is the upstream model name, and `config.model_api` must be one of the provider type's model APIs. A catalog model starts with the type's default API; change it later with `PATCH`.
- `config.characteristics` declares the context window, context-management thresholds and `capabilities`: `image_understanding`, `video_understanding`, `audio_understanding`. Optional `max_tokens`, `temperature` and `top_p` are defaults that an agent's `model.settings` can override.
- `pricing` records the model's prices for usage records; catalog models include it. `pricing.model` must equal `config.model_name`.
- `enabled: false` creates the model disabled.

A model spends its provider's credential, so creating a model or changing its `config` needs `write` on the provider as well; models of a shared provider are managed with organization-scope grants. A workspace provider serves only its own workspace's models.

Change a model with `PATCH` (`name`, `description`, `config`, `pricing`, `enabled`) and its `If-Match`. Disable it with `{"enabled": false}`; models have no delete operation. Model-API-specific settings, such as reasoning effort, belong to the agent revision that uses the model (`model.settings`), where they are validated against the provider type's `settings_schemas`. These schemas leave out transport and raw request options (`extra_body`, `extra_headers`, `timeout`), upstream model selection and provider-account conversation state (such as `openai_previous_response_id`, `bedrock_inference_profile` or `openrouter_models`), and server-side tools a provider bills and runs outside the request, such as `openai_native_tools`, which `max_usage` cannot see: the settings form simply does not offer them.

A model's `config.model_api` must still be one the deployment offers; checking its settings, such as when an agent revision is saved, answers `503 unavailable` with `{"dependency": "model_api:<api>"}` for one it no longer does.

## Media understanding

An agent whose model cannot read images, video or audio can delegate that to another model. Each workspace can set a default per media kind, and each agent can choose its own.

In Console use **Workspace settings → Media understanding**. Through the API, workspace administrators replace all three defaults at once with the workspace's `If-Match`:

```sh
curl -X PUT "$A13N_URL/api/v1/workspaces/$WORKSPACE/media-understanding-defaults" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" -H "If-Match: $WORKSPACE_ETAG" \
  -d '{"image": "mdl_...", "audio": null}'
```

An omitted or `null` kind has no default. Each model must be usable in the workspace and declare the matching capability (`image_understanding`, `video_understanding` or `audio_understanding`).

At execution, an agent's own `media_understanding` choice wins and must be usable, or the run fails. A workspace default that is no longer usable is skipped with a warning, and that media kind is unavailable to the run.

## Usage and prices

Every model call of a run is recorded as a usage record attributed to the model that serves it, with a snapshot of the model's pricing at that time. See [usage](agents-and-runs.md#usage).
