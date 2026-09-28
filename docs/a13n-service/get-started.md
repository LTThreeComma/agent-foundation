# Deploy your first platform

Deploy Service, create the first administrator, and connect a model. You will then be ready to run your first Agent in Console or call it from an application.

Already have a running platform? Go directly to [Use an existing platform](use-platform.md) or [Connect your application](connect-application.md). For an introduction to the terminology, read [Core concepts](../core-concepts.md).

## Deploy the Service

The Service is published as the `a13n-service` Python package, the `ghcr.io/converge-ai-labs/a13n-service` image for `linux/amd64` and `linux/arm64`, and the Helm Chart `oci://ghcr.io/converge-ai-labs/charts/a13n-service`, each at the Service's version. Every deployment needs PostgreSQL, Redis and an encryption key; see [Configure Service](configuration.md#required-infrastructure). Two ready-made deployments are maintained in the repository:

- [Single host with Docker Compose](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose): the Service, PostgreSQL, Redis and Console on one machine, with Docker environments on the host's Docker Engine.
- [Kubernetes with Helm](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes): separate control and worker Deployments and a migration Job on any cluster, with values for a local kind cluster.

Follow either guide until the Service reports ready at `/readyz`.

## Create the first administrator

Open Console at the Service's public URL. Until the Service is initialized, Console asks for the first administrator's email and a password of at least 12 characters instead of a sign-in; creating it signs you in. This creates the first organization, its workspace and the administrator, once: afterwards Console shows the sign-in, and further people join through [invitations](identity.md#invitations).

Whoever reaches an uninitialized Service first becomes its administrator. When a new deployment is reachable by others before you open it, create the administrator with the operator command `bootstrap` instead, run where the Service's configuration is available (inside the Service container for the deployments above):

```sh
a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

It prompts for the password and prints the new organization, workspace and user IDs as JSON. Browser requests are accepted only from the Service's public origin; for a loopback public URL, `localhost` and `127.0.0.1` are interchangeable.

## Add a model

1. Open **Models → Add model → Connect a new provider**, choose the provider type (for example OpenAI or Anthropic) and enter its API key.
2. Choose a model from the model catalog, or enter an upstream model ID.

Outbound requests reject private addresses and plain HTTP by default. To use a model server on your own network, allow it first; see [outbound requests](configuration.md#outbound-requests). See [Models](models.md) for every provider type.

## Create and try an agent

Continue with [Create your own Agent](use-platform.md#create-your-own-agent) to create an assistant and verify a first response and follow-up in Console. Once it works, [invite your team](identity.md#invitations) with the roles they need.

## Use the API

Follow [Connect your application](connect-application.md) to create a workspace API key, select an Agent, submit a message, and read its result.

## Operate the platform

Before expanding the deployment, review [Configure Service](configuration.md), [Run and maintain](operations.md), and [Monitor and troubleshoot](monitoring.md). These guides cover deployment settings, maintenance, and diagnosing failed requests or runs.
