# Choose how to use Agent Foundation

For a shared agent platform on your infrastructure, start with Service. You can also embed Harness in a Python application or work interactively in Harness UI. Choose the path that matches what you need to operate.

## Try the platform locally

Use the **Service local quickstart** to evaluate Console and the API before setting up a shared deployment.

- **You need:** Docker with Docker Compose and your own model provider API key.
- **You get:** a local Service with a pre-created administrator, organization, and workspace. Connect your model to get a first Agent response.
- **Start here:** [Local quickstart](a13n-service/get-started.md).

The trial uses public credentials and only publishes loopback access. For a shared platform, follow the deployment path below with your own administrator credentials.

## Deploy a platform

Use **Service** when people or applications need shared agents, credentials, conversations, and managed worker execution.

- **You need:** a deployment host or Kubernetes cluster, PostgreSQL, Redis, an encryption key, and access to a model provider. The deployment guides describe the complete configuration.
- **You get:** a Service API and a Console deployment where administrators configure resources and users run agents.
- **Start here:** [Deploy your platform](a13n-service/get-started.md#deploy-the-service).
- **Then:** [Run and maintain](a13n-service/operations.md) and [Monitor and troubleshoot](a13n-service/monitoring.md).

## Use your team's platform

Use **Console** when your team already runs Service and you want to work with its agents.

- **You need:** the platform's Console URL, an account or invitation, and permission to run agents in a workspace.
- **You get:** conversations with configured agents, streamed responses, and a way to answer questions or approval requests.
- **Start here:** [Use an existing platform](a13n-service/use-platform.md).
- **Then:** configure your own [Agents](a13n-service/agents-and-runs.md#agents) and [resources](a13n-service/resources.md) if your role permits it.

## Connect an application

Use a **Service SDK or the HTTP API** when an application needs to submit work to agents running on Service.

- **You need:** a running Service, its URL, a workspace API key with the required permissions, and an Agent to call.
- **You get:** remote agent execution, stored results, streaming, and lifecycle webhooks.
- **Start here:** [Connect your application](a13n-service/connect-application.md).
- **Then:** choose a [language SDK or remote CLI](a13n-service/sdks.md). Client installation and language-specific examples live in those repositories.

## Embed an Agent in Python

Use **Harness** when your own Python process should construct and execute agents, and your application will manage users, persistence, and delivery.

- **You need:** Python 3.13 or later and `uv`; the offline quickstart works without a model API key.
- **You get:** reusable Agent construction, tools, continuation state, environments, and execution observations.
- **Start here:** [Harness Getting Started](a13n-harness/getting-started.md).
- **Then:** [Embedding in a Host](a13n-harness/hosting.md) for application-owned persistence and recovery.

The Harness SDK executes agents in your process. A Service SDK calls agents on a running Service. These are different integration choices; follow the guide for the execution model you need.

## Work interactively with an Agent

Use **Harness UI** as a terminal and browser workbench for individuals and trusted small teams, with its own configuration and conversations.

- **You need:** the [installation prerequisites](a13n-harness-ui/installation.md) and a supported model subscription or API key.
- **You get:** an Agent application without writing Python or deploying Service.
- **Start here:** [Harness UI](a13n-harness-ui/index.md).
- **Then:** [Configuration recipes](a13n-harness-ui/configuration-recipes.md).

Harness UI collaborators share one instance's credentials, configuration, and accessible files. It does not provide separate permissions for each participant or Service's durable worker execution. See [sharing and execution boundaries](a13n-harness-ui/index.md#sharing-and-execution-boundaries). Choose Service and Console when your team needs managed identities, workspace permissions, and execution recovery.

## Integrate individual components

| Need                                                       | Guide                                            |
| ---------------------------------------------------------- | ------------------------------------------------ |
| Portable file and command access, with or without an Agent | [Environments](environments/index.md)            |
| A daemon for environment operations                        | [Envd](a13n-envd/index.md)                       |
| AG-UI events from an embedded Harness application          | [Stream Protocol](a13n-stream-protocol/index.md) |
| Distribution names, imports, and example projects          | [Package catalog](packages.md)                   |

## Match the documentation to your version

This site follows `main`. Check your Service release and the [client's supported contract](a13n-service/sdks.md#keep-version-ownership-clear) before copying integration code. For Harness examples on `main`, use the locked source setup in its quickstart. Consult each component's release documentation and dependency metadata when using published packages.
