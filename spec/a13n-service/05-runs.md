# Runs: input ownership, execution and history

The core has four operations: accept, claim, execute and seal. PostgreSQL owns input assignment, execution authority and the pointer to each run's committed checkpoint. A sealed run owns its outcome. Accepting an input does not prove incorporation, and incorporating it does not make a failed run the thread's history.

## Nouns

- A **session** groups related threads for the Console.
- A **thread** advances one history at a time, with an inbox, a desired mount set and caller headers.
- An **entry** is one queued input: a message or a child result. It becomes immutable when assigned.
- A **run** starts from one source, either an entry or the answers that resume a waiting run, with an agent revision, options and an execution identity. It may incorporate further compatible entries. Its initial history never changes.
- An **attempt** executes that run under a renewable lease. Recovery and handoff replace the attempt, preserving the run and its assigned inputs.
- A **checkpoint** is resumable Harness state committed by moving the run's pointer to an immutable object; the display object committed with it is what viewers see. It makes no promise to roll back external effects.

## Tables

The tenant foreign-key rules in [03](03-tenancy.md#tenant-integrity) apply throughout. Object-reference columns hold immutable object keys; run state/display objects and their cleanup are in [07](07-facts-and-delivery.md#objects).

```
sessions
  id  organization_id  workspace_id  labels  created_by_id  version  created_at  updated_at

threads
  id  organization_id  workspace_id  session_id  origin  origin_thread_id NULL
  origin_run_id NULL  origin_tool_call_id NULL  current_run_id NULL  head_run_id NULL
  last_run_id NULL  mcp_headers  archived_at NULL  version  labels  created_at  updated_at
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
  child_run_id NULL  origin_run_id NULL
  request_key NULL  request_digest NULL  request_kind NULL  request_target NULL
  status  assigned_run_id NULL  incorporated_checkpoint_seq NULL  failure NULL
  created_at  finished_at NULL
  kind IN ('message', 'child_result')
  delivery IN ('steer', 'next_run')
  status IN ('pending', 'assigned', 'consumed', 'failed', 'withdrawn')
  UNIQUE (thread_id, position) DEFERRABLE INITIALLY IMMEDIATE
  UNIQUE (workspace_id, principal_id, request_key) WHERE request_key IS NOT NULL
  UNIQUE (child_run_id) WHERE kind = 'child_result'
  CHECK ((payload IS NULL) <> (payload_ref IS NULL))
  CHECK ((kind = 'message') = (agent_id IS NOT NULL))
  CHECK ((kind = 'child_result') = (child_run_id IS NOT NULL AND origin_run_id IS NOT NULL))
  CHECK (status NOT IN ('assigned','consumed') OR assigned_run_id IS NOT NULL)
  CHECK ((status = 'consumed') = (incorporated_checkpoint_seq IS NOT NULL))
  CHECK (status <> 'pending' OR assigned_run_id IS NULL)

runs
  id  organization_id  workspace_id  session_id  thread_id  agent_id  agent_revision_id
  revision_selection  principal_id  authority  options  environment_mounts
  source_entry_id NULL  resume NULL  resumed_by_id NULL  request_key NULL  request_digest NULL
  trigger  lineage  parent_run_id NULL
  status  wait_reason NULL  pending NULL  cancel_requested_at NULL
  current_attempt_id NULL  available_at  attempts  max_attempts  max_usage NULL
  checkpoint NULL  display NULL
  output NULL  output_ref NULL  failure NULL  usage_at_seal NULL  labels  version
  created_at  started_at NULL  sealed_at NULL  updated_at
  revision_selection IN ('pinned', 'default', 'inherited')
  trigger IN ('input', 'queued', 'resume', 'child_result', 'spawned')
  lineage IN ('root', 'continue', 'fork')
  status IN ('accepted', 'running', 'waiting', 'completed', 'failed', 'cancelled')
  wait_reason IN ('approval', 'client_tool', 'user_input', 'multiple')
  CHECK ((source_entry_id IS NULL) <> (resume IS NULL))
  CHECK ((resume IS NULL) = (resumed_by_id IS NULL))
  CHECK (request_key IS NULL OR resume IS NOT NULL)
  CHECK ((lineage = 'root') = (parent_run_id IS NULL))
  CHECK ((status IN ('waiting','completed','failed','cancelled')) = (sealed_at IS NOT NULL))
  CHECK ((status IN ('failed','cancelled')) = (failure IS NOT NULL))
  CHECK (status NOT IN ('waiting','completed') OR (checkpoint IS NOT NULL AND display IS NOT NULL))
  CHECK ((status = 'waiting') = (wait_reason IS NOT NULL AND pending IS NOT NULL))
  CHECK ((status = 'running') = (current_attempt_id IS NOT NULL))
  UNIQUE (thread_id) WHERE status IN ('accepted', 'running')
  UNIQUE (source_entry_id)
  UNIQUE (workspace_id, resumed_by_id, request_key) WHERE request_key IS NOT NULL

run_attempts
  id  organization_id  workspace_id  run_id  number  status  start_reason
  replaces_attempt_id NULL  worker_id  worker_build  harness_run_id NULL
  lease_token_hash  lease_expires_at  heartbeat_at  yield_reason NULL  failure NULL
  created_at  started_at NULL  finished_at NULL  updated_at
  status IN ('leased', 'running', 'succeeded', 'yielded', 'failed', 'cancelled')
  start_reason IN ('initial', 'recovery', 'handoff')
  UNIQUE (run_id, number)
  UNIQUE (run_id) WHERE status IN ('leased', 'running')
  CHECK ((status IN ('succeeded','yielded','failed','cancelled')) = (finished_at IS NOT NULL))
```

`thread_environments` is owned by [06](06-environments.md). `runs.environment_mounts` is the mount set frozen at acceptance, `[{name, environment_id, working_directory}]`, with a GIN index for the active-use query in 06. Source entry and run references can use deferred foreign keys within acceptance. Same-thread pointer constraints cover current/head/last run and source/assigned entries; parent/origin links instead require the same workspace/session. A constraint trigger checks pointer/status relationships at commit, including completed/waiting-only heads.

`head_run_id` is the latest completed or waiting run. `last_run_id` is the most recently sealed run, including failure/cancellation. `current_run_id` is accepted or running. A failed run's checkpoint remains inspection evidence but never becomes head.

`runs.checkpoint` and `runs.display` are typed pointers (digest, size, format, sequence and producing attempt; the display pointer also records the stream position it covers) to the run's latest committed immutable state and display objects. They move only in fenced worker transactions and freeze at seal. The pointer, not an object's presence, is the recovery authority; database lifecycle and inbox disposition are never rolled back to match object bytes.

Thread `version` changes on every visible inbox, mount, pointer, header, label or archive change. It is the thread ETag for inbox, mount and header edits and the change signal for thread streams in [07](07-facts-and-delivery.md#the-thread-stream). Entry positions are positive bigints; allocation/reordering takes the thread lock. Reordering defers uniqueness until all affected positions are set.

`threads.mcp_headers` is set when the thread is created and edited with `If-Match`; acceptance freezes it into the run's options. [04](04-resources.md#caller-headers) owns its validation and inheritance. Message delivery is selected by the caller; child results use `steer`.

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
        run = await accept(session, thread)                       # None while the thread is busy
        return Submitted(entry=entry, run=run)
```

Only messages use this path; answers to approval and client-tool requests use [resume](#waiting-interrupt-and-fork). Archived threads refuse public submissions. Waiting or failed threads may receive messages; source eligibility below determines whether they start a run or remain pending. Malformed/unauthorized requests fail without appending. Payload limits and the capacity rules below are enforced under the thread lock. Preparation and failed duplicate requests cannot evade upload limits.

### Inbox capacity

The thread's input usage is the count and payload bytes of its **pending plus assigned entries**. Assignment and return to pending do not release or acquire capacity. Consumption, failure and withdrawal release an entry's capacity once. Recovery retains its assignment and occupancy. Pending edits recheck the resulting byte total under the same thread lock. A valid child result that cannot fit remains in its retryable outbox delivery, without inserting or settling it. Do not fail or discard it merely because the inbox is full. One query/predicate owns this accounting; no separate pending/assigned counters or allowance for returning entries is needed.

Resume never enters the inbox, so a full inbox cannot block the resolution of a wait; its request has its own bounded size and answer count.

The boundary delivery batch is separately bounded. Its configured count/byte defaults are still to be selected from vertical-slice measurements; this design does not specify one or ten entries.

### Source selection

`start_run(session, thread, source)` is the only run-creation function. `accept(session, thread)` selects a queued source and calls it; [resume](#waiting-interrupt-and-fork), [fork](#waiting-interrupt-and-fork) and [spawn](#child-runs) call it with their own source. Both are SQL-only.

`accept`:

1. Return if archived or already active.
2. Choose an eligible source below; revalidate the source principal, its authority and the agent. Fail rejected entries, examining only a bounded batch.
3. Call `start_run` with the chosen entry.

`start_run`:

1. Resolve the intended revision and mount set with ordinary reads, then acquire all required locks in the order below and revalidate versions/state. Freeze revision, normalized options, the thread's `mcp_headers` and authority; run core checks and additional admission policy.
2. Insert the accepted run, assign its source entry if it has one, reserve/freeze mounts as in [06](06-environments.md), and update thread current/version.
3. Stage matching lifecycle webhooks ([07](07-facts-and-delivery.md#lifecycle-webhooks)) and register the after-commit claim wakeup ([09](09-runtime.md#worker-claim-wakeups)). Nothing is yet consumed.

| Thread condition                                                                          | Eligible source                                                                                                                                                                                                                                                                                                                                                                     |
| ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Archived, or an accepted/running run exists                                               | None                                                                                                                                                                                                                                                                                                                                                                                |
| No previous run, or latest run completed                                                  | Lowest-position pending message or valid child result                                                                                                                                                                                                                                                                                                                               |
| Head waiting only for user input, with no later failed/cancelled run                      | Lowest-position pending message; close the unanswered question calls with no-response results, then inject this message                                                                                                                                                                                                                                                             |
| Head waiting with any approval or client-tool request, with no later failed/cancelled run | None; only resume continues it. Messages and child results stay pending                                                                                                                                                                                                                                                                                                             |
| Latest run failed/cancelled                                                               | Automatic advancement stops. An explicit submission may start **that new message** using the unchanged initial-history rule below, subject to the same waiting-type restrictions when the head is waiting; resuming a waiting head is also explicit. Unrelated pending entries do not replace its source. There is no retry operation; to run the same input again, submit it again |

Select by thread-state eligibility, then position. Inspect the entire sealed pending set: a wait is question-only only when every item is `user_input`; a mixed set requires resume. `wait_reason` summarizes one kind or `multiple`, but never replaces the per-item check. Child results do not release any wait.

After explicit restart, compatible pending steers may join it; `next_run` messages stay queued. Inputs assigned to the ended run are excluded. A child result is not an explicit user action that lifts this pause.

Messages select their requested revision or the default at acceptance (`pinned`/`default`). A resumed run inherits its waiting run's revision/options; a child result starting a run inherits its origin run's (`inherited`). Options (allowed overrides, limits and the frozen `mcp_headers`) are validated and stored on the run, never reconstructed from an editable entry, a changed default or a later thread edit. Resume cannot change them. New messages have their own options; omission uses documented defaults.

Choose initial history by one rule: use this thread's `head_run_id` when present; otherwise a fork thread uses its immutable `origin_run_id`; otherwise start with empty history. These produce continue, fork and root lineage respectively. The fork fallback persists through any number of failed/cancelled runs until this thread has its own completed/waiting head. It never uses the failed run's checkpoint. Existing origin/head fields suffice; no retry-specific baseline is stored. Parent/revision never move after acceptance. Per-entry rejection uses a typed result or savepoint; database failures roll back and retry the transaction.

## Assignment and incorporation

The durable state is **assigned to a run**, not offered by an attempt:

```
pending ──accept / boundary──> assigned ──checkpoint commit──> consumed
   │                              │
   └──reject / withdraw           ├──completed / waiting, not incorporated──> pending
                                  └──failed / cancelled, not incorporated───> failed
```

Recovery and handoff do not change assignment. `offered` is a worker-local set for one Harness instance. This removes an unnecessary second recovery state machine.

At a boundary, lock thread → run → attempt, check authority and assign a bounded FIFO batch of compatible pending steers. The source was assigned at acceptance and is incorporated first, even if source eligibility skipped earlier entries; FIFO order applies to the boundary-delivered steers. Materialize and offer after releasing the transaction. Assigned entries cannot be edited or withdrawn.

Compatibility is a configuration rule, not an identity rule. A message may steer when its selector permits this agent/revision and its normalized options match the run's. Headers are a thread setting and take no part in the comparison. Anyone with the `run` verb on the thread may steer; the run keeps the principal and authority frozen at acceptance, and a steer never changes them. Messages require an explicit `agent_id`; an omitted revision permits the current run's revision for steer matching. A mismatch leaves the message for a later run.

The Service host capability attaches `(run_id, entry_id)` provenance. An **awaited incorporation hook before history replacement or compaction** tells the worker which offered entries the exported state contains. Enqueue acceptance, observer events and an empty event queue are not evidence of incorporation. Prove this integration before expansion; see [12](12-validation.md).

A **checkpoint commit** writes the complete state and the current display as new immutable objects outside any session ([07](07-facts-and-delivery.md#objects)). Then one short transaction locks thread → run → attempt and the input rows, verifies the worker predicate and that `runs.checkpoint` is still the one this attempt last committed, moves both pointers, marks the entries the exported state incorporated `consumed` with `incorporated_checkpoint_seq`, ingests pending usage and commits. The commit is the checkpoint's durability point. Consumption can therefore never run ahead of or behind the state it describes, and capacity is released exactly once in that transaction. After commit the worker deletes the superseded objects.

On recovery, read the pointers, load and verify the named objects and clean the run's object prefix ([07](07-facts-and-delivery.md#objects)) **before** delivering new input or entering the Harness. Entries marked `consumed` are in the restored state; reoffer only still-assigned entries, once per new Harness instance. **Do not feed the source again just because an attempt started.** Without a checkpoint, initialize from the parent's selected state, then offer the source entry or apply the resume answers. Incorporation after the last committed checkpoint may be replayed.

When a failed/cancelled seal wins, still-assigned entries become `failed`; consumed entries stay consumed. A completed/waiting seal returns still-assigned entries to pending. Object bytes that no committed pointer names are never read and cannot change input disposition.

**Keeps:** at-most-once incorporation per entry into its assigned run's committed continuation. This is not exactly-once model requests, tool effects or sandbox changes. Unknown external effects follow Harness/provider replay rules; unsafe calls need reconciliation or human input rather than automatic repetition.

Before an unsafe side-effecting tool dispatch, commit a safe pending-call checkpoint or obtain a provider/tool durable operation receipt with a stable call identity. Otherwise a crash can roll back past the call's existence, and no recovery policy can recognize its unknown outcome. Use the Harness's `tool_recovery="declared"`, never blanket `always`. Undeclared pending calls are closed with unknown-effect results; a later model decision can still request another call, so tool approval/idempotence remains necessary. The real pre-dispatch checkpoint/receipt integration is a vertical-slice gate, not an assumed API.

## Claim, heartbeat and authority

The existing periodic worker scan also wakes early on a Redis marker. Markers follow the commit that makes a run accepted and are registered only by `start_run` and by the transition that returns a run to accepted; they never select a run or grant ownership. [09](09-runtime.md#worker-claim-wakeups) owns the rules.

Workers reserve local capacity before selecting a due accepted run with `FOR UPDATE SKIP LOCKED`. In one short transaction recheck cancellation, attempt limits, archive state and build/checkpoint compatibility, insert the attempt, increment the count, set running/current, and stage matching lifecycle webhooks. Claim locks run → attempt and never subsequently locks thread. Archive/interrupt take thread → run, so the run lock arbitrates with claim.

`runs/attempts.py` owns the single worker predicate, evaluated after relevant locks with fresh database time:

```
run.status = running
AND run.current_attempt_id = attempt.id
AND attempt.status IN (leased, running)
AND attempt.worker_id = caller.worker_id
AND attempt.lease_token_hash = hash(caller.token)
AND attempt.lease_expires_at > clock_timestamp()
```

Every execution-dependent database mutation uses it: checkpoint commits, assignment, child spawn, worker environment requests and worker seal. Expired workers cannot revive leases even before the sweep. `now()` is transaction-start time, unsuitable after lock waiting. Usage ingested outside a checkpoint commit is the explicit exception: it records a past charge. Redis streams cannot publish authoritative state.

State and display objects are create-only and written outside sessions. A stale attempt can still write bytes, but only the fenced checkpoint commit makes them reachable, so late bytes are garbage, never state. Do not start an object write within one write timeout of the local lease deadline, so that a stale attempt's writes land before any takeover cleans the run's prefix. [07](07-facts-and-delivery.md#objects) owns keys, verification and cleanup.

Heartbeat starts immediately after claim in a supervised task, at one third of the lease. It uses the same predicate and extends from database time. If renewal is not confirmed before a conservative local deadline, stop new dispatch and cancel supported in-flight work. Already sent calls can still complete. Cancellation/grant refresh have independent bounded polling too; long requests do not defer them to model boundaries.

The expiry sweep locks thread → run → attempt, rechecks current identity and expiry, then closes the attempt. The run becomes accepted with backoff, failed on exhaustion, or cancelled on interrupt. Terminal attempts are immutable. Recovery/handoff preserve the run; no sweep guesses what inputs a dead worker saw.

## Execute

`execute` owns orchestration; adapters own their I/O, never domain persistence. No session survives an external call or wait.

1. Load revision/options/authority; revalidate current principal, grants and live referenced resources.
2. Read the pointers, load and verify the named objects and clean the run's prefix. If the restored state already holds a completed/waiting outcome, seal it without repeating execution; database terminal state always wins. Output produced after the last checkpoint by an earlier attempt is regenerated, not restored. See [07](07-facts-and-delivery.md).
3. Obtain frozen mounts through the environment protocol. Resolve credentials/OAuth through resource services. Build typed capabilities with an async exit stack for partial-setup cleanup. A generic `open()` loop is not a requirement.
4. Offer the source entry while it is still assigned, or apply the resume answers when starting without a checkpoint. Before each paid call check cancel, authority, limits and the admission policy's `proceed`. Ingest usage after results. Service tools use the run's bounded authority and the same services as HTTP.
5. At safe boundaries commit a checkpoint as above, then select and offer steers for the next request. Before finishing, drain awaited incorporation hooks and commit a final checkpoint.
6. Seal, or yield at a safe boundary for handoff. Provider cleanup is outside seal.

Setup errors such as missing credentials fail with actionable reasons. Transient failures use bounded recovery/backoff within `max_attempts`. Unknown effects do not automatically become retryable errors. Limits aggregate across attempts and late usage. Recorded-usage checks have the soft-limit semantics defined with the policy seam in [09](09-runtime.md#extension-points).

## Seal and successor scheduling

Seal changes one thread. Cross-thread delivery uses the outbox after commit.

| Caller                    | Authority                                                |
| ------------------------- | -------------------------------------------------------- |
| Worker                    | Full unexpired worker predicate                          |
| Interrupt of accepted run | Thread/run locks; still accepted without current attempt |
| Lease sweep               | Thread/run/attempt locks; still current and expired      |

A completed/waiting seal writes no objects: the worker's final checkpoint commit already holds the outcome (the exact pending set for waiting), and the seal verifies that the pointers are still the ones it committed. Failure/cancellation selects no continuation state; the checkpoint pointer keeps its last value as inspection evidence. A worker sealing failed/cancelled may first write a display object carrying its in-memory tail marked interrupted and move only the display pointer in the seal transaction. Sweep and interrupt seals write nothing; readers show any `in_progress` item of a terminal run as interrupted. Resolve uncertain database commits by rereading lifecycle and pointers.

Inside thread → run → attempt locks:

1. Verify caller/outcome under the current authority.
2. Yield/recoverable failure: close the attempt, clear current attempt, set accepted/available time, stage matching webhooks, register the claim wakeup when immediately due, and commit. Assignments stay. Otherwise continue below.
3. Close the attempt if present, seal outcome and `usage_at_seal`, clear current attempt, update thread last/current. Completed/waiting also advance head.
4. Completed/waiting return still-assigned entries to pending. Failed/cancelled fail all still-assigned entries. **Consumed entries stay consumed**: they record committed incorporation, not successful work. Neither kind automatically enters a later run after failure.
5. Enqueue deduplicated child-result delivery for a child thread's completed/failed/cancelled run. Waiting emits no result delivery.
6. Stage matching lifecycle webhooks; commit. After commit, clean the run's object prefix.

```python
async def seal(caller: SealCaller, run_id: str, outcome: Outcome) -> None:
    async with transaction() as session:
        thread, run, attempt = await lock_chain(session, run_id)  # thread → run → attempt
        caller.verify(run, attempt)                               # worker predicate, interrupt or sweep
        ...                                                       # steps 1–6
    await clean_run_objects(run_id)   # keep only what the frozen pointers name
    await advance(thread.id)          # its own transaction: accept(); advance_threads covers a crash here
```

Successor creation is a separate `accept` call after commit. An indexed `advance_threads` sweep recovers a lost call; submission and child delivery can also call it. Head/last/ current and pending entries are durable work evidence. Invalid inputs or slow admission cannot prevent sealing. The sweep uses bounded batches and `SKIP LOCKED`, with a starvation test for repeated invalid entries. Worker cleanup only closes clients; environment stop/destroy belongs to its durable lifecycle.

## Waiting, interrupt and fork

**User questions** (`ask_user_question`, pending kind `user_input`) receive ordinary messages, including free text submitted through a question UI. At a question-only wait, create the message's successor from the waiting checkpoint, complete the question calls with no-response results and inject the message once through the usual incorporation protocol. Do not fabricate structured answers or leave unresolved tool calls in the resumed history. The message retains the normal agent/revision/options and execution-identity rules. Ordinary messages never implicitly approve or reject a pending approval, or complete a client tool, in the same thread.

**Resume** answers a wait containing approval or client-tool requests: `POST /runs/{run}/resume` with answers that each carry a `tool_call_id`. Under the thread lock the run must be this thread's idle waiting head; otherwise the request fails with `conflict` and nothing is stored. The same Harness tool call can suspend more than once for different approval sources, so the waiting run, not the call ID alone, identifies the suspension: an old approval card for a superseded wait conflicts. Duplicate/unknown call IDs and result-kind mismatches are invalid. Never infer the target from a newer head or reinterpret answers as a message.

Normalize the answers against the complete sealed pending set. Use each supplied approval decision or client-tool result; omitted approvals become denied, omitted client results become no response, and unanswered user questions in a mixed set become no response. An empty answer list applies all defaults to that exact wait. The UI states that omitted approvals will be denied. The normalized batch is stored as the successor's `resume` and passed to the existing Harness deferred-resume contract; do not persist partial approval progress or add a waiting state. Ordinary text uses a message.

Any principal with the `run` verb may resume; the successor records them as `resumed_by_id`. `start_run` creates the successor in the same transaction with `trigger = 'resume'` and continue lineage; it inherits the waiting run's revision, options, principal and authority, and resume cannot change them. The request requires an `Idempotency-Key`, recorded on the successor ([10](10-api.md#idempotency)). A concurrent second resume conflicts. If the successor fails or is cancelled, the waiting run is still the head and can be resumed again explicitly; answers are never replayed automatically.

**Interrupt** records cancellation on accepted/running; repeating is a no-op. Accepted runs seal immediately. Running workers cancel supported calls and seal at a safe boundary; expiry handles missing workers. Repeating on cancelled returns that result; other sealed outcomes conflict. Cancellation neither undoes effects nor implicitly cancels children.

**There is no retry operation**. A failed or cancelled run never becomes head. The next message uses the unchanged baseline selected by [source selection](#source-selection); running the same input again is submitting it again, pinned to the same revision if wanted, and the Console can prefill it. Cancelling “write a poem” then submitting “hello” never reruns the poem. Recovery of a crashed attempt is not retry: it resumes the same run from its last checkpoint within `max_attempts`.

**Fork** requires a completed/waiting origin and retained compatible checkpoint. Create thread/source atomically using the entry request key. Copy desired mounts, unless fresh environments were requested, and `mcp_headers`. An explicit message fork of waiting history closes inherited pending calls with denial/no-response results in the new branch; it neither grants an approval nor resolves the original thread's wait. This existing new-thread operation is separate from ordinary messages continuing a waiting thread. Failed/cancelled runs are not fork origins: after history advanced, fork their original parent and resubmit (or create a thread for root input); the UI can prefill the input.

**Archive** replaces destructive interactive thread deletion. Under thread lock, block acceptance, withdraw pending inputs, remove desired mounts and request active cancellation. An accepted run is sealed cancelled by the same archive operation; a running run gets an interrupt request. Frozen run mounts remain until it ends. Later child notifications are delivered-but-ignored. Retain facts/keys in v1; no physical purge or unarchive.

## Child runs

`spawn_child` checks parent worker authority under thread → run → attempt, enforces depth/count limits, and atomically creates child thread/message/first run. The child thread copies the parent run's frozen `mcp_headers`. Pinned subagent revision and authority come from the parent definition. Unique `(origin_run_id, origin_tool_call_id)` is stable across recovery, never attempt-local. Authorized replay returns the same child/initial run regardless of its current outcome.

Child seal enqueues a result keyed by sealed child run ID. Delivery locks only the parent thread, validates immutable origin/current state, then inserts the unique entry and settles the internal outbox row in one commit. Every completed/failed/cancelled child run reports, including successors after waiting. This is notification of that run, not a second completion of the original tool call.

Eligibility requires the origin to be current or an ancestor of the parent's committed head. Active origins can receive compatible steers. Failed/cancelled origins that never became history fail the result entry with `origin_not_committed`; they are not revived. Waiting history retains results until a message or resume eligible under [source selection](#source-selection) starts a successor. A later failure still pauses automatic advancement. The child remains directly inspectable in all cases.

## Lock discipline and guarantees

Multi-row domain locks: thread → run → attempt → environment, sorting IDs within a set. Resource heads needed for acceptance follow these; resources never lock runs/threads. Object I/O is outside database locks. Claim/heartbeat may take a suffix, never a preceding lock. Child delivery and successor acceptance are separate transactions. PostgreSQL deadlock/serialization errors still receive bounded whole-transaction retries; the convention is not an impossibility proof for all database deadlocks.

**Keeps:** one active run per thread, one live attempt per run, immutable terminal facts, state changes and their webhook outbox rows in one database commit, checkpoint pointers and input consumption in one fenced transaction, one worker predicate, no session across external I/O, recoverable successor/child delivery. Adversarial schedules and implementation gates are in [12-validation.md](12-validation.md).
