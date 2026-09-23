# Facts, recovery state and delivery

There are three distinct products: immutable facts (usage, audit, revisions and terminal run/attempt records), resumable execution state, and user-visible observations. PostgreSQL owns lifecycle, execution authority, inbox disposition and the pointers that select each run's committed state and display. Objects hold immutable bytes. Redis carries provisional live output and worker wakeups; it cannot advance execution history or decide that a run is finished.

## Immutability

Triggers forbid update/delete of usage, audit and revision rows, and changes to terminal run/attempt facts. Run labels alone remain editable through a narrow whitelist including their version/timestamp; frozen checkpoint/display pointers, status and usage do not. Terminal attempts have no exemption. Consumed inputs/payloads and run mount snapshots are also immutable. Transition/pointer constraints supplement checks; a status-value CHECK alone does not restrict transitions. The migration's trigger count is an implementation result, not a target.

Resource heads/revisions are archived and retained in v1; no automatic revision purge guesses reachability from JSON configuration. A future revision purge must first provide complete dependency evidence for pinned skills/subagents.

There is no separate lifecycle event log and no workspace-ordered lifecycle cursor. Run and attempt rows record each transition's result (timestamps, status, yield reason, failure), and a run's attempts are its execution history. Outbound notification uses [lifecycle webhooks](#lifecycle-webhooks); live observation uses [the thread stream](#the-thread-stream).

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

Every object is immutable. Run-owned state and display objects are digest-keyed under the run's prefix: `orgs/{org}/runs/{run}/state/{digest}` and `orgs/{org}/runs/{run}/display/{digest}`. Other payloads use `orgs/{org}/runs/{run}/output/{digest}`, `orgs/{org}/entries/{entry}/payload/{digest}` and `orgs/{org}/uploads/{upload}`. An upload is referenced in place when it becomes a skill package or asset. Content-addressed entry payloads let a pending edit select different bytes without overwriting an earlier version. A `*_ref` column stores an immutable payload key; the API expands it to `ObjectRef(key, digest, size, content_type)`. There is no object catalog table.

The object-store contract is create-only write, read, prefix listing and delete, on both local and S3 backends; there is no conditional replacement or version token. Publication uploads outside any session, verifies durable acknowledgement with the expected digest and size, then commits the owner's reference. Conditional create refuses different bytes at an existing key. An uncertain write is reconciled by reading back and comparing the exact bytes and metadata. Failure to commit the reference can leave an unused object.

**Run state/display cleanup is owner-driven.** Only the run's own attempts write under its prefix, and only its pointers make an object reachable:

1. After a checkpoint commit, the worker deletes the objects the pointers no longer name.
2. A takeover attempt, before entering the Harness, and every seal, after commit, list the run's `state/` and `display/` prefixes and delete everything the pointers do not name.
3. No attempt starts an object write within one write timeout of its local lease deadline, so a stale attempt's bytes land before a takeover cleans.

In the normal path a run holds one state and one display object, briefly two of each between commit and deletion. A leftover requires the cleaner itself to crash after other garbage already existed. Objects named by frozen pointers are never deleted: each completed/waiting run keeps its final pair because fork and continuation can start from it, and a failed/cancelled run keeps its last pair as inspection evidence.

**No other object reclamation in v1**: no `collect_objects`, age-based upload expiry, orphan inventory, object-delete outbox or physical history/asset purge. Unused uploads and abandoned payloads remain stored; upload limits still apply. Outbox SQL retention is separate and remains as specified below.

Asset retirement blocks new selection; content stays readable to authorized readers of retained input and output. Cross-workspace asset references are refused at submission. Large tool outputs use assets rather than unbounded state or display payloads. A future reclamation design must arbitrate with publication before enabling deletion; v1 introduces no state machine for it.

## Checkpoints and display

The state object carries a format version, Harness state, the accepted revision/options digest, the checkpoint and attempt numbers and any waiting/completed outcome. The Service validates its host incorporation capability; it does not invent a Harness API that returns delivered entry IDs. [05](05-runs.md#assignment-and-incorporation) owns incorporation and the checkpoint commit.

Display is folded from Harness events, never reconstructed from compacted message history. The worker keeps the run's ordered display items in memory, keyed by item ID with states `in_progress`, `completed`, `interrupted` and `failed`, and writes the complete set as a new display object in every checkpoint commit. The display object therefore always describes exactly the restored history: recovery needs no attempt segments or interrupted-tail reconciliation, and output after recovery simply continues it. Output streamed after the last checkpoint is provisional; a crash removes it from the durable view and the next attempt regenerates it. An unsafe tool call is committed as `in_progress` before dispatch, so evidence of an attempted side effect never disappears. The display pointer records the stream position `(attempt, sequence)` it covers.

A worker that seals failed/cancelled may write its in-memory tail as one more display object, marking unfinished items interrupted; these are observations, not continuation state. A run sealed by the sweep or by interrupt keeps its last committed display, and readers render its `in_progress` items as interrupted.

`GET /runs/{run}/items` reads the pointers and lifecycle in one short database read, then the named display object, and returns the items, the covered stream position, the current attempt, the outcome and `complete` (execution sealed). Completed/waiting views use the frozen final pair. Fork and continuation read the parent's frozen state pointer; a successor writes under its own prefix. Display bytes, both stored and in memory, have configured hard limits. Large data is externalized as assets; exceeding the display limit fails explicitly with retained content, rather than using unbounded memory. Each checkpoint rewrites the whole display object; chunked storage can be added only if measured run sizes require it.

## The thread stream

Each thread has one Redis stream, `thread:{id}`. Only workers append to it, and only two kinds of entries: output deltas carrying `run_id`, attempt number and a per-attempt sequence, and a boundary marker after each checkpoint commit. Appends are best effort with a short timeout; a Redis failure drops live output without slowing execution. `XADD` uses `MAXLEN ~` as a memory cap: nothing in the stream needs to survive, because durable output is already in display objects. A Redis restart recreates the stream on the next append. Streams expire after a configured idle TTL.

`GET /threads/{thread}/stream` serves one thread over SSE. Data frames wrap AG-UI events with run, attempt and sequence; `Last-Event-ID` is the opaque stream cursor. The gateway authorizes in a short session, then reads from the client's cursor. It re-reads authority, the thread `version` and each active run's current attempt in short sessions on connect, before each bounded batch and at least once per bounded interval while idle. All connections of a gateway process share one blocking multi-key `XREAD` and one batched authority query. From those reads the gateway emits the only control frames:

| Frame               | Meaning                                                                                                                            | Client action                                                                 |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `changed {version}` | The thread snapshot is stale: inbox, pointers, mounts, headers, labels or archive changed                                          | Re-read the thread, and the inbox if shown                                    |
| `reset {run_id}`    | The run changed attempt                                                                                                            | Discard that run's provisional output and re-read its items                   |
| `gap {run_id}`      | Provisional deltas were skipped: a per-attempt sequence discontinuity, a cursor older than the stream's first entry, or Redis loss | Re-read items; after the next boundary marker the durable view covers the gap |

The gateway drops deltas from attempts older than the run's current attempt. A delta already in flight when authority changes can still be shown provisionally; `reset` corrects it. Redis never supplies durable writer authority, and a terminal run's view ignores Redis. Changes made by other callers become visible within one authority interval; a client's own operations return their results directly. There is no run-level or session-level stream: a thread has at most one active run, and a child thread is discovered from the spawn tool call in its parent's output. Streaming endpoints hold no database session while idle.

## Outbox

```
outbox
  id  organization_id  workspace_id NULL  kind  dedupe_key  target  payload
  status  subscription_id NULL
  available_at  attempts  lease_owner NULL  lease_token_hash NULL  lease_expires_at NULL
  last_error NULL  created_at  delivered_at NULL
  kind IN ('webhook', 'child_result', 'email')
  status IN ('pending', 'delivered', 'dead')
  UNIQUE (kind, dedupe_key)
```

The common mechanism owns durable delivery/claim/settlement, not domain handlers. `subscription_id` is diagnostic provenance, not a retention foreign key to a deletable source row; target/payload contain all delivery requirements. `app.py` registers handlers from the owning packages. These three kinds have actual v1 callers: subscriptions, child notifications and identity mail. This is not a generic job system. No handler holds a session during external delivery.

Claim selects pending/due rows with an absent or expired lease using `SKIP LOCKED`, then increments attempts and records a fresh random token and database-clock expiry. Settlement compares the same token/generation; an old sender cannot settle a newer claim. Internal child deliveries retain retryable failures with alerting; permanent invalid destinations receive an explicit delivered/ignored disposition rather than silently disappearing. Dead and delivered rows have bounded configured retention. Child-result insertion/settlement occurs in one database transaction as described in [05](05-runs.md).

## Lifecycle webhooks

A subscription selects lifecycle kinds: `run.accepted`, `run.running`, `run.waiting`, `run.completed`, `run.failed`, `run.cancelled` and the matching `run_attempt.leased`, `run_attempt.running`, `run_attempt.succeeded`, `run_attempt.yielded`, `run_attempt.failed`, `run_attempt.cancelled`. Every transaction that performs such a transition stages its kind and, as its last phase, calls `notify_subscribers`: it matches the workspace's enabled subscriptions with bounded count/filter complexity and inserts one outbox row per match in the same transaction. A transition and its deliveries therefore commit together. A webhook row uses its own ID as `dedupe_key` and as the delivery ID receivers use to deduplicate.

The subscription read at that point is the selection point: a concurrent edit affects later transitions, not already queued deliveries. The row copies the URL, a bounded redacted payload describing the transition (never secrets or raw credentials; large content uses authorized resource links) and the signing secret. Re-encrypt the secret with **outbox-row AAD**; copying ciphertext bound to a subscription row would fail decryption or retain an unsafe cross-row exemption. Editing/deleting a subscription does not invalidate queued rows. Rotation/re-encryption uses key IDs in the encrypted envelope.

After an uncertain HTTP result, retry may deliver twice. Receivers deduplicate by delivery ID; there is no exactly-once webhook claim, and delivery order is not guaranteed, so a receiver needing current state reads the run. Sign exact body bytes plus timestamp and delivery ID with HMAC-SHA256. Do not follow redirects; destination/network policy applies on every attempt, including resolved addresses, not only on subscription creation. Webhook retries back off to a bounded attempt limit, then dead-letter with inspection and manual redelivery.

## Trace query

`runs/traces.py` queries the configured backend with `harness_run_id` and tenant correlation, then filters returned spans against organization/workspace/run/attempt. Provider results are not trusted for tenancy. Redact credentials and private tool data consistently with display. **Keeps:** the post-fetch correlation filter.
