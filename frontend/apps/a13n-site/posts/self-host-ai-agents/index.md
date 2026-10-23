---
title: Self-host AI agents with Docker Compose
description: Run a13n Service with Docker Compose, connect a model provider, and try an agent in the browser. Includes data storage and shared-deployment requirements.
date: 2026-10-23
authors: [a13n team]
---

a13n Service runs agents on your own infrastructure. It serves a browser Console and an HTTP API, stores conversations, and keeps agent work running when a client disconnects.

This guide takes you from an empty directory to a conversation with an agent using Docker Compose. It also covers where the stack stores data and what to configure before sharing the deployment. The [Service quickstart](/docs/a13n-service/get-started/) maintains the detailed setup instructions.

## Choose what you need to host

Service is the starting point when a team or application needs a shared agent deployment. It manages identities, saved conversations, and execution. Console is the browser application served by that deployment.

If you want to run an agent inside your Python process, start with [Harness](/docs/a13n-harness/getting-started/). Your application owns its persistence and access policy. If you want to work interactively in a repository from a terminal or browser, start with [Harness UI](/docs/a13n-harness-ui/setup/). These are different entry points into the same foundation; you do not need to deploy all three to try an agent.

Self-hosting Service does not automatically make model inference local. When you connect a hosted model provider, model requests still go to that provider using your account. Choose the model connection and execution environment according to where your data may go.

## Start the local deployment

You need Docker with Docker Compose and a model provider API key. This path does not require Python, Node.js, or a source build. Provider calls may incur charges.

Download the maintained Compose file into an empty directory:

```sh
mkdir a13n-service
cd a13n-service
curl -fL https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/deploy/docker/compose/a13n-service.yaml -o a13n-service.yaml
docker compose -f a13n-service.yaml up -d --wait --pull always
```

The stack starts three containers: Service, PostgreSQL, and Redis. Service serves Console and the API. It publishes Console at `http://127.0.0.1:8080` and keeps data in named volumes. The Compose file mounts the host Docker socket so Service can create Docker execution environments. Use a machine where that access is appropriate.

The source Compose file defaults to the published Service `latest` image. For a repeatable deployment, use the version-pinned Compose asset from the [Service release](https://github.com/converge-ai-labs/agent-foundation/releases). Read the instructions for the release you deploy; documentation on the site tracks `main`.

Open Console and register the first administrator account. There is no default administrator login. Keep this initial setup on loopback: the first person to register on an uninitialized deployment becomes its administrator. For a shared deployment, use the [operator bootstrap procedure](/docs/a13n-service/get-started/#initialize-a-shared-deployment) before exposing registration.

## Get a first agent response

1. Open **Models → Add model → Connect a new provider**. Select your provider, enter its API key, and connect it.
1. Select a model available to your account, check its upstream ID and API, and add it. The [model guide](/docs/a13n-service/models/) explains the supported connection types.
1. Open **Agents → Create manually**. Name the agent, choose the model, and give it simple instructions such as `Answer clearly and briefly. Suggest concrete next steps.`
1. Create the agent, select **Try agent**, and send a short message. Follow the streamed response in the conversation.

This first conversation needs no tools, memory, or execution environment. Keeping the first task small makes a provider or credential problem easier to isolate. Once the model responds, follow the [Console guide](/docs/a13n-service/use-platform/) to continue conversations, handle approvals, and work with files.

If the stack does not start, inspect it from the directory containing the Compose file:

```sh
docker compose -f a13n-service.yaml ps -a
docker compose -f a13n-service.yaml logs service
```

If Console works but the model does not respond, check the provider credentials, upstream model ID, and selected API. A model appearing in a catalog does not guarantee that your account can use it. The [quickstart troubleshooting section](/docs/a13n-service/get-started/#troubleshooting) also covers port conflicts and an existing administrator account.

## Connect an application

After the browser conversation works, create a workspace API key and select the agent from an application. Keep credentials in server-side configuration. The [application guide](/docs/a13n-service/connect-application/) walks through credentials, SDK selection, the first message, and follow-up messages.

Give each new submission an idempotency key and reuse that key when retrying the same submission after a lost response. A client disconnect stops observation, not the agent's work. Your application should distinguish a completed result from waiting, failed, or cancelled outcomes. A waiting result can require an answer, approval, or client-side tool result; it is not a successful final answer.

## Preserve data when you stop

To stop the stack and later resume with its existing data:

```sh
docker compose -f a13n-service.yaml down
docker compose -f a13n-service.yaml up -d --wait
```

Use the same Compose project and volumes. Removing volumes deletes the stored data. Back up the database, files, and encryption key together before operating a deployment you depend on; the [Compose operations guide](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#backups-and-upgrades) explains backup and upgrade handling.

Keeping stored conversations across restarts and recovering an interrupted execution are separate concerns. Before relying on recovery for a tool that changes an external system, read the [execution lifecycle](/docs/a13n-service/agents-and-runs/). A retry policy must account for external side effects as well as saved agent state.

## Move to a shared deployment

A working local conversation proves the basic connection between Service and your model. A shared deployment also needs a public URL, access setup, storage, backups, and an upgrade plan. Follow [Service configuration](/docs/a13n-service/configuration/) and the maintained [single-host Compose](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#single-host-deployment-with-native-docker) or [Kubernetes](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes) guide for that step.
