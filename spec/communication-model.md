# Communication Model

## Design Position

Foundation exposes four distinct caller-facing delivery surfaces for Run
interaction, online resource wake-up, standard AG-UI presentation, and durable
server-to-server lifecycle notification. They share Foundation application
authority and selected source facts, but they do not share a public envelope,
cursor, replay promise, or delivery acknowledgement.

This document is the comparative reading overview for Native Run SSE, the
Native notification WebSocket, Hosted AG-UI, and Foundation Hook Webhooks. It
does not redefine their wire schemas or source events. The linked owning
contracts remain authoritative when a summary here omits protocol detail.

[A2A](foundation-service/23-a2a.md) owns its separate Task streaming and Push Notification
contract. [External Connectivity](foundation-service/40-connectivity/README.md) owns provider
ingress, Connector-backed actions, Remote MCP, and the a13n MCP. Model,
Environment, object-storage, telemetry, and other service egress are outside
this caller-delivery overview.

## Boundaries

| Concern                                                      | Owner                                                                                             | Relationship to this overview                                                                  |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Public protocol composition and shared application authority | [Protocol Gateway](foundation-service/15-protocol-gateway.md)                                     | Establishes that protocol adapters do not create another Run or authorization authority        |
| Native Run SSE and notification WebSocket                    | [Native Streaming and Notifications](foundation-service/21-native-streaming-and-notifications.md) | Owns HTTP/WebSocket surfaces, replay attachment, topics, and client recovery                   |
| Hosted AG-UI                                                 | [Hosted AG-UI](foundation-service/22-hosted-ag-ui.md)                                             | Owns standard AG-UI input, bindings, event visibility, lifecycle projection, and hosted replay |
| Run Stream and retained presentation                         | [Lifecycle and Stream Persistence](foundation-service/24-lifecycle-and-stream-persistence.md)     | Owns the stable Run-scoped Redis Stream, Run Stream cursor, Items, and retained snapshot       |
| Source-authority and external delivery separation            | [Events, Usage, and Delivery](foundation-service/25-events-usage-and-delivery.md)                 | Owns the delivery envelope and separation among observations, facts, and destination progress  |
| Hook names, subscriptions, and Webhook eligibility           | [Hook Notifications](foundation-service/26-hook-notifications.md)                                 | Owns the Hook registry, scope matching, inline subscriptions, and Webhook behavior             |
| Durable publication and retry                                | [Durable Operations and Outbox](foundation-service/06-durable-operations-and-outbox.md)           | Owns PostgreSQL Outbox atomicity, claims, retries, dead letter, and redrive                    |

A transport cursor, external ID, notification ID, or delivery receipt grants no
resource authority. Every attachment, subscription change, resource read, and
command uses the current Principal and the owning authorization action.

## Surface Overview

| Surface                       | Transmitted information                                                                                                                                                            | Delivery semantics                                                                                                                             | Purpose for the caller                                                                                                        |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Native Run SSE                | Detailed observations for one Run: text, public reasoning, tool calls and results, Harness custom observations, Item closure, Environment attachment, and final outcome projection | Ordered within the Run Stream, bounded replay, possible duplicate delivery, explicit replay gaps; disconnect never cancels the Run             | Render a Native Agent's detailed live execution, output, tools, Environment progress, and diagnostics                         |
| Native notification WebSocket | Lightweight Thread-, Run-, pending-action-, and Session-change wake-ups                                                                                                            | Best effort, no acknowledgement, cursor, replay, or ordering guarantee; notifications can be duplicated, coalesced, delayed, or dropped        | Invalidate an online UI cache and prompt current resource or lifecycle reads                                                  |
| Hosted AG-UI                  | Standard AG-UI input and `BaseEvent` output plus a finite Foundation custom-event registry                                                                                         | Independent Hosted cursor and bounded replay; external `runId` is idempotent; disconnect never cancels the Run                                 | Let a standard AG-UI client invoke and render a Foundation Agent without consuming Native event shapes                        |
| Foundation Hook Webhook       | Committed Run and RunAttempt lifecycle facts selected by a durable HookSubscription                                                                                                | Signed asynchronous HTTP delivery through PostgreSQL Outbox; at least once and unordered; bounded retries, dead letter, and authorized redrive | Drive server-side callbacks, workflow orchestration, durable state synchronization, offline notification, and audit reactions |

## Native Run SSE

Native Run SSE is the detailed interaction surface for one Run. It carries
bounded `RunStreamEvent` values and preserves the underlying Run Stream cursor.
It is not the Workspace lifecycle feed and does not turn a process-local
Harness observation into durable Run authority.

### Text and Public Reasoning

| Event type                       | Meaning                                                                                        | Caller use                                                              |
| -------------------------------- | ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `agui.text_message_start`        | An Assistant text part begins                                                                  | Create an in-progress Assistant message or content part                 |
| `agui.text_message_content`      | A bounded visible Assistant text delta arrives                                                 | Append streamed text                                                    |
| `agui.text_message_end`          | The text part ends without claiming Run completion                                             | Close the corresponding text part                                       |
| `agui.reasoning_message_start`   | A policy-permitted public reasoning part begins                                                | Create a public reasoning presentation part                             |
| `agui.reasoning_message_content` | A policy-permitted public reasoning delta arrives                                              | Append the visible reasoning projection                                 |
| `agui.reasoning_encrypted_value` | A policy-permitted encrypted reasoning value is observed; it is not plaintext chain of thought | Preserve it only in a client that supports the selected protocol policy |
| `agui.reasoning_message_end`     | The public reasoning part ends                                                                 | Close the corresponding reasoning part                                  |

### Tool Calls and Harness Terminal Observations

| Event type              | Meaning                                                                                                                                         | Caller use                                                                                 |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `agui.tool_call_start`  | A complete model-requested tool call begins AG-UI presentation; this is not authorization or dispatch evidence                                  | Create a tool-call presentation                                                            |
| `agui.tool_call_args`   | Policy-permitted public tool arguments are projected                                                                                            | Display or update tool input                                                               |
| `agui.tool_call_end`    | The requested tool-call presentation closes; this is not proof that dispatch succeeded                                                          | Mark tool-input presentation complete                                                      |
| `agui.tool_call_result` | A successful public tool return is observed; this is not a provider receipt                                                                     | Display the visible tool result                                                            |
| `agui.run_finished`     | The current Harness Run emits a successful terminal observation after cleanup; it is not independently durable Foundation Run completion        | Close the current Harness execution presentation while reconciling authoritative Run state |
| `agui.run_error`        | The current Harness Run emits a failed or cancelled terminal observation after cleanup; it is not independently `run.failed` or `run.cancelled` | Display the bounded execution error while reconciling authoritative Run state              |

The final retained Native stream observation projects the authoritative sealed
Run outcome. Connection closure alone never proves completion. A planned
RunAttempt handoff emits no false `agui.run_finished`, `agui.run_error`, or
synthetic cancellation and keeps the stable Run Stream open.

### Harness and Pydantic Custom Observations

These observations use the outer `agui.custom` event and preserve the exact
custom name and public source meaning.

| Custom name               | Meaning                                                                                                                                           | Caller use                                                                                  |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `a13n.harness.run_result` | Harness suspends with deferred calls or approvals and emits a bounded public summary; complete continuation authority remains in sealed Run state | Show that the interaction is waiting, then read the authorized pending contract             |
| `a13n.harness.lifecycle`  | A model-request boundary starts, completes, or fails                                                                                              | Show model-request progress                                                                 |
| `a13n.harness.invocation` | Managed-tool preparation, authorization, approval, dispatch, result-safety, or unknown-outcome progress is observed                               | Show detailed tool-execution progress without treating it as a durable invocation ledger    |
| `a13n.harness.delegation` | Inline or Host-managed subagent delegation changes                                                                                                | Show child or subagent activity                                                             |
| `a13n.harness.usage`      | Harness emits a bounded usage report                                                                                                              | Show provisional run usage while durable usage records remain authoritative for attribution |
| `a13n.harness.context`    | A bounded source-owned Context or Environment-topology observation changes                                                                        | Update the visible run context                                                              |
| `a13n.harness.state`      | Harness emits a public revisioned state delta; it is not a durable Foundation state mutation                                                      | Update a runtime-state presentation                                                         |
| `a13n.harness.recovery`   | Internal ModelAttempt recovery enters interruption, backoff, restart, exhaustion, or cancellation                                                 | Show automatic recovery progress                                                            |
| `a13n.harness.diagnostic` | Harness emits bounded safe implementation detail                                                                                                  | Support authorized run diagnostics                                                          |
| `a13n.pydantic_ai.*`      | Another public Pydantic AI observation has no standard AG-UI mapping                                                                              | Preserve the selected public custom observation for a client that understands it            |

### Item Projection

| Event type         | Meaning                                               | Caller use                                                             |
| ------------------ | ----------------------------------------------------- | ---------------------------------------------------------------------- |
| `item.completed`   | A semantic Item closes successfully                   | Finalize the corresponding message, tool, plan, or other semantic unit |
| `item.failed`      | A semantic Item closes with a presentation failure    | Mark the semantic unit failed without inferring Run failure            |
| `item.interrupted` | An Item remains incomplete at the closed Run boundary | Mark the semantic unit incomplete or interrupted                       |

### Environment Attachment

| Event type                   | Meaning                                                                                                                    | Caller use                                                                                        |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `environment.entry.started`  | The fenced RunAttempt begins attaching to its accepted Environment target                                                  | Show that Environment connection is in progress                                                   |
| `environment.entry.ready`    | The fresh adapter attaches and exposes a bounded safe capability and readiness summary                                     | Show that the Environment is ready                                                                |
| `environment.entry.failed`   | Construction, authorization, connection validation, attachment, or compatibility fails without exposing target credentials | Show the safe Environment failure and await the authoritative Attempt outcome                     |
| `environment.adapter.closed` | The process-local adapter completes or fails its non-destructive close                                                     | Update attachment presentation without claiming that the external target stopped or was destroyed |

### Delivery Signals

| Signal                       | Meaning                                                                      | Caller use                                                                                                                 |
| ---------------------------- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `a13n.foundation.replay_gap` | Requested delivery history is no longer completely retained                  | Rebuild from current Run, Item, and pending-action reads rather than treating surviving stream entries as complete history |
| SSE heartbeat comment        | Connection liveness only; it carries no product state and advances no cursor | Maintain the attachment without changing local Run state                                                                   |
| SSE close                    | The current delivery attachment ended                                        | Reconcile the Run; never infer cancellation or completion from transport closure                                           |

## Native Notification WebSocket

The Native notification WebSocket carries only coarse product wake-ups. It
never carries prompt or message text, reasoning, tool arguments or results,
pending response schemas, Artifact content, credentials, or internal execution
identity.

| Topic                    | Meaning                                                                 | Caller use                                                         |
| ------------------------ | ----------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `thread.updated`         | Thread version, current Run, or selected head may have changed          | Refresh a conversation page, Thread list, or current-Run selection |
| `run.updated`            | A correlated Run lifecycle or summary may have changed                  | Read the current Run and, when needed, its lifecycle collection    |
| `pending_action.updated` | An authorized waiting-Run pending projection may require reconciliation | Refresh approval, client-tool, or deferred-interaction UI          |
| `session.updated`        | A Session list or summary may have changed                              | Refresh Workspace Session navigation or summaries                  |

After a disconnect the caller reconnects, resubscribes, and reconciles through
Workspace lifecycle events and current resource reads. The WebSocket is a
low-cost freshness hint, not a completeness path.

## Hosted AG-UI

Hosted AG-UI maps standard `RunAgentInput` into the same Foundation application
authority and emits standard AG-UI `BaseEvent` values. Its external Run
lifecycle comes from durable Foundation acceptance and sealed outcome rather
than a process-local Harness terminal observation.

### Standard Event Families

| Event family                    | Meaning                                                                                                    | Caller use                                                                |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Run lifecycle                   | `RUN_STARTED` follows durable Run acceptance; `RUN_FINISHED` and `RUN_ERROR` follow the sealed Run outcome | Drive the standard AG-UI Run state machine                                |
| Text message lifecycle          | Standard Assistant message start, content, and end events                                                  | Render streamed Agent output                                              |
| Client-visible tool lifecycle   | Standard tool-call and tool-result events permitted by the selected Revision                               | Render tool activity and results                                          |
| Optional registered projections | Policy-selected state, message snapshot, activity, subagent, and safe reasoning-summary events             | Enrich a compatible AG-UI client without exposing private execution state |

### Foundation Custom Events

| Custom event                 | Meaning                                                                                                                                                                          | Caller use                                                                                                                               |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `a13n.foundation.run_status` | A safe durable Run-status projection. A waiting Run carries the complete authorized pending contract and closes the current attachment without emitting false success or failure | Render accepted, running, waiting, cancellation, or another supported safe status and submit later feedback under a new external `runId` |
| `a13n.foundation.artifact`   | A stable authorized result projection. An explicitly published Asset is represented without an object key or bearer URL                                                          | Present or retrieve an authorized result or Asset                                                                                        |
| `a13n.foundation.replay_gap` | Hosted delivery history is unavailable                                                                                                                                           | Reconcile from current Foundation state instead of continuing from an arbitrary surviving event                                          |

Hosted AG-UI never forwards raw `a13n.harness.*` fallback, raw
`a13n.pydantic_ai.*` observations, encrypted reasoning values, provider frames,
unprocessed exceptions, or RunAttempt, Worker, Redis, and internal Harness Run
identities.

## Shared Run Stream Source

For one Foundation Run, Worker and Harness observations enter one stable,
tenant-scoped, Run-scoped Redis Stream. Every RunAttempt and replacement
Harness Run for that Foundation Run continues the same source Stream.

Control consumes that shared Run Stream and creates protocol-specific public
projections. Native Run SSE exposes `RunStreamEvent` values and preserves the
source Redis Stream cursor. Hosted AG-UI filters and converts the shared source
into standard `BaseEvent` values, then assigns an independent Hosted delivery
cursor and retained projection. Native and Hosted cursors are not
interchangeable.

The accepted contract therefore defines one shared source Run Stream, not one
source Redis Stream key per public protocol. It does not require the retained
Hosted projection to use another Redis Stream or prescribe that projection's
storage key.

## Foundation Hook Webhook

Foundation Hook Webhooks deliver only committed Run and RunAttempt lifecycle
facts. Live token deltas, tool-stream observations, Items, diagnostics,
Environment-attachment observations, and telemetry never create Webhook
Outbox intents.

### Run Lifecycle Hooks

| Hook            | Meaning                                                      | Caller use                                                            |
| --------------- | ------------------------------------------------------------ | --------------------------------------------------------------------- |
| `run.accepted`  | Complete Run acceptance commits                              | Create or confirm an upstream task and begin asynchronous tracking    |
| `run.running`   | The first RunAttempt is leased and the Run leaves `accepted` | Mark the upstream task as running                                     |
| `run.waiting`   | Deferred outcome and complete waiting state seal             | Notify a user or business system that authorized feedback is required |
| `run.completed` | Output and final Run state seal successfully                 | Retrieve or synchronize final output and Artifacts                    |
| `run.failed`    | A terminal failure seals the Run                             | Apply failure handling, alerting, or compensation                     |
| `run.cancelled` | Authorized cancellation seals the Run                        | Synchronize the durable cancellation outcome                          |

### RunAttempt Lifecycle Hooks

| Hook                    | Meaning                                                                                               | Caller use                                                                                           |
| ----------------------- | ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `run_attempt.leased`    | A Worker generation claims or replaces the current fenced Attempt                                     | Observe scheduling, takeover, or recovery without changing Run state                                 |
| `run_attempt.running`   | Harness Run identity and start commit                                                                 | Observe the concrete Attempt start                                                                   |
| `run_attempt.succeeded` | The current Attempt commits the owning Run outcome                                                    | Record Attempt success and its bounded usage summary                                                 |
| `run_attempt.yielded`   | The owner confirms a safe checkpoint and voluntarily releases execution authority                     | Observe planned handoff without mistaking it for Run completion                                      |
| `run_attempt.failed`    | Preparation, execution, lease replacement, or fenced publication makes the generation terminal failed | Monitor Worker- or Attempt-level failure while allowing Run recovery semantics to remain independent |
| `run_attempt.cancelled` | Cancellation makes the current Attempt generation terminal                                            | Observe the Attempt cancellation and its Run correlation                                             |

### At-Least-Once Delivery

Every Foundation outbound Hook Webhook uses the shared PostgreSQL Outbox. The
owning resource transition, immutable lifecycle event, and one Outbox row per
matching subscription Revision commit in one short transaction. A rollback
therefore leaves none of them committed, while a successful source transaction
cannot lose its publication intent merely because the destination is down.

A Control or all-in-one publisher claims due Outbox rows under a generation and
lease, sends the signed HTTP request outside the claim transaction, and records
the result in another short transaction. Any successful HTTP `2xx` acknowledges
destination receipt; success is not limited to `200`. A retryable failure,
timeout, unknown response, publisher crash, or expired claim leaves the stable
delivery identity eligible for retry. Exhausted policy moves the row to
`dead_lettered`; authorized redrive reuses the original source and delivery
identity.

If the receiver applies a request but its response is lost, Foundation can send
the same delivery again. The receiver therefore validates signature and
freshness, durably deduplicates by delivery or source identity, and records the
event before returning `2xx`. It applies contiguous `resource_seq` values and
uses the resource lifecycle API to fill a gap. This yields at-least-once network
delivery, not exactly-once business processing.

All Foundation Hook Webhooks use this PostgreSQL Outbox path; there is no
best-effort outbound Hook Webhook. A2A Push Notification is a separate protocol
but also reuses the durable Outbox. The lower-reliability Native notification
WebSocket is not a Webhook. Provider ingress Webhooks from Slack, Lark, GitHub,
and other sources flow in the opposite direction and are outside this outbound
delivery statement.

### Inline HookSubscription Lifecycle

| Stage                               | Behavior                                                                                                                                                                       |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Run command admission               | Foundation validates exact Hook names, destination, managed signing-Secret authority, and request bounds                                                                       |
| Queued submission                   | The inline configuration remains unaccepted editable intent; no HookSubscription or Outbox row exists                                                                          |
| Immediate or delayed Run acceptance | Foundation creates the active exact-Run HookSubscription head and immutable Revision before the matching `run.accepted` lifecycle event and Outbox row in the same transaction |
| Run execution                       | Matching selected Run and RunAttempt lifecycle facts create independent Outbox rows for the exact subscription Revision selected at source commit                              |
| Pause, update, or deletion          | The new head state controls future matching; already committed Outbox rows retain their original immutable subscription Revision and delivery identity                         |
| Run terminal outcome                | The exact-Run scope becomes naturally quiescent after its final matching facts, while already committed deliveries continue retry, dead-letter, or redrive independently       |

Run completion does not automatically pause, delete, or destroy an inline
HookSubscription. It is an ordinary durable HookSubscription whose filters are
assigned to the accepted Run. Referenced subscription Revisions remain retained
while an Outbox row can still be delivered or redriven.

## Invariants

1. Native Run SSE, Native notification WebSocket, Hosted AG-UI, and Hook
   Webhook remain separate public delivery contracts.
2. Native Run SSE is the detailed interaction surface; the notification
   WebSocket is only a resource freshness wake-up.
3. Hosted AG-UI and Native Run SSE project one shared Run Stream source but use
   different public event shapes, visibility policy, cursors, and replay
   contracts.
4. Harness terminal observations do not independently establish durable Run
   completion.
5. Only committed eligible lifecycle facts create outbound Hook Webhook
   delivery intents.
6. Every outbound Hook Webhook uses PostgreSQL Outbox and is at least once;
   callers must deduplicate and reconcile sequence gaps.
7. Disconnecting or failing any caller delivery transport never cancels, seals,
   or rewrites the owning Run.
