# Connect your application

Call an Agent on an existing Service from your application or a shell. You need the Service URL, a workspace API key, and an Agent configured with a working model. If the platform is not ready yet, start with [Service quickstart](get-started.md); to configure or try an Agent in Console, see [Use an existing platform](use-platform.md).

## Choose an integration

For application code or shell workflows, choose a [Service SDK or the remote CLI](sdks.md) and follow its repository-owned quick start. The curl examples below illustrate the Service HTTP boundary without duplicating those client guides.

## Set up credentials

Create an API key under **Workspace settings → My API keys** and export it with the Service URL. Requests with the key act in its workspace, so their paths name no workspace:

```sh
export A13N_URL=http://127.0.0.1:8080 A13N_API_KEY=a13n_...
```

Your account needs the **runner** role or higher to run an existing Agent. Creating an Agent also requires **builder** or **admin**. The key uses your account's current permissions in its workspace. For an application operated by a team, an administrator can create a dedicated [service account](identity.md#service-accounts). Keep the key in your server-side configuration; do not put it in browser code.

## Select an Agent

If an Agent is already configured, find its ID in Console or list the agents available to your key:

```sh
curl "$A13N_URL/api/v1/agents" -H "Authorization: Bearer $A13N_API_KEY"
```

Otherwise, create an Agent with the key of a configured model, such as `gpt-5.5` (`GET /api/v1/models` lists them). Replace the model key below with one from your workspace:

```sh
curl -X POST "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Helper", "config": {"model": "gpt-5.5", "instructions": "Answer briefly."}}'
```

## Start a conversation

Use the Agent's `id` (`ap_…`) from the list or creation response in place of `ap_...` below. Generate an idempotency key once for this submission and reuse it if you retry after a lost response. Generate a new key for a different submission:

```sh
IDEMPOTENCY_KEY=$(uuidgen)
```

Start a conversation with its first message:

```sh
curl -X POST "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -d '{"agent_id": "ap_...", "payload": {"content": [{"type": "text", "text": "What is a13n?"}]}}'
```

## Read the result

The response holds the new `thread`, the message's inbox `entry`, and the `run` when execution has been accepted. Replace `run_...` below with the returned run ID. If the submission is queued without a run, follow the [thread stream](agents-and-runs.md#follow-a-thread-stream) to observe acceptance and execution.

Read the run until its `status` is `completed`, `waiting`, `failed` or `cancelled`; a completed run's answer is in `output`:

```sh
curl "$A13N_URL/api/v1/runs/run_..." -H "Authorization: Bearer $A13N_API_KEY"
```

Instead of polling, follow the [thread stream](agents-and-runs.md#follow-a-thread-stream) or subscribe to [webhooks](files-and-webhooks.md#webhooks). Continue the conversation with `POST …/threads/{thread_id}/inbox`; see [Agents, threads and runs](agents-and-runs.md).

A `waiting` run needs an approval, answer, or client tool result; follow [Waits, approvals and questions](agents-and-runs.md#waits-approvals-and-questions). For a failed run, inspect its result and [troubleshoot the run](monitoring.md#troubleshoot-a-request-or-run). Disconnecting your client does not cancel remote execution.

## Next steps

- Choose a [Service SDK](sdks.md) for language-specific setup, streaming, and error handling.
- Read [HTTP conventions](http.md) for authentication, pagination, conditional writes, and errors.
- Use the [HTTP reference](api-reference.md) for request and response schemas.
- Match your client's supported Service contract to your deployed version; see [version ownership](sdks.md#keep-version-ownership-clear).
