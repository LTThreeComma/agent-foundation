# Service

The Service runs agents on your servers for people and applications to use together. Configure agents and resources in Console, or connect your application through the authenticated HTTP API. Conversations are saved, work continues when you disconnect, and interrupted execution can recover after a process failure. See [Run and maintain](operations.md) for deployment and recovery details.

Use the Service when several people or applications share agents, credentials and history. For a personal terminal agent, use [Harness UI](../a13n-harness-ui/index.md); to run agents inside your own Python process, use [Harness](../a13n-harness/index.md) directly.

Integrate through the [SDKs and remote CLI](sdks.md), or use the [HTTP API](http.md) directly. Client-specific installation and examples live with each independent SDK repository.

## Start here

| Your situation                                | Guide                                              |
| --------------------------------------------- | -------------------------------------------------- |
| You are setting up a new platform             | [Deploy your first platform](get-started.md)       |
| Your team already runs Service                | [Use an existing platform](use-platform.md)        |
| You want to call an Agent from an application | [Connect your application](connect-application.md) |

For a short introduction through one conversation, read [Core concepts](../core-concepts.md). The concepts below describe what you configure and use on the platform.

## Concepts

| Concept                        | What you use it for                                                                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **Organization and workspace** | Organize your team and its work. A workspace contains the agents, resources, and conversations its members use.                             |
| **Role**                       | Decide what a user or application can view, run, configure, or administer.                                                                  |
| **API key**                    | Let an application access one workspace with the current permissions of its user or service account.                                        |
| **Provider and model**         | Connect a model provider, then choose a model for your Agent. Other providers supply services such as environments, search, or memory.      |
| **Agent and version**          | Combine a model, instructions, and tools into an assistant. Save configurations as versions and choose which version new conversations use. |
| **Session and thread**         | A session groups conversation threads. Each thread holds one discussion; forks let you explore a different answer within the session.       |
| **Run**                        | Follow one execution of an Agent: see its progress, answer a question or approval request, and inspect the result.                          |
| **Environment**                | Give an Agent a sandbox or computer where it can read files and run commands.                                                               |
| **Memory**                     | Make saved information available across conversations, using files or records the Agent can read and update.                                |
| **Tool and connection**        | Give an Agent actions it can perform. Connections provide access to external tools through MCP or an app account.                           |
| **Skill**                      | Add reusable instructions and supporting files for a task.                                                                                  |
| **File and webhook**           | Exchange files with an Agent, or notify your application when a run changes state.                                                          |

Read [Resource basics](resources.md) to configure these resources, [Identity and access](identity.md) for permissions, and [Agents, threads and runs](agents-and-runs.md) for API operations and lifecycle details.

## How the pieces fit

1. Configure an Agent with a model, instructions, and the tools it needs.
2. Start a conversation in Console or send a message through the API. The Agent works on your request and streams its response. Messages can wait when the Agent is busy.
3. If the Agent asks a question or requests approval, submit your answer or decision so it can continue.
4. Review the result and send a follow-up in the same conversation. You can return to saved conversations later.

For application integration, follow [Connect your application](connect-application.md). For deployment roles, storage, and failure recovery, read [Run and maintain](operations.md); use [Monitor and troubleshoot](monitoring.md) to investigate execution problems.

## Guides

| Goal                                                                 | Guide                                                                                                                                                                                                           |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deploy, create the first administrator and hold a first conversation | [Get started](get-started.md)                                                                                                                                                                                   |
| Manage members, roles, invitations, API keys and service accounts    | [Identity and access](identity.md)                                                                                                                                                                              |
| Configure agents and run conversations through the API               | [Agents, threads and runs](agents-and-runs.md)                                                                                                                                                                  |
| Let an agent configure agents with you                               | [Agent Composer](agent-composer.md)                                                                                                                                                                             |
| Configure resources shared by agents                                 | [Resource basics](resources.md), [Models](models.md), [Tools and connections](tools.md), [Skills](skills.md), [Environments](environments.md), [Memory](memory.md), [Files and webhooks](files-and-webhooks.md) |
| Build a client                                                       | [HTTP conventions](http.md) and the [HTTP reference](api-reference.md)                                                                                                                                          |
| Configure and operate a deployment                                   | [Configure Service](configuration.md), [Run and maintain](operations.md), [Settings reference](configuration-reference.md)                                                                                      |

## Interfaces

- **Console** is the browser application for the Service. Deployments serve it on the same origin as the API.
- **HTTP API**: every operation is under `/api/v1`. A running Service publishes its OpenAPI document at `/api/v1/openapi.json`; the [HTTP reference](api-reference.md) is generated from it.
- **Specification**: the Service's accepted contracts start at the [Service overview specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-service/00-overview.md).
- **`a13n-service`** is the server and operator command. Language SDKs and the companion remote command-line client are developed in independent repositories of [converge-ai-labs](https://github.com/converge-ai-labs); check each one for the Service versions it supports.
