# Environments

An environment is the computer an agent works on: its files and terminal tools act there. The Service offers two kinds:

- **Managed environments**, which the Service creates, starts, stops and deletes from a **template**, such as a Docker container per conversation.
- **Registered devices**, computers you run yourself with the `a13n-envd` daemon, which the Service only connects to.

Threads **mount** environments. Each run freezes the thread's mounts when it starts and uses them until it ends. Without a mounted environment, an agent has no file or terminal tools.

In Console, **Environments → Templates** manages templates and **Environments → Instances** lists environments, and providers are under **Workspace settings → Providers → Environment**. All API paths below are under `/api/v1/workspaces/{workspace_id}` unless they say otherwise.

## Providers

An environment provider is the account environments run on. Create it like any other [provider](resources.md#providers), in `/api/v1/organizations/{organization_id}/environment-providers`, either shared by the organization or confined to one workspace. `GET /api/v1/provider-types/environment` returns each type's configuration, credential and recipe schemas.

| Type        | Kind                      | Configuration                                                                                                                                                        |
| ----------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `docker`    | Managed                   | `docker_host`, optionally a remote `tcp://host:port` or `https://host:port` engine the outbound endpoint policy allows. No credential.                               |
| `http_envd` | Registered devices only   | `endpoint` (the daemon's HTTP(S) origin), `request_timeout`, `initialization_timeout`, `max_in_flight`, `allow_plaintext_private_link`; credential `{"token": ...}`. |
| `local`     | Managed, development only | Offered only with `environments.allow_local`. Commands run directly on the worker host, which is no isolation boundary.                                              |

Docker is operator trust: an engine runs its containers as root on its host. An account that names no `docker_host` uses the operator's `environments.docker_host` (by default the Service process's own Docker environment); a value it does name must be a remote engine over TCP or HTTPS passing the outbound [endpoint policy](configuration.md#outbound-requests) — a unix socket, `ssh://` or other local address is refused, since it would reach a host the operator owns. A template's recipe binds host directories only below `environments.docker_mount_roots` (empty by default, so no host mounts are allowed); the recipe has no privileged mode, capabilities, devices, host namespaces, security options, volume or runtime choice, and refuses unknown fields. A recipe also cannot raise the worker's own buffers for that container (`max_file_bytes`, `max_output_bytes_per_stream`, `max_spool_bytes`, `max_output_preview_bytes`, `max_query_entries`, `max_concurrent_processes`) above the Harness defaults; container CPU, memory and process limits stay the operator's to bound on the Docker engine itself, for example with a dedicated engine set through `environments.docker_host`. The [Compose deployment](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/compose) mounts the host's socket for the operator's own engine, and the Kubernetes chart provides none. An `http_envd` endpoint must pass the same outbound policy. See [provider configuration](../environments/configuration-reference.md#httpenvdconnectionconfiguration) for every field.

Testing an environment provider (`POST …/{provider_id}/test`) fails with message `provider_unavailable` when its endpoint does not resolve; only a policy refusal answers `provider_endpoint_denied`.

## Templates

A template describes how to build a managed environment and when to stop and delete idle ones. Templates belong to a workspace; creating and changing them needs `write`.

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/environment-templates" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"key": "python", "name": "Python sandbox", "provider_id": "eprov_...",
       "config": {"recipe": {"image": "ghcr.io/converge-ai-labs/a13n-docker-environment:dev",
                             "cpus": 2, "memory_gb": 4, "init_script": "pip install pandas"},
                  "stop_after_seconds": 1800, "delete_after_seconds": 604800}}'
```

- `config.recipe` is validated by the provider type's recipe schema. For Docker it sets the `image`, host directory `mounts` (their sources must lie below `environments.docker_mount_roots`), `environment` variables, an `init_script`, `disable_network`, the `user`, and resource limits; see [`DockerEnvironmentConfiguration`](../environments/configuration-reference.md#dockerenvironmentconfiguration). The provider must support managed environments. A rejected recipe names each invalid field's location and the schema's own message, such as `mounts: Host mount sources must lie below a directory the operator allows`, never the value you sent.
- `stop_after_seconds` (default 1800, 60–2,592,000) stops an environment no run has used for that long. `delete_after_seconds` (60–31,536,000) deletes one that no run has used for that long and no thread mounts. `null` turns either off. Both count from the environment's last use.
- `PATCH` changes the name, description, provider, config and labels with the template's `If-Match`. A new provider or recipe applies to environments created afterwards: an environment is built from the template as it is when its creation is dispatched, and keeps that recipe. The idle policy always applies as currently set. Creating a template, or changing its provider or recipe, needs `write` on the provider, since it directs that provider's backend; changing only the idle policy needs to read it.
- `PATCH {"enabled": false}` stops new environments from the template; `{"enabled": true}` allows them again. Existing environments keep working.

To give every conversation of an agent its own environment, set the agent's `default_environment_template_id`. When a run of a thread without a `workspace` mount starts, the Service reserves a new environment from that template and mounts it as `workspace`.

## Instances

`GET …/environments` lists the workspace's environments, filtered by `status`; deleted ones appear only with `status=deleted`. Each has a `status`:

| Status                 | Meaning                                                                       |
| ---------------------- | ----------------------------------------------------------------------------- |
| `creating`, `starting` | Being created or started.                                                     |
| `ready`                | Usable.                                                                       |
| `stopping`, `stopped`  | Stopped environments keep their files; a run that mounts one starts it again. |
| `deleting`, `deleted`  | Being deleted, or gone. Deleted environments cannot be used again.            |

`failure` describes the last failed operation with `code`, `message`, `certainty` (`not_dispatched`, `known`, or `unknown` when the call may have taken effect) and `permanent`. Maintenance retries unfinished operations with the same operation ID. A permanent failure refuses new mounts and runs until its cause is fixed or the environment is deleted.

### Reserve a managed environment

Environments are usually reserved automatically for agents with a template. To create one yourself, for example to share it between threads:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/environments" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"template_id": "envtpl_...", "name": "Shared data"}'
```

It needs `run`, starts in `creating`, and becomes `ready` in the background. The template's idle policy applies to it like any other. A workspace holds at most `environments.managed_count` managed environments that are not deleted; a reservation beyond it, explicit or at run acceptance, is `409 conflict` with reason `environment_limit`.

### Register a device

Run the `a13n-envd` daemon over HTTP on the target computer, with a device ID and token (see [Connect to an existing HTTP daemon](../environments/remote-envd.md#connect-to-an-existing-http-daemon)), and add an `http_envd` provider with its endpoint and token. Then register the device:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/environments" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"provider_id": "eprov_...", "device_id": "build-box", "name": "Build box"}'
```

Registration needs `write`. The Service opens a session on the device to verify its endpoint, token and identity, then records it as `ready`. A registered device is **private**: only the person who registered it can mount it, and only they or a workspace administrator can rename or delete it. The Service never stops or deletes the device itself.

### Stop, rename and delete

These take the environment's `If-Match` and need `write`:

- `PATCH …/environments/{environment_id}` with `{"name": ...}` renames it.
- `POST …/stop` stops a `ready` managed environment that no run is using, and answers `202`; a later run starts it again. Otherwise it answers `409 conflict` with a reason such as `in_use`, `environment_stopped`, `connect_only` (a device) or `stop_unsupported`.
- `DELETE …/environments/{environment_id}` retires an environment that no thread mounts and no run uses, and answers `202`; otherwise it answers `409 conflict` with reason `mounted`, `in_use` or `operation_unresolved`. A managed environment is destroyed with its files. A registered device is only forgotten.

## Mount environments on a thread

A thread's mounts decide what its later runs use:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/threads/$THREAD/environments" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" -H "If-Match: $THREAD_ETAG" \
  -d '{"name": "data", "environment_id": "env_...", "working_directory": "/srv/data"}'
```

- `name` matches `^[a-z][a-z0-9-]{0,62}$`. The mount named `workspace` is the primary one and appears to the agent at `/workspace`; others appear at `/mnt/{name}`. `working_directory` is the mount's starting directory inside the environment.
- A thread mounts each name and each environment once. To replace a mount, remove it and add it again.
- A thread holds at most 32 mounts; a request beyond that is `409 conflict` with reason `mount_limit`. Acceptance may still add the agent's primary sandbox beyond this limit.
- Mount changes take the **thread's** `If-Match`, return the thread's new ETag, need `run`, and affect runs that start afterwards; a running run keeps its mounts. `GET …/threads/{thread_id}/environments` lists the mounts with the thread's ETag, and `DELETE …/threads/{thread_id}/environments/{name}` removes one.
- New threads and forks take initial mounts in their `environments` field. A fork shares its origin thread's mounts unless it sets `fresh_environments`. Archiving a thread removes its mounts.
- Several threads may mount the same environment and use it at once.

When a run starts, it waits up to `environments.wait_seconds` for its environments to be ready, starting stopped ones. If they do not become ready in time, the attempt fails and the run is retried within its attempt budget; an environment that can no longer be used, such as a deleted one, fails the run with `environment_unavailable`.

Async [subagents](agents-and-runs.md#subagents) get environments from their edge's policy: the parent run's mounts (`shared`), a new environment from a template (`dedicated`), or none.
