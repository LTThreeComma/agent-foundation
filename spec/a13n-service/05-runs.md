# Runs: input ownership, execution and history

The core has four operations: accept, claim, execute and seal. PostgreSQL owns input assignment and execution authority. A checkpoint owns resumable Harness state. A sealed run owns its outcome. Accepting an input does not prove incorporation, and incorporating it does not make a failed run the thread's history.

## Nouns

- A **session** groups related threads for the Console.
- A **thread** advances one history at a time, with an inbox and a desired mount set.
- An **entry** is one submitted input. It becomes immutable when assigned.
- A **run** accepts one source entry, an agent revision, options and an execution identity. It may incorporate further compatible entries. Its initial history never changes.
- An **attempt** executes that run under a renewable lease. Recovery and handoff replace the attempt, preserving the run and its assigned inputs.
- A **checkpoint** is resumable state plus Service input receipts. It is not display history and makes no promise to roll back external effects.

## Tables

The tenant foreign-key rules in [03](03-tenancy.md#tenant-integrity) apply throughout. Object-reference columns hold immutable payload keys; snapshot ownership and publication are in [07](07-facts-and-delivery.md#objects).

```
sessions
  id  organization_id  workspace_id  labels  created_by_id  version  created_at  updated_at

threads
  id  organization_id  workspace_id  session_id  origin  origin_thread_id NULL
  origin_run_id NULL  origin_tool_call_id NULL  current_run_id NULL  head_run_id NULL
  last_run_id NULL  archived_at NULL  version  labels  created_at  updated_at
  origin IN ('new', 'fork', 'child')
  CHECK (origin = 'new' AND origin_thread_id IS NULL AND origin_run_id IS NULL
                       AND origin_tool_call_id IS NULL
      OR origin = 'fork' AND origin_thread_id IS NOT NULL AND origin_run_id IS NOT NULL
                        AND origin_tool_call_id IS NULL
      OR origin = 'child' AND origin_thread_id IS NOT NULL AND origin_run_id IS NOT NULL
                         AND origin_tool_call_id IS NOT NULL)
  UNIQUE (origin_run_id, origin_tool_call_id) WHERE origin = 'child'

inbox_entries
  id  organization_id  workspace_id  thread_id  kind  delivery  position  principal_id
  authority  payload NULL  payload_ref NULL  agent_id NULL  agent_revision_id NULL  options
  waiting_run_id NULL  child_run_id NULL  origin_run_id NULL
  request_key NULL  request_digest NULL  request_kind NULL  request_target NULL
  status  assigned_run_id NULL  incorporated_checkpoint_seq NULL  failure NULL
  created_at  finished_at NULL
  kind IN ('message', 'feedback', 'child_result')
  delivery IN ('steer', 'next_run')
  status IN ('pending', 'assigned', 'consumed', 'failed', 'withdrawn')
  UNIQUE (thread_id, position) DEFERRABLE INITIALLY IMMEDIATE
  UNIQUE (workspace_id, principal_id, request_key) WHERE request_key IS NOT NULL
  UNIQUE (child_run_id) WHERE kind = 'child_result'
  CHECK ((payload IS NULL) <> (payload_ref IS NULL))
  CHECK ((kind = 'message') = (agent_id IS NOT NULL))
  CHECK ((kind = 'feedback') = (waiting_run_id IS NOT NULL))
  CHECK ((kind = 'child_result') = (child_run_id IS NOT NULL AND origin_run_id IS NOT NULL))
  CHECK (status NOT IN ('assigned','consumed') OR assigned_run_id IS NOT NULL)
  CHECK ((status = 'consumed') = (incorporated_checkpoint_seq IS NOT NULL))
  CHECK (status <> 'pending' OR assigned_run_id IS NULL)

runs
  id  organization_id  workspace_id  session_id  thread_id  agent_id  agent_revision_id
  revision_selection  principal_id  authority  options  environment_mounts  source_entry_id  trigger
  lineage  parent_run_id NULL
  status  wait_reason NULL  pending NULL  cancel_requested_at NULL
  current_attempt_id NULL  available_at  attempts  max_attempts  max_usage NULL
  sealed_checkpoint NULL  sealed_display NULL
  output NULL  output_ref NULL  failure NULL  usage_at_seal NULL  labels  version
  lifecycle_seq  created_at  started_at NULL  sealed_at NULL  updated_at
  revision_selection IN ('pinned', 'default', 'inherited')
  trigger IN ('input', 'queued', 'feedback', 'child_result', 'spawned')
  lineage IN ('root', 'continue', 'fork')
  status IN ('accepted', 'running', 'waiting', 'completed', 'failed', 'cancelled')
  wait_reason IN ('approval', 'client_tool', 'user_input', 'multiple')
  CHECK ((lineage = 'root') = (parent_run_id IS NULL))
  CHECK ((status IN ('waiting','completed','failed','cancelled')) = (sealed_at IS NOT NULL))
  CHECK ((status IN ('failed','cancelled')) = (failure IS NOT NULL))
  CHECK ((status IN ('waiting','completed')) = (sealed_checkpoint IS NOT NULL))
  CHECK ((status IN ('waiting','completed')) = (sealed_display IS NOT NULL))
  CHECK ((status = 'waiting') = (wait_reason IS NOT NULL AND pending IS NOT NULL))
  CHECK ((status = 'running') = (current_attempt_id IS NOT NULL))
  UNIQUE (thread_id) WHERE status IN ('accepted', 'running')
  UNIQUE (source_entry_id)

run_attempts
  id  organization_id  workspace_id  run_id  number  status  start_reason
  replaces_attempt_id NULL  worker_id  worker_build  harness_run_id NULL
  lease_token_hash  lease_expires_at  heartbeat_at  yield_reason NULL  failure NULL
  lifecycle_seq  created_at  started_at NULL  finished_at NULL  updated_at
  status IN ('leased', 'running', 'succeeded', 'yielded', 'failed', 'cancelled')
  start_reason IN ('initial', 'recovery', 'handoff')
  UNIQUE (run_id, number)
  UNIQUE (run_id) WHERE status IN ('leased', 'running')
  CHECK ((status IN ('succeeded','yielded','failed','cancelled')) = (finished_at IS NOT NULL))
```

`thread_environments` is owned by [06](06-environments.md). `runs.environment_mounts` is the mount set frozen at acceptance, `[{name, environment_id, working_directory}]`, with a GIN index for the active-use query in 06. Source entry and run references can use deferred foreign keys within acceptance. Same-thread pointer constraints cover current/head/last run and source/assigned entries; parent/origin links instead require the same workspace/session. A constraint trigger checks pointer/status relationships at commit, including completed/waiting-only heads.

`head_run_id` is the latest completed or waiting run. `last_run_id` is the most recently sealed run, including failure/cancellation. `current_run_id` is accepted or running. A failed run's checkpoint remains inspection evidence but never becomes head.

Running progress lives in the run's fixed `state.json` and `display.json`, not relational checkpoint selectors. `sealed_checkpoint` and `sealed_display` are typed metadata (digest, size, content type, format, sequence and producing attempt) selecting exact completed/waiting objects. Keys derive from the run ID. Failed/cancelled runs have no selected continuation state. The checkpoint envelope, rather than a database progress counter, is the recovery authority; database lifecycle and inbox disposition are never rolled back to match it.

Session `updated_at` is durable conversation activity. Actual Run/Attempt lifecycle transitions and visible inbox, Session metadata and Thread changes advance it transactionally; reads, heartbeat renewals, idle scans, publication and idempotent replay do not. The runs activity helper locks affected Sessions in sorted ID order with `FOR NO KEY UPDATE`, after existing domain locks and before the workspace event cursor. It reads fresh database time after locking and never moves activity backward. Lifecycle event flush touches each affected Session once; non-event mutations use the same helper without fabricating events. Event retention does not determine current activity.

The Session-specific database stamp distinguishes metadata from activity: actual row changes excluding `version` and `updated_at` increment the old metadata version once and use at least old activity and fresh database time. Activity-only writes retain the old version and the greater timestamp. Supplied versions and version-only/no-op updates cannot manufacture a metadata revision. Other resource stamps are unchanged.

Thread `version` changes on every visible inbox, mount, pointer, label or archive change. It is the inbox-edit ETag, separate from checkpoint counters and event cursors. Entry positions are positive bigints; allocation/reordering takes the thread lock. Reordering defers uniqueness until all affected positions are set.

Message delivery is selected by the caller, child results use `steer`, and feedback stores `next_run`; feedback is never offered to an active run. Public feedback has no delivery selector.

## Submit and accept

Submission is authorize and validate, prepare external payloads without a database session, then a short transaction: lock thread, revalidate, resolve the request key, append an entry, and try to accept. New-thread submission also creates its session if needed. A unique-key loser rolls back the entire tentative creation before reading the winner; it leaves no duplicate thread or session. See [10](10-api.md#idempotency).

```python
async def submit_input(actor: Principal, thread_id: str, submission: Submission) -> Submitted:
    authorize(actor, await read(ThreadRow, thread_id), "run")     # before any preparation
    entry_id = new_object_id("inb")
    payload_ref = await stage_payload(entry_id, submission)       # object store, no session held
    async with transaction() as session:
        thread = await lock(session, ThreadRow, thread_id)
        authorize(actor, thread, "run")
        if found := await find_request(session, actor, submission.request_key):
            return replay(found, submission)                      # conflict when the digest differs
        entry = await append_entry(session, thread, submission, entry_id, payload_ref, principal=actor)
        run = await accept(session, thread, cause="submit")       # None while the thread is busy
        return Submitted(entry=entry, run=run)
```

Messages and control feedback use the same inbox submission path, distinguished by `kind`. Archived threads refuse public submissions. Waiting or failed threads may receive messages; source eligibility below determines whether they start a run or remain pending. Malformed/unauthorized requests fail without appending; otherwise valid feedback for an ineligible target is retained as a failed receipt with `stale_feedback`, including while busy. Payload limits and the capacity rules below are enforced under the thread lock. Preparation and failed duplicate requests cannot evade upload limits.

### Inbox capacity

The thread's ordinary input usage is the count and payload bytes of **pending plus assigned messages and child results**. Assignment and return to pending do not release or acquire capacity. Consumption confirmation, failure and withdrawal release a counted entry's capacity once. Recovery retains its assignment and occupancy. Pending edits recheck the resulting byte total under the same thread lock. A valid child result that cannot fit remains in its retryable outbox delivery, without inserting or settling it. Do not fail or discard it merely because the inbox is full. One query/predicate owns this accounting; no separate pending/assigned counters or allowance for returning entries is needed.

Control feedback is excluded from ordinary inbox count/byte occupancy so queued messages cannot block resolution of an existing wait. It still has bounded request bytes, answer count and per-result size, plus ordinary authorization and request-key checks. Under the thread lock, validate its exact idle waiting target and attempt acceptance in the submission transaction; an ineligible target fails stale and an admission rejection fails the entry rather than parking control replies for a future wait. Concurrent submissions cannot create two active successors. Feedback contains only the control results defined below, never free-form message input or execution overrides; ordinary text must use a capacity-counted message. This exception adds no reserved slots, extra queue or input state.

The boundary delivery batch is separately bounded. Its configured count/byte defaults are still to be selected from vertical-slice measurements; this design does not specify one or ten entries.

### Source selection

`accept(session, thread, cause)` is the only run-creation function. It is SQL-only:

1. Return if archived or already active.
2. Choose an eligible source below; revalidate the source principal, its authority, the agent and the exact feedback target. Fail rejected entries, examining only a bounded batch.
3. Resolve the intended revision and mount set with ordinary reads, then acquire all required locks in the order below and revalidate versions/state. Freeze revision, normalized options and authority; run core checks and additional admission policy.
4. Insert the accepted run, assign its source, reserve/freeze mounts as in [06](06-environments.md), and update thread current/version.
5. Stage `run.accepted` for the transaction's event flush. Nothing is yet consumed.

| Thread condition                                                                          | Eligible source                                                                                                                                                                                                                                                                                                                                          |
| ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Archived, or an accepted/running run exists                                               | None                                                                                                                                                                                                                                                                                                                                                     |
| No previous run, or latest run completed                                                  | Lowest-position pending message or valid child result; stale feedback is failed                                                                                                                                                                                                                                                                          |
| Head waiting only for user input, with no later failed/cancelled run                      | Lowest-position pending message; close the unanswered question calls with no-response results, then inject this message                                                                                                                                                                                                                                  |
| Head waiting with any approval or client-tool request, with no later failed/cancelled run | Matching feedback for this exact head; messages and child results stay pending                                                                                                                                                                                                                                                                           |
| Latest run failed/cancelled                                                               | Automatic advancement stops. Explicit submission may start **that new message or valid feedback** using the unchanged initial-history rule below, subject to the same waiting-type restrictions when the head is waiting. Unrelated pending entries do not replace its source. There is no retry operation; to run the same input again, submit it again |

Select by thread-state eligibility, then position. Matching control feedback passes earlier ineligible messages and child results without changing their positions. Inspect the entire sealed pending set: a wait is question-only only when every item is `user_input`; a mixed set requires control feedback. `wait_reason` summarizes one kind or `multiple`, but never replaces the per-item check. Child results do not release any wait. [Waiting, interrupt and fork](#waiting-interrupt-and-fork) owns feedback targeting and result normalization.

After explicit restart, compatible pending steers may join it; `next_run` messages stay queued. Inputs assigned to the ended run are excluded. A child result is not an explicit user action that lifts this pause.

Messages select their requested revision or the default at acceptance (`pinned`/`default`). Feedback inherits its waiting run's revision/options; a child result starting a run inherits its origin run's (`inherited`). Options (allowed overrides, `mcp_headers`, limits) are validated and stored on the run, never reconstructed from an editable entry or changed default. Feedback cannot change them. New messages have their own options; omission uses documented defaults.

Choose initial history by one rule: use this thread's `head_run_id` when present; otherwise a fork thread uses its immutable `origin_run_id`; otherwise start with empty history. These produce continue, fork and root lineage respectively. The fork fallback persists through any number of failed/cancelled runs until this thread has its own completed/waiting head. It never uses the failed run's checkpoint. Existing origin/head fields suffice; no retry-specific baseline is stored. Parent/revision never move after acceptance. Per-entry rejection uses a typed result or savepoint; database failures roll back and retry the transaction.

## Assignment and incorporation

The durable state is **assigned to a run**, not offered by an attempt:

```
pending ──accept / boundary──> assigned ──receipt confirmation──> consumed
   │                              │
   └──reject / withdraw           ├──completed / waiting, absent from final receipts──> pending
                                  └──failed / cancelled before confirmation──────────> failed
```

Recovery and handoff do not change assignment. `offered` is a worker-local set for one Harness instance. This removes an unnecessary second recovery state machine.

At a boundary, lock thread → run → attempt, check authority and assign a bounded FIFO batch of compatible pending steers. The source was assigned at acceptance and is incorporated first, even if source eligibility skipped earlier entries. FIFO checks then apply to the compatible boundary-delivered entries; they do not require a control-feedback source to have a lower inbox position than the previously ineligible messages that may now steer its successor. Materialize and offer after releasing the transaction. Assigned entries cannot be edited or withdrawn.

Compatibility is a configuration rule, not an identity rule. A message may steer when its selector permits this agent/revision and its normalized options match the run's, using the effective-header comparison in [04](04-resources.md#caller-headers). Anyone with the `run` verb on the thread may steer; the run keeps the principal and authority frozen at acceptance, and a steer never changes them. Messages require an explicit `agent_id`; an omitted revision permits the current run's revision for steer matching. Different headers for a connection available to this run prevent steering; unused inherited headers do not. A mismatch leaves the message for a later run. Feedback is never a steer.

The Service prepares each assigned message as one bounded native group: an empty application-only TextContent marker carrying its Run/entry identity, item count and deterministic digest, followed by exactly the ordered prepared text/binary items. The digest covers complete bytes, text, media type and supported native metadata. A pending delivery batch is prepared before any frame is offered, within the lesser of the configured object byte limit and the native 16 MiB payload limit. This preparation budget counts native encoded content, including markers and binary base64 expansion; URL reads use the remaining allowance and immutable Asset size is checked before reading its object. The final checkpoint envelope retains its separate authoritative object limit, including earlier history and state. A worker accepts a newly incorporated frame only when its marker matches that attempt's offered expectation and the complete group validates within one user content container. An unissued, copied, incomplete or changed frame cannot create a receipt. This in-process digest checks integrity; it is not a credential against malicious server code.

The **awaited incorporation hook before history replacement or compaction** validates the detached exported HarnessState history and records cumulative receipts outside message history. Enqueue acceptance, observer events and an empty event queue are not receipts. A frame's bytes are checked for a new receipt until checkpoint publication succeeds; later hooks need not rehash that already confirmed payload. Recovery validates saved frame bytes, and each native observation validates complete owned groups before suppressing presentation. Previously durable receipts remain valid after legitimate later compaction removes their frames. The Stream Protocol's exact input coordinates select only complete validated Service groups for suppression in the native presentation; canonical inbox content remains the public input owner. Unowned neighboring content retains its ordinary metadata-based presentation, while enqueue diagnostics remain visible. Prove this integration before expansion; see [12](12-validation.md).

Publication conditionally replaces the run's fixed `state.json` with complete state and cumulative receipts. Confirmed object publication is the checkpoint's durability point. Then a short transaction locks thread → run → attempt and the input rows, verifies current worker authority, receipt scope/kind, assignment and FIFO order, and marks newly confirmed entries `consumed` with `incorporated_checkpoint_seq`. Already consumed entries are skipped; their receipt metadata is not rewritten by later checkpoints. This transaction is idempotent and releases ordinary capacity only for newly consumed capacity-counted entries. No session spans the object write.

On recovery, claim the object writer and confirm the restored receipts **before** delivering new input or entering the Harness. Reoffer only assigned entries absent from those receipts, once per new Harness instance. **Do not feed the source again just because an attempt started.** Without a run checkpoint, initialize from the parent's selected state, start an empty current-run receipt set, and offer the source. Later checkpoints retain all earlier receipts. If a PUT succeeded but confirmation did not, the next eligible attempt repairs the database without enqueueing the input again. If the PUT never succeeded, incorporation after the prior checkpoint may be replayed.

Canonical inbox content also owns public input visibility, independently of whether a native input observation has been emitted. Run views reuse the inbox projection with stable entry IDs and current assignment/status; recovery does not reoffer an incorporated input to regenerate display.

`consumed` means durable incorporation **and database confirmation**, not merely presence in an object. If failed/cancelled seal wins before confirmation, remaining assigned entries become `failed`, even when an object-only receipt exists. Such receipts cannot later change terminal input disposition or revive the run; confirmed consumed entries remain consumed. Completed/waiting seal first confirms the selected final checkpoint's receipts, then returns only the remaining assigned entries to pending. This terminal-race rule preserves the existing implementation's division of authority.

**Keeps:** at-most-once incorporation per entry into its assigned run's committed continuation. This is not exactly-once model requests, tool effects or sandbox changes. Unknown external effects follow Harness/provider replay rules; unsafe calls need reconciliation or human input rather than automatic repetition.

Before an unsafe side-effecting tool dispatch, persist a safe pending-call checkpoint or obtain a provider/tool durable operation receipt with a stable call identity. Otherwise a crash can roll back past the call's existence, and no recovery policy can recognize its unknown outcome. Use the Harness's `tool_recovery="declared"`, never blanket `always`. Undeclared pending calls are closed with unknown-effect results; a later model decision can still request another call, so tool approval/idempotence remains necessary. The real pre-dispatch checkpoint/receipt integration is a vertical-slice gate, not an assumed API.

## Claim, heartbeat and authority

The existing periodic worker scan is also triggered early by a capacity-1 Redis List carrying generic wake markers. Notifications follow the commit that makes a run accepted; they do not select a run or grant ownership. [09](09-runtime.md#worker-claim-wakeups) owns the notification and wait rules.

Workers reserve local capacity before selecting a due accepted run with `FOR UPDATE SKIP LOCKED`. In one short transaction recheck cancellation, attempt limits, archive state and build/checkpoint compatibility, insert the attempt, increment the count, set running/current, and stage events. Claim locks run → attempt and never subsequently locks thread. Archive/interrupt take thread → run, so the run lock arbitrates with claim.

`runs/attempts.py` owns the single worker predicate, evaluated after relevant locks with fresh database time:

```
run.status = running
AND run.current_attempt_id = attempt.id
AND attempt.status IN (leased, running)
AND attempt.worker_id = caller.worker_id
AND attempt.lease_token_hash = hash(caller.token)
AND attempt.lease_expires_at > clock_timestamp()
```

Every execution-dependent database mutation uses it: receipt confirmation, assignment, child spawn, worker environment requests and worker seal. Expired workers cannot revive leases even before the sweep. `now()` is transaction-start time, unsuitable after lock waiting. Usage is the explicit exception: it records a past charge. Redis attempt streams cannot publish authoritative state.

Object reads/claims/writes happen outside sessions, with database authority checks before and after and object-version CAS against the claimed writer. A newer object claim invalidates the old token. Lease expiry cannot retract an already dispatched PUT: late bytes are not permission to confirm receipts or seal. [07](07-facts-and-delivery.md#checkpoints-and-display-snapshots) owns uncertain-write reconciliation and the distinction between physical bytes and database lifecycle.

Heartbeat starts immediately after claim in a supervised task, at one third of the lease. It uses the same predicate and extends from database time. If renewal is not confirmed before a conservative local deadline, stop new dispatch and cancel supported in-flight work. Already sent calls can still complete. Cancellation/grant refresh have independent bounded polling too; long requests do not defer them to model boundaries.

The expiry sweep locks thread → run → attempt, rechecks current identity and expiry, then closes the attempt. The run becomes accepted with backoff, failed on exhaustion, or cancelled on interrupt. Terminal attempts are immutable. Recovery/handoff preserve the run; no sweep guesses what inputs a dead worker saw.

## Execute

`execute` owns orchestration; adapters own their I/O, never domain persistence. No session survives an external call or wait.

1. Load revision/options/authority; revalidate current principal, grants and live referenced resources.
2. Read and claim checkpoint/display writers under the current attempt; validate formats, identities, digests and display-cut ordering. Confirm checkpoint receipts before Harness entry. Recovery starts a display segment marking the previous attempt interrupted relative to its execution checkpoint. A recoverable completed/waiting candidate may seal without repeating execution; database terminal state always wins. See [07](07-facts-and-delivery.md).
3. Obtain frozen mounts through the environment protocol. Resolve credentials, OAuth and principal-owned managed account bindings through resource services. Build typed capabilities with an async exit stack for partial-setup cleanup. A generic `open()` loop is not a requirement.
4. Offer source if absent from current-run receipts. Before each paid call check cancel, authority, limits and the admission policy's `proceed`. Ingest usage after results. Service tools use the run's bounded authority and the same services as HTTP.
5. At safe boundaries record incorporation, ingest usage, flush the paired display cut and publish the checkpoint, then confirm receipts before selecting/offering steers for the next request. Display may flush more often. Before finishing, drain awaited incorporation hooks and the local publisher, persist final state and stop further publication.
6. Seal, or yield at a safe boundary for handoff. Provider cleanup is outside seal.

Setup errors such as missing credentials fail with actionable reasons. Transient failures use bounded recovery/backoff within `max_attempts`. Unknown effects do not automatically become retryable errors. Limits aggregate across attempts and late usage. Recorded-usage checks have the soft-limit semantics defined with the policy seam in [09](09-runtime.md#extension-points).

## Seal and successor scheduling

Seal changes one thread. Cross-thread delivery uses the outbox after commit.

| Caller                    | Authority                                                |
| ------------------------- | -------------------------------------------------------- |
| Worker                    | Full unexpired worker predicate                          |
| Interrupt of accepted run | Thread/run locks; still accepted without current attempt |
| Lease sweep               | Thread/run/attempt locks; still current and expired      |

Completed/waiting require a verified final object checkpoint containing the source receipt and a matching outcome candidate, plus durable display through its cut. Prepare and verify them outside the transaction; waiting requires the exact pending set. The local publisher is quiescent before seal. The seal selects their exact metadata and confirms receipts in the same database transaction. An object-only completed/waiting candidate is not a terminal run until that transaction succeeds.

Failure/cancellation selects no new continuation state and requires no object I/O. Confirmed consumption is retained; any still-assigned input fails even if an unconfirmed receipt exists in an object. A lease sweep that schedules recovery preserves assignments for the next attempt to reconcile; one that seals failure/cancellation applies this terminal rule. Receipt confirmation and seal serialize on the run lock, but object PUT does not. Late object bytes cannot reverse the seal. Resolve uncertain database commits by rereading lifecycle and selected terminal metadata.

Inside thread → run → attempt locks:

1. Verify caller/outcome under the current authority.
2. Yield/recoverable failure: close the attempt, clear current attempt, set accepted/available time, stage events and commit. Assignments stay. Otherwise continue below.
3. For completed/waiting, confirm the verified final checkpoint's receipts and select its exact checkpoint/display metadata. Close the attempt if present, seal outcome and `usage_at_seal`, clear current attempt, update thread last/current. Completed/waiting also advance head.
4. Completed/waiting return assigned entries absent from final receipts to pending. Failed/cancelled fail all still-assigned entries. **Consumed entries stay consumed**: they record confirmed durable incorporation, not successful work. Neither kind automatically enters a later run after failure; an object-only receipt never overrides a terminal disposition.
5. Stage lifecycle events; enqueue deduplicated child-result delivery for a child thread's completed/failed/cancelled run. Waiting emits no result delivery.
6. Flush events/matching webhooks as the final database phase; commit.

```python
async def seal(caller: SealCaller, run_id: str, outcome: Outcome) -> None:
    async with transaction() as session:
        thread, run, attempt = await lock_chain(session, run_id)  # thread → run → attempt
        caller.verify(run, attempt)                               # worker predicate, interrupt or sweep
        ...                                                       # steps 1–6
    await advance(thread.id)   # its own transaction: accept(); advance_threads covers a crash here
```

Successor creation is a separate `accept` call after commit. An indexed `advance_threads` sweep recovers a lost call; submission and child delivery can also call it. Head/last/ current and pending entries are durable work evidence. Invalid inputs or slow admission cannot prevent sealing. The sweep uses bounded batches and `SKIP LOCKED`, with a starvation test for repeated invalid entries. Worker cleanup only closes clients; environment stop/destroy belongs to its durable lifecycle.

## Waiting, interrupt and fork

**User questions** (`ask_user_question`, pending kind `user_input`) receive ordinary messages, including free text submitted through a question UI. At a question-only wait, create the message's successor from the waiting checkpoint, complete the question calls with no-response results and inject the message once through the usual receipt protocol. Do not fabricate structured answers or leave unresolved tool calls in the resumed history. The message retains the normal agent/revision/options and execution-identity rules. Ordinary messages never implicitly approve or reject a pending approval, or complete a client tool, in the same thread.

**Feedback** carries control results for a wait containing approval or client-tool requests. It requires `waiting_run_id` plus a `tool_call_id` on every supplied result; the UI carries these identifiers, not the user. Both target and call IDs remain fixed through consumption and request-key replay. The same Harness tool call can suspend more than once for different approval sources, so a call ID alone is insufficient. Missing targets, duplicate/unknown call IDs and result-kind mismatches are invalid. Under the thread lock, check at submission and actual acceptance that the target is this thread's idle waiting head; otherwise valid replies to an ineligible target fail with `stale_feedback`, including while a successor runs. Never infer the target from a newer head or reinterpret stale feedback as a message.

Normalize one feedback submission against the complete sealed pending set. Use each supplied approval decision or client-tool result; omitted approvals become denied, omitted client results become no response, and unanswered user questions in a mixed set become no response. An empty result list applies all defaults to that exact control wait. The UI states that omitted approvals will be denied. Pass the complete result batch to the existing Harness deferred-resume contract; do not persist partial approval progress or add a waiting state. The normalized batch is the entry's payload, so even an empty submitted list satisfies the payload storage constraint. Ordinary message text uses `kind=message`, not feedback.

Any principal with the `run` verb may submit feedback; its successor inherits the waiting run's revision, options, principal and authority. Feedback cannot change execution settings. A concurrent second reply cannot start another active successor. Failed/cancelled successors keep the existing explicit-resubmission rule and unchanged head; no feedback is automatically replayed as a new request.

**Interrupt** records cancellation on accepted/running; repeating is a no-op. Accepted runs seal immediately. Running workers cancel supported calls and seal at a safe boundary; expiry handles missing workers. Repeating on cancelled returns that result; other sealed outcomes conflict. Cancellation neither undoes effects nor implicitly cancels children.

**There is no retry operation**. A failed or cancelled run never becomes head. The next message uses the unchanged baseline selected by [source selection](#source-selection); running the same input again is submitting it again, pinned to the same revision if wanted, and the Console can prefill it. Cancelling “write a poem” then submitting “hello” never reruns the poem. Recovery of a crashed attempt is not retry: it resumes the same run from its last checkpoint within `max_attempts`.

**Fork** requires a completed/waiting origin and retained compatible checkpoint. Create thread/source atomically using the entry request key. Copy desired mounts unless fresh environments were requested. An explicit message fork of waiting history closes inherited pending calls with denial/no-response results in the new branch; it neither grants an approval nor resolves the original thread's wait. This existing new-thread operation is separate from ordinary messages continuing a waiting thread. Failed/cancelled runs are not fork origins: after history advanced, fork their original parent and resubmit (or create a thread for root input); the UI can prefill the input.

**Archive** replaces destructive interactive thread deletion. Under thread lock, block acceptance, withdraw pending inputs, remove desired mounts and request active cancellation. An accepted run is sealed cancelled by the same archive operation; a running run gets an interrupt request. Frozen run mounts remain until it ends. Later child notifications are delivered-but-ignored. Retain facts/keys in v1; no physical purge or unarchive.

## Child runs

`spawn_child` checks parent worker authority under thread → run → attempt, enforces depth/count limits, and atomically creates child thread/message/first run. Pinned subagent revision and authority come from the parent definition. Unique `(origin_run_id, origin_tool_call_id)` is stable across recovery, never attempt-local. Authorized replay returns the same child/initial run regardless of its current outcome.

Child seal enqueues a result keyed by sealed child run ID. Delivery locks only the parent thread, validates immutable origin/current state, then inserts the unique entry and settles the internal outbox row in one commit. Every completed/failed/cancelled child run reports, including successors after waiting. This is notification of that run, not a second completion of the original tool call.

Eligibility requires the origin to be current or an ancestor of the parent's committed head. Active origins can receive compatible steers. Failed/cancelled origins that never became history fail the result entry with `origin_not_committed`; they are not revived. Waiting history retains results until a message or feedback eligible under [source selection](#source-selection) starts a successor. A later failure still pauses automatic advancement. The child remains directly inspectable in all cases.

## Lock discipline and guarantees

Multi-row domain locks: thread → run → attempt → environment, sorting IDs within a set. Resource heads needed for acceptance follow these; resources never lock runs/threads. Object I/O and version CAS are outside database locks. Session activity locks follow these domain locks, in sorted Session ID order, using `FOR NO KEY UPDATE` to remain compatible with child foreign-key checks. Event allocation is last; no new domain lock after it. Claim/heartbeat may take a suffix, never a preceding lock. Child delivery and successor acceptance are separate transactions. PostgreSQL deadlock/serialization errors still receive bounded whole-transaction retries; the convention is not an impossibility proof for all database deadlocks.

**Keeps:** one active run per thread, one live attempt per run, immutable terminal facts, state/events in one database commit, checkpoint/state receipts in one object write followed by idempotent database confirmation, one worker predicate, no session across external I/O, recoverable successor/child delivery. Adversarial schedules and implementation gates are in [12-validation.md](12-validation.md).
