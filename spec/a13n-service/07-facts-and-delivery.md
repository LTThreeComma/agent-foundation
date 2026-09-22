# Facts, recovery state and delivery

There are three distinct products: immutable lifecycle/usage facts, resumable execution state, and user-visible observations. PostgreSQL owns lifecycle, execution authority and inbox disposition. The conditionally replaced run checkpoint owns resumable state; its receipts are reconciled into PostgreSQL while that run remains eligible. Object-backed payloads are immutable. Redis accelerates live display; it cannot advance execution history or decide that a run is finished.

## Events

```
event_cursors
  workspace_id  organization_id  last_seq  retained_from
  PRIMARY KEY (workspace_id)

events
  id  organization_id  workspace_id  seq  kind  entity_kind  entity_id  entity_seq
  mutation_id  session_id  thread_id  run_id  run_attempt_id NULL  actor_id NULL
  payload  occurred_at
  PRIMARY KEY (id)
  UNIQUE (workspace_id, seq)
  UNIQUE (entity_kind, entity_id, entity_seq)
  kind IN ('run.accepted','run.running','run.waiting','run.completed','run.failed','run.cancelled',
           'run_attempt.leased','run_attempt.running','run_attempt.succeeded','run_attempt.yielded',
           'run_attempt.failed','run_attempt.cancelled')
```

Every state-changing service transaction stages its events, then flushes them **as its last database phase**. It inserts the workspace cursor row if absent, locks it, advances `last_seq` by the batch length, inserts those events and matching webhook outbox rows, then commits. A transaction touches one workspace's event stream. No domain row lock may be acquired after the cursor lock; entity counters are already set under their locks.

`seq` is a **workspace-scoped transactional counter**, never a sequence/identity default. If transaction A holds position n, B cannot obtain n+1 in that workspace until A commits or rolls back. Thus a visible prefix cannot later acquire an earlier committed event. Rollback also rolls back allocation. Workspaces do not block each other's counters. Lifecycle transitions are serialized briefly per workspace; tokens, heartbeat and usage do not allocate lifecycle positions. Measure this cost with the acceptance load test.

`entity_seq` increments the owning run/attempt's `lifecycle_seq`, only on lifecycle transitions; it is not the workspace cursor. `mutation_id` groups one commit. Payload is a bounded, redacted public projection sufficient to understand the transition; large content uses authorized resource links, never secrets or raw credentials.

The previous `bigserial` plus `xact_id < xmin` rule is invalid: transaction-ID assignment order need not match sequence allocation order. PostgreSQL documents both mechanisms separately: [sequences](https://www.postgresql.org/docs/current/functions-sequence.html) and [snapshots](https://www.postgresql.org/docs/current/functions-info.html#FUNCTIONS-PG-SNAPSHOT).

Readers use `(workspace_id, seq > cursor)` in sequence order. HTTP and WebSocket have the same opaque workspace-bound cursor. With filters, the returned cursor is the last **examined** position, not just the last matching event. Page bounds and retention metadata come from one database snapshot; no session remains open while streaming.

### Retention and immutability

Retention deletes only a prefix and advances `retained_from` atomically under the cursor lock. Default lifecycle retention is 30 days. Pending/dead outbox rows keep their own frozen payload and event ID, so they do not pin the lifecycle log forever. A cursor older than `retained_from - 1` returns `invalid_cursor` plus the new boundary and instructions to refresh resource snapshots. Counters survive an empty retained log.

Triggers forbid update/delete of events, usage, audit and revision rows, and changes to terminal run/attempt facts. Run labels alone remain editable through a narrow whitelist including their version/timestamp; selected terminal checkpoint/display metadata, status and usage do not. Terminal attempts have no exemption. Confirmed input receipts/consumed payloads and run mount snapshots are also immutable. An object-only receipt cannot rewrite a failed/cancelled inbox disposition. Transition/pointer constraints supplement checks; a status-value CHECK alone does not restrict transitions.

Audit rows share one append-only table and recorder. Account-wide identity/credential facts have null organization and workspace scope under the explicit action/target rules in [03](03-tenancy.md#audit); tenant facts retain their actual scope. Mutation and success audit commit or roll back together.

Retention uses a separate least-privilege database role/connection with explicitly permitted deletion functions. A freely writable session setting is not authorization to bypass immutability. Online application roles cannot switch to the retention role. The migration's trigger count is an implementation result, not a target. Resource heads/revisions are archived and retained in v1; no automatic revision purge guesses reachability from JSON configuration. A future revision purge must first provide complete dependency evidence for pinned skills/subagents/templates.

## Usage records

```
usage_records
  id  organization_id  workspace_id  run_id  run_attempt_id  harness_run_id
  call_id  digest  record  model_id NULL  price_snapshot NULL  ingested_at
  PRIMARY KEY (id)                         -- stable Harness usage record ID
```

Ingestion accepts reports from known authenticated attempts, including expired/terminal ones. Foreign keys and correlation checks must match their organization/workspace/run; being exempt from the worker fence is not permission to forge another tenant's usage. Duplicate IDs with equal canonical digests are no-ops; differing content is an integrity error, quarantined/logged and not silently overwritten. This path is idempotent even when the run is sealed.

`runs.usage_at_seal` is explicitly the total visible to the sealing transaction. `GET /usage` computes totals from records, including late arrivals. It does not pretend the sealed summary will be corrected. A crash before a provider report is durably ingested can leave an unknown charge; ingestion guarantees preservation of received facts, not discovery of every billable provider operation.

The call context records provider/model identity and the price snapshot used at dispatch, or an explicit unknown price. Displayed estimated historical cost uses that snapshot; changing current `models.pricing` does not rewrite history. Usage remains raw evidence; a budget policy (#410) reads it through the seam in [09](09-runtime.md#extension-points), correlated by `call_id`.

## Objects

Each run has one replaceable execution object, `orgs/{org}/runs/{run}/state.json`, and one replaceable display object, `orgs/{org}/runs/{run}/display.json`. Sequence numbers live inside the objects; there is no file per checkpoint or display sequence. Retain the existing local/S3 conditional-write protocol, including an opaque version token that changes on every replacement, even a writer-claim update with unchanged logical content.

Payloads remain immutable: `orgs/{org}/runs/{run}/output/{digest}`, `orgs/{org}/entries/{entry}/payload/{digest}` and `orgs/{org}/uploads/{upload}`. An upload is referenced in place when it becomes a skill package or asset. Content-addressed entry payloads let a pending edit select different bytes without overwriting an earlier version. A `*_ref` column stores an immutable payload key; the API expands it to `ObjectRef(key, digest, size, content_type)`. There is no object catalog table.

Immutable payload publication uploads outside any session, verifies durable acknowledgement with the expected digest and size, then commits the owner's reference. Conditional create refuses different bytes at an existing key. An uncertain write is reconciled by reading back and comparing the exact bytes and metadata. Failure to commit the reference can leave an unused object.

**No object reclamation in v1**: no `collect_objects`, age-based upload expiry, orphan inventory, object-delete outbox or physical history/asset purge. Unused uploads and abandoned payloads remain stored; upload limits still apply. Mutable run snapshots avoid accumulating a full copy per checkpoint. Bucket version history, if enabled, has separate deployment storage costs; the application promises one logical key per snapshot, not physical reclamation by the backend. Lifecycle-event/outbox SQL retention is separate and remains as specified above and below.

Asset retirement blocks new selection; content stays readable to authorized readers of retained input and output. Cross-workspace asset references are refused at submission. Large tool outputs use assets rather than unbounded snapshot or event payloads. A future reclamation design must arbitrate with publication before enabling deletion; v1 introduces no state machine for it.

## Checkpoints and display snapshots

The checkpoint envelope carries a format version, Harness state, accepted revision/options digest, cumulative current-run input receipts, checkpoint/attempt numbers and any waiting/completed outcome candidate. The Service validates its host receipt capability; it does not invent a Harness API that returns delivered entry IDs. [05](05-runs.md#assignment-and-incorporation) owns incorporation.

Create initial state with create-only semantics; each later checkpoint conditionally replaces the same key using the exact version read with its predecessor. A confirmed object write makes the checkpoint durable. A later short, fenced transaction confirms its inbox receipts; there is no atomic transaction spanning the object store and PostgreSQL. Each checkpoint replacement retains that run's prior receipts. An object-only outcome candidate permits recovery, not lifecycle completion.

On takeover, validate database authority, read and validate the complete current object, then CAS its writer fence to the new attempt number without advancing semantic progress. Revalidate authority afterwards. The confirmed claim's content and new version token become the recovery baseline; never attach a fresh token to stale bytes. An older fence or stale object token is rejected. Claim and writes use bounded timeouts with lease monitoring active and no database session across I/O. If a response is lost, compare the intended bytes, digest, writer fence and object version on readback before accepting success or retrying. A competing publication is not the caller's successful receipt. The storage adapters must preserve this conditional-write contract, including protection against identical-content replacements reusing a token.

After a writer claim, follow [05's receipt reconciliation and terminal-race rules](05-runs.md#assignment-and-incorporation) before delivery or Harness execution. Object writes cannot mutate database lifecycle or inbox disposition. A PUT already in flight may finish after authority loss; only a later authorized attempt can adopt its state while that run remains recoverable.

Visible user input is projected from canonical inbox entries, with stable entry identity, position, current assignment and disposition. Inbox and run-item reads reuse the same typed public payload projection; execution options, credentials and private metadata are excluded. Run views include only entries currently assigned to that run, including consumed or failed entries retaining that assignment; entries returned to pending belong to the inbox until assigned again. Control and child inputs retain kind-specific presentation rather than becoming ordinary user messages.

Execution display is folded from Harness events, never reconstructed from compacted message history. Service-owned input content uses native `display:false` metadata so delayed or recovered Harness input events cannot duplicate the canonical input projection. An emitted-prefix marker proves only that already-emitted execution observations reached the publisher; user-input visibility does not depend on a future native event. The worker maintains an ordered display value with attempt segments and conditionally replaces `display.json` periodically, on byte pressure, and at finalization. A snapshot records its `display_seq`, attempt, last included per-attempt event sequence, and latest observed execution checkpoint. It uses the same version-token and writer-claim rules as execution state. One worker-local publication task serializes both objects and captures immutable bytes before upload; concurrent event folding cannot mutate the bytes or cursor being published.

Before publishing an execution checkpoint, durably flush display through its corresponding cut, then replace `state.json` recording that display sequence/attempt. Display-only flushes can advance further without claiming resumable progress. There is no atomic two-object publication: a crash between writes leaves display ahead of execution, which recovery already supports. A replacement attempt claims both objects before publishing either; a CAS conflict requires validated readback. Never trim Redis past a confirmed display write or advance execution past an unconfirmed display cut. Prove this two-object ordering with real storage; the old checkpoint code alone is not that proof.

On recovery, load the last execution checkpoint and newest durable display independently. Keep already durable observations from the failed attempt and mark its segment interrupted at the recorded execution cut; they are observations, not evidence that their effects are in the restored history. New output uses `(attempt_number, item_id)` identities in a new segment. Provisional content after the latest **display** snapshot can disappear on reset. The UI shows the recovery boundary and does not concatenate interrupted text as if it were one successful answer. Compaction never deletes already durable display items.

Completed/waiting seal selects the exact checkpoint and display digests, sequences and formats in `runs.sealed_checkpoint` / `sealed_display`. The local publisher is drained before seal and cannot write afterwards. Reads and fork/continuation validate that exact selection; they never silently adopt different bytes at the fixed key. A successor run writes its own objects, not its parent's. Failed/cancelled runs select no new continuation state. Their remaining object bytes, including an in-flight write completed after seal, are diagnostic observations and cannot override lifecycle or inbox disposition.

`GET /runs/{run}/items` composes the durable display with database status/current attempt and terminal outcome. Active snapshot sequences come from the objects, not a database progress cache; validate identity, format, writer fence and display-cut consistency. If reads straddle a replacement, retry within a bounded deadline; never pair an execution cut with older display. A failed/cancelled view marks the interrupted attempt and treats any retained tail only as observations. `complete` means execution sealed, not “all provisional Redis content was made durable”. Snapshot payload bytes and in-memory pending event bytes have configured hard limits. Large data is externalized as assets; exceeding the display limit fails explicitly with retained content, rather than using unbounded memory. Pagination/chunked storage can be added only if measured run sizes require it.

## The run stream

Use **one Redis stream per attempt**, keyed by run ID and attempt number. An old worker can neither append to nor trim a newer attempt's stream. There is no Redis value that overrides PostgreSQL's current attempt or sealed state. Stable cursors encode run ID, attempt number and the per-attempt event sequence, not a bare Redis ID.

Only the owning worker produces an attempt's ordered stream. An atomic Redis helper checks count and bytes before append. It refuses oversized entries and capacity overflow; it does **not** evict an unpersisted prefix with `MAXLEN`. The worker pauses event production, flushes its display snapshot, trims only through the durable cursor, and continues. A bounded queue and provider response/output limits propagate backpressure; if the durable store remains unavailable past a deadline, stop the attempt. Never buffer indefinitely. Stream expiry is bounded even for a stale producer; missing streams are not recreated by an append operation.

The worker creates/recreates its stream only after checking authority and publishing a display snapshot covering all earlier local events. The initial trim floor is that snapshot's cursor. Redis loss forces the same handshake; a gap is not treated as empty successful replay. A transient replay connection failure or timeout does not fail an otherwise authorized execution while durable storage is healthy. The producer continues bounded display publication, retries cache setup no more than once per second (or the longer configured display flush interval), and resumes append only after a fresh authority check and a covering snapshot reset. An uncertain append invalidates replay until that reset. Durable publication, authority, integrity and size failures retain their ordinary failure behavior. The server may wait for a newer snapshot within a bounded timeout, then return `unavailable` with retry guidance. It must not repeatedly tell a client to fetch the same inadequate snapshot.

The SSE gateway periodically reads database authority in short sessions, on initial connect, reconnect and before each bounded batch (with a bounded batch time). It serves only that attempt. A change of attempt, lost stream, expired cursor or terminal state emits a control frame (`reset`, `retry_later`, or `closed`) carrying the required durable view version. Clients replace provisional state from `/items` before reconnecting. Sealed views ignore Redis; control-plane seal needs no successful Redis close write. An event already in flight when authority changes can still be shown provisionally; the reset protocol corrects it. Redis never supplies durable writer authority.

Only persisted display prefixes are trimmed. Terminal attempt streams expire after the configured replay TTL. Redis `MAXLEN ~` is approximate and cannot express this persistence condition; see [Redis XADD](https://redis.io/docs/latest/commands/xadd/). The exact cap and lost-stream handshake require a real Redis acceptance test, not merely a client filter.

## Outbox and webhooks

```
outbox
  id  organization_id  workspace_id NULL  kind  dedupe_key  target  payload
  status  event_id NULL  event_seq NULL  subscription_id NULL
  available_at  attempts  lease_owner NULL  lease_token_hash NULL  lease_expires_at NULL
  last_error NULL  created_at  delivered_at NULL
  kind IN ('webhook', 'child_result', 'email')
  status IN ('pending', 'delivered', 'dead')
  UNIQUE (kind, dedupe_key)
```

The common mechanism owns durable delivery/claim/settlement, not domain handlers. `event_id`, `event_seq` and `subscription_id` are diagnostic provenance, not retention foreign keys to deletable source rows; target/payload contain all delivery requirements. `app.py` registers handlers from the owning packages. These three kinds have actual v1 callers: subscriptions, child notifications and identity mail. This is not a generic job system. No handler holds a session during external delivery.

Event flush matches subscriptions with bounded count/filter complexity. The subscription read at flush is the selection point: a concurrent edit affects subsequent selections, not already queued deliveries. A webhook copies URL, secret and payload. Re-encrypt the secret with **outbox-row AAD**; copying ciphertext bound to a subscription row would fail decryption or retain an unsafe cross-row exemption. Editing/deleting a subscription does not invalidate queued rows. Rotation/re-encryption uses key IDs in the encrypted envelope.

Claim selects pending/due rows with an absent or expired lease using `SKIP LOCKED`, then increments attempts and records a fresh random token and database-clock expiry. Settlement compares the same token/generation; an old sender cannot settle a newer claim. After an uncertain HTTP result, retry may deliver twice. Receivers deduplicate by stable delivery/event ID; there is no exactly-once webhook claim. Sign exact body bytes plus timestamp and delivery ID with HMAC-SHA256. Do not follow redirects; destination/network policy applies on every attempt, including resolved addresses, not only on subscription creation.

Webhook retries back off to a bounded attempt limit, then dead-letter with inspection and manual redelivery. Internal child deliveries retain retryable failures with alerting; permanent invalid destinations receive an explicit delivered/ignored disposition rather than silently disappearing. Dead and delivered rows have bounded configured retention. Child-result insertion/settlement occurs in one database transaction as described in [05](05-runs.md).

## Trace query

`runs/traces.py` queries the configured backend with `harness_run_id` and tenant correlation, then filters returned spans against organization/workspace/run/attempt. Provider results are not trusted for tenancy. Redact credentials and private tool data consistently with display. **Keeps:** the post-fetch correlation filter.
