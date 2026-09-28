# Use an existing platform

Use this guide when your team already runs Service and you want to talk to an Agent in Console. You need the Console URL and an account with permission to run agents in a workspace. If you are setting up the platform itself, start with [Deploy your first platform](get-started.md).

Console is the browser application for Service. Harness UI is a separate local Agent application and is not needed for these steps.

## Sign in and select a workspace

1. Open the Console URL supplied by your administrator.
2. Sign in, or accept your administrator's invitation and follow the account setup steps. There is no public self-service sign-up.
3. Select the workspace where your team has configured its agents.

You need the **runner** role or higher to start conversations. A **builder** can also create agents and configure resources; an **admin** manages access. If you can view an Agent but cannot run it, ask your administrator to check your [role](identity.md#roles).

## Try an Agent

1. Open **New conversation** and pick an Agent your team has configured.
2. Send a concrete request that matches its purpose. For a basic assistant, try: “Turn these notes into two next steps: draft ready; review pending; release planned for Friday.”
3. Watch the response as it streams. Depending on the Agent and model, the conversation may also show reasoning and tool calls.
4. Send a follow-up, such as “Make the first step more specific.” Continue in the same conversation so the Agent can use its history.

You should see a response to the first request and a follow-up that uses the previous discussion. The Service stores the conversation so you can return to it later. If the run fails, use its details and the [troubleshooting guide](monitoring.md#troubleshoot-a-request-or-run), or share the run ID with your platform administrator.

## Create your own Agent

If you have the builder or admin role:

1. Confirm a model is available under **Models**. If needed, [add a model](get-started.md#add-a-model) first.
2. Open **Agents → Create agent**, give it a name, choose the model, and write its instructions. For a first Agent, try: “Help turn project notes into short, concrete next steps.”
3. Save it, then choose **Try agent** and send the example request above.

Agent configurations are versioned. Start with a model and instructions, then add [tools and connections](tools.md), [skills](skills.md), or [subagents](agents-and-runs.md#subagents) as the task requires. See [Agents](agents-and-runs.md#agents) for version management.

## Work with questions, approvals, and files

- **Answer a question or approval request:** respond in the conversation when the Agent asks. An approval can be accepted or rejected; the Agent's tool permissions determine when approval is required.
- **Guide or stop work:** send a message while the Agent is working to steer it, or stop the active work from the conversation.
- **Use file and terminal tools:** a builder must configure an [environment](environments.md) for the Agent. A model connection alone does not provide a filesystem or command runner.
- **Remember information across conversations:** configure [memory](memory.md) and attach it to the Agent. History within one conversation and shared memory serve different purposes.

For the underlying lifecycle, read [Core concepts](../core-concepts.md) and [Agents, threads and runs](agents-and-runs.md).

## Next steps

- [Agent Composer](agent-composer.md) helps you configure agents through a conversation.
- [Resource basics](resources.md) explains the resources an Agent can use.
- [Connect your application](connect-application.md) takes the same platform into an API integration.
