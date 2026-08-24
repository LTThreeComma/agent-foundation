# Output Policy and Retention

## Design Position

Every envd retained byte-output producer applies a finite effective EIP `OutputPolicy` while bytes are produced. A request can provide a narrower policy; when it omits one, envd uses the generous finite hard limits advertised in its descriptor with `overflow="truncate"`. The policy determines what can remain inline, whether overflow fails, truncates, or uses a daemon-owned retained reference, and the maximum bytes that can be captured for one operation. Daemon-global and per-object safety limits bound aggregate retained storage and object count.

The Harness owns [`ToolOutputPolicy`](../agent-harness/07-tool-execution.md#tool-metadata) as a model-result policy. Envd does not deserialize Harness tool metadata, tool identity, or Pydantic objects. The EIP adapter maps an explicitly narrower Harness decision into the protocol-native `OutputPolicy`; otherwise it can omit the field and use the advertised envd default. Envd understands and enforces the effective provider contract at the producer.

## Boundaries

| Concern                                                                     | Owner                                             | Relationship                                                   |
| --------------------------------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------- |
| Tool-level inline/total policy, overflow selection, and managed redaction   | Harness `ToolOutputPolicy` and result-safety path | Computes a model-result ceiling before provider dispatch       |
| Protocol-native optional per-operation output policy and result disposition | This document                                     | Omitted for the advertised daemon default or sent to narrow it |
| Daemon-global aggregate retained quotas                                     | `agent-envd`                                      | Non-disableable hard ceilings                                  |
| Native retained-output production and stream identity                       | Command, process, or output-read owner            | Supplies bytes incrementally                                   |
| Native file readers and staged writers                                      | [Resource Operations](04-resource-operations.md)  | Use the raw transfer plane, not an `OutputReference`           |
| Host artifact storage or durable checkpoint                                 | Host                                              | Not implied by an envd retained object                         |

EIP output policy grants no filesystem, process, or content authority. Redaction remains a Harness concern because envd does not know model-facing tool semantics or all secret classes. Envd still removes its own transport and bootstrap secrets before production and never writes them into retained output itself.

## EIP `OutputPolicy`

This schema is serialized EIP JSON:

```python
type OutputOverflow = Literal["fail", "truncate", "retain"]


class OutputPolicy(BaseModel):
    max_inline_bytes: int
    max_output_bytes: int
    overflow: OutputOverflow
```

When present, both values are positive and finite, and `max_inline_bytes <= max_output_bytes`. `max_inline_bytes` is the largest complete output that can remain directly in one EIP result. `max_output_bytes` is the largest amount the operation can capture in a retained representation when `overflow="retain"`; it is not permission to construct an equally large JSON response.

The descriptor's `max_inline_output_bytes` and `max_output_bytes` are both hard maxima and the omitted-policy values. Omission therefore selects:

```python
OutputPolicy(
    max_inline_bytes=descriptor.limits.max_inline_output_bytes,
    max_output_bytes=descriptor.limits.max_output_bytes,
    overflow="truncate",
)
```

An explicit request is intersected with envd method, response, daemon-global, Environment-provider, Host, and Harness ceilings. No layer can widen an upstream ceiling. `fail` remains fail when required by an upstream layer; `truncate` cannot be upgraded to retention; `retain` is available only when the trusted adapter and envd both permit a retained sink. The effective policy is decided before producer dispatch and remains finite.

### Harness translation

For a managed Environment tool, translation is:

| Harness `ToolOutputPolicy`         | EIP `OutputPolicy`                                                                                                    |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `max_inline_bytes`                 | Same or narrower value                                                                                                |
| `max_output_bytes`                 | Same or narrower value                                                                                                |
| `overflow="fail"`                  | `overflow="fail"`                                                                                                     |
| `overflow="truncate"`              | `overflow="truncate"`                                                                                                 |
| `overflow="environment_reference"` | Explicit `overflow="retain"` when the selected binding supports finite retained output; otherwise explicit `truncate` |
| `redact`                           | Not serialized; the Harness applies its managed redaction pass to the bounded result and preview                      |

When Harness policy is no narrower than the advertised envd default and uses truncation, the adapter omits `output_policy` canonically. Descriptor method availability for both `output.read` and `output.release` means the daemon supports producer-created retained references; without those methods, an explicit `overflow="retain"` request is `unsupported`, while omitted or explicit truncation remains available. The adapter never copies `HarnessToolMetadata`, `tool_id`, effects, credentials, or arbitrary metadata into EIP. It also does not expose the raw EIP selector as bearer-like model text: it wraps the selector as a binding-scoped logical Environment reference, and every model-facing follow-up read crosses the same Harness authorization, size, and redaction path. This preserves output-safety semantics without coupling envd to Pydantic AI.

## Encoded Bytes and Dispositions

Bounded binary values that inherently remain in JSON control or retained-output methods use this serialized shape:

```python
class EncodedBytes(BaseModel):
    encoding: Literal["base64"]
    data: str


class OutputSegment(BaseModel):
    start_offset: int
    data: EncodedBytes


class OutputPreview(BaseModel):
    segments: tuple[OutputSegment, ...]
    represented_bytes: int


type OutputKind = Literal[
    "empty", "inline", "retained", "truncated"
]


class OutputCapture(BaseModel):
    kind: OutputKind
    producer_complete: bool
    content_complete: bool
    produced_bytes: int
    captured_bytes: int
    dropped_bytes: int
    inline: EncodedBytes | None = None
    preview: OutputPreview | None = None
    reference: OutputReference | None = None
    available_start: int
    available_end: int
    expires_at: datetime | None = None
```

Counts refer to raw producer bytes before base64 and JSON framing. `produced_bytes` is the number observed at the snapshot boundary; it can grow for a live process. `captured_bytes` is currently readable inline or through the reference. `dropped_bytes` counts known bytes that will never be readable through this object.

`producer_complete` means the requested byte source or process stream has reached a terminal producer boundary. `content_complete` means every byte in that logical output through the producer boundary remains available. A live process normally has both fields false even when no byte has been dropped. A terminal operation with dropped bytes has `producer_complete=true` and `content_complete=false`.

`available_start` and `available_end` define the half-open logical offset interval currently addressable by explicit reads and always satisfy `available_start <= available_end`. A retained ring or quota policy can advance the floor; any missing interval is explicit. A preview is a bounded set of offset-labelled head and/or tail segments and never pretends those segments are contiguous when a middle gap exists.

The disposition fields have a small generated structural contract. `empty` has zero counts, a zero interval, and no inline, preview, reference, or expiry. `inline` has `inline` and none of preview, reference, or expiry. `retained` has a reference and expiry, has no inline value, and can include a preview; it can still have `content_complete=false` after a capture ceiling or retention-floor advance. `truncated` has no inline value, reference, or expiry and can include a bounded preview. Complete process and operation lifecycle coherence remains owned by the daemon domain state rather than duplicated in generated models.

## Overflow Behavior

### Inline success

When the complete output is no larger than `max_inline_bytes` and the final JSON response fits the daemon response ceiling, envd returns `kind="inline"`, complete bytes, and no retained object. Response framing overhead is separately reserved, so a policy equal to the response hard limit is narrowed enough to fit a valid envelope.

### `fail`

When output crosses `max_inline_bytes`, envd:

1. stops the producer when the operation is safely stoppable;
2. for a running command, requests tree termination because a successful complete result can no longer satisfy the selected policy;
3. continues bounded pipe drain and native cleanup so the producer cannot deadlock;
4. if the initiating EIP operation is still active, returns `output_limit_exceeded` with `dispatch_stage`, captured/dropped counts, command status where known, and a side-effect receipt when applicable;
5. if `process.start` already returned, records the later background-process failure as `phase="failed"` and `termination_reason="output_limit"`, while `process.inspect` and `process.wait` expose the bounded output counts and cleanup outcome.

The already successful `process.start` response is never rewritten. Failure does not roll back output bytes already observed or command side effects that occurred before termination. A mutating operation therefore can fail its result policy while its receipt reports dispatched or completed effects. Clients reconcile before retry.

`fail` creates no general retained output object. A bounded safe preview can appear in error data only when content policy permits it; by default the error contains counts and references to side-effect evidence, not output content.

### `truncate`

Envd retains at most a bounded preview no larger than `max_inline_bytes`, discards remaining producer bytes while continuing safe execution and drain, and returns `kind="truncated"` with explicit counts and completeness. It creates no output reference.

For a streaming process, counts are snapshots and can increase. The configured per-process output ceiling determines when later bytes are dropped. A terminal process never changes a previously reported gap into complete content.

### `retain`

Envd first atomically reserves retained-object metadata plus private spool capacity. It appends output to generation-private spool files up to `max_output_bytes`, retains only a bounded preview in memory, and supplies references for the output streams that exist. If total produced output exceeds the per-operation capture ceiling, later or policy-selected bytes are dropped and the offset/gap fields report the exact available region or segments.

If aggregate quota cannot reserve a safe retained sink before overflow, envd falls back to explicit bounded truncation. It does not buffer while hoping for quota, evict another live object, exceed a ceiling, or silently return an incomplete inline value as complete. A Host policy that requires failure rather than this safe fallback selects `overflow="fail"` before dispatch.

## Producer-side Enforcement

Output safety begins before full materialization:

```mermaid
flowchart LR
    Producer[Native producer] --> Counter[Incremental byte or item counter]
    Counter --> Inline[Bounded inline buffer]
    Counter -->|retain permitted and reserved| Store[Bounded retained store]
    Counter -->|beyond policy| Drop[Count and discard]
    Inline --> Result[Bounded EIP result]
    Store --> Reference[Opaque reference and explicit offsets]
    Drop --> Result
```

Command stdout and stderr are drained concurrently. File text, list, find, and search methods use their own fixed response ceilings and explicit line or item offsets; they are not retained output and do not accept `OutputPolicy`. Raw file readers and writers use the independently bounded binary transfer plane, and cross-mount copy streams from source to destination without routing full content through a JSON result or retained object. No default implementation can call an unbounded “read all” API and apply policy afterward.

A producer can keep a bounded head/tail preview using fixed buffers. Retained storage records stream identity and logical offsets. For command output, stdout and stderr have independent offset domains while sharing the operation's total captured-byte and object quotas; interleaving timestamps are observations and not a deterministic total order unless a method explicitly provides a merged stream.

The output reader itself is subject to another `OutputPolicy`. Reading a large retained object never forces an equally large response; the client advances its explicit byte offset through bounded chunks.

## Aggregate Retention Quotas

Per-operation limits do not prevent many small objects from exhausting a daemon. Envd enforces finite ceilings at the scopes that matter for its one-user model:

| Scope                | Purpose                                                                 |
| -------------------- | ----------------------------------------------------------------------- |
| Daemon global        | Bounds total disk/memory and object metadata across all sessions        |
| Process or operation | Applies `max_output_bytes`, object count, and method-specific retention |

Quota accounts cover retained command/output bytes, object metadata, previews stored outside the response, process output, and equivalent daemon-owned output objects. Private file-writer staging uses the separate aggregate transfer-staging quota; it cannot consume or hide inside retained-output capacity. Metadata overhead has its own bounded accounting and cannot be made unbounded with zero-byte objects.

Reservation is atomic and precedes object creation or growth. Streaming growth reserves in bounded increments before writing. Failure keeps the prior valid object unchanged and applies the selected overflow behavior. Envd never oversubscribes, evicts an independently retained object to satisfy another allocation, or counts sparse file logical size as free. Process-owned output follows the explicitly bounded process-record lifetime rather than becoming a second detached object lifecycle.

Release, expiry, failed creation rollback, terminal process-record release or capacity reclamation, and daemon drain return quota exactly once through the single retention owner. Session close does not release output. Retained state never survives daemon restart, so startup creates an empty store and a fresh generation rather than reconstructing accounting.

## References and Explicit Offsets

An `OutputReference` is opaque and bound to Environment identity and generation, the daemon user, producer kind, original operation and request shape, retained object, and expiry. It is not a native path or bearer credential.

Read positions are client-owned non-negative byte offsets, not daemon-created selectors. Two clients can read the same reference independently without creating cursor records or consuming one another's bytes.

`output.read` uses:

```python
class OutputReadParams(BaseModel):
    context: EIPCallContext
    reference: OutputReference
    start_offset: int
    output_policy: OutputPolicy | None = None


class OutputReadResult(BaseModel):
    chunks: tuple[OutputSegment, ...]
    capture: OutputCapture
    next_offset: int
```

An initial sequential read uses `start_offset=0`; a caller continues from `next_offset`. The result contains only contiguous chunks actually available. When the requested offset is below `available_start`, inside a known gap, beyond an expired object, or otherwise lost, envd returns `retention_gap` with safe available bounds and terminal metadata. It never skips a gap or marks missing bytes complete.

`output.release` uses these serialized shapes:

```python
class OutputReleaseParams(BaseModel):
    context: EIPCallContext
    reference: OutputReference


class OutputReleaseResult(BaseModel):
    released: bool
    receipt: OperationReceipt
```

Releasing a reference invalidates that retained object and frees its exact quota once. Repeated release converges while a bounded internal tombstone remains. Releasing process output does not kill the process; subsequent bytes are drained and counted as dropped under that process's active policy, and envd does not create an implicit replacement object.

## Expiry and Generation Lifetime

Every retained object has finite internal idle and maximum lifetime policy. The server returns `expires_at` as its current expiry observation, not as a minimum availability lease. Client activity can refresh internal idle expiry only within the configured maximum and current policy. Expiry is enforced even if no cleanup request arrives.

Objects are daemon-generation-owned rather than session-owned. Stdio or reverse-WebSocket carrier loss, session close, and Harness run completion do not release them. A fresh initialized session in the same generation can continue from a reference and explicit offset while its record remains available. Explicit release, expiry, terminal process-record release or capacity reclamation, failed-object cleanup, or daemon shutdown reclaims them. No output reference survives daemon restart, and storing one in Host state does not extend its lifetime or make it recoverable.

## Storage and Security

Retained data lives in append-only generation-private spool files under an envd-owned runtime subtree outside EIP mount authority and command grants. The native path can still lie beneath an intentional `/` or volume-root mount; Resource Operations subtracts the opened spool/runtime identities from every direct and traversing EIP file method, while required execution isolation independently subtracts them from payload projections. Each daemon start creates fresh home, temporary, spool, and control subtrees and never reuses fixed prior-generation contents. File names are random internal identities, permissions/ACLs are restrictive defense in depth, sparse allocation is charged by owned physical/logical policy rather than treated as free, and path lookup never uses caller strings directly.

At-rest encryption is deployment policy. If retained output can contain sensitive business data, the provider selects an encrypted state volume or an encrypted retained-object adapter. EIP references reveal neither path nor storage key. Host storage does not copy retained bytes unless explicitly exported as an artifact through another contract.

Normal logs, metrics, traces, errors, and receipts contain byte counts and outcome classes, not output content. Output content reaches only the authenticated result/reference path and then remains subject to Harness redaction and content policy.

## Failure Semantics

| Failure                                                | Result                                                                                       | Guarantee                                               |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Invalid or widening policy                             | `invalid_params` before dispatch                                                             | No producer starts for that request                     |
| Inline response would exceed hard frame limit          | Effective inline limit narrows or selected overflow applies                                  | Frame remains bounded                                   |
| Fail-on-overflow threshold crossed                     | `output_limit_exceeded`, producer stop/command termination request, and side-effect evidence | No unbounded buffer; prior effects not hidden           |
| Truncate threshold crossed                             | Explicit incomplete capture with dropped counts                                              | Producer can complete while excess is drained/discarded |
| Retain reservation unavailable                         | Explicit truncation, or fail when upstream selected fail                                     | No oversubscription or live-object eviction             |
| Retained object reaches `max_output_bytes`             | Further bytes dropped with explicit offsets/counts                                           | Object remains bounded                                  |
| Explicit offset below retention floor or inside gap    | `retention_gap`                                                                              | No false continuity or completeness                     |
| Reference expired, released, lost, or generation-stale | `retention_gap`, `invalid_handle`, or `stale_generation`                                     | No silent retargeting                                   |
| Storage write fails after producer dispatch            | Bounded provider/output failure with receipt and known captured counts                       | Mutation outcome remains separately classified          |
| Release/expiry cleanup fails                           | Quota remains conservatively charged and daemon reports cleanup fault                        | No quota undercount                                     |

## Compatibility

`OutputPolicy` field meaning, overflow behavior, byte-count domain, completeness, offset semantics, reference scope, and gap behavior are protocol compatibility facts. Changing `retain` to imply unbounded storage, treating a preview as complete, changing raw-byte counts to encoded-byte counts, or making reads consume shared bytes requires an incompatible EIP revision.

Harness translation is tested independently from EIP wire fixtures. Cross-carrier conformance verifies identical output policy, base64, preview, reference, quota, explicit-offset, release, expiry, and error behavior.

## Trade-offs

### Explicit references for produced output, raw streams for native files

References and a private spool add lifecycle and storage accounting for command and process output. Client-owned offsets avoid another selector lifecycle while keeping result envelopes, Harness memory, and model values bounded. Native file queries use explicit offsets, while native file download/upload uses a session-scoped raw reader or staged writer; treating either as retained output would add redundant storage and cursor lifecycle.

### Provider enforcement plus Harness redaction

Two layers perform different work: envd bounds raw production; the Harness validates/redacts the already bounded model-facing result. Combining them would either require envd to understand tool metadata or allow unbounded data to cross the provider boundary first.

### Aggregate reservation over best-effort caching

Atomic quota reservation can truncate an operation even when disk has incidental free space. It prevents one caller from evicting another valid reference and makes resource behavior predictable under load.

## Invariants

01. Every retained-output-producing EIP method accepts an optional finite valid `OutputPolicy`; omission uses the descriptor's generous finite hard maxima with `overflow="truncate"`. Raw file transfer follows its separate finite transfer contract.
02. Envd applies output bounds while consuming the producer, never after unbounded materialization.
03. Harness `ToolOutputPolicy` maps to EIP policy without serializing Harness metadata or redaction configuration.
04. Inline output contains a complete value only when every byte fits both inline and response ceilings.
05. Fail, truncate, and retain overflow behaviors are explicit and never silently substitute an unbounded or falsely complete result.
06. Command pipes continue bounded drain or tree termination after overflow so output backpressure cannot deadlock the daemon.
07. Per-operation limits and finite daemon-global aggregate byte/object quotas both apply, with reservation before storage.
08. An independently retained object is never evicted to satisfy another allocation; process-owned output is reclaimed only with its terminal process record.
09. References are non-authoritative, generation-scoped, and finite-lived; explicit-offset reads are non-draining and create no server cursor state.
10. Every retention gap, dropped byte, expired object, and incomplete producer is reported rather than hidden.
11. Output survives protocol-session reconnects only within the same daemon generation; no lease, state export, or restart recovery exists.
12. Retained bytes, previews, and secret-bearing output never enter normal logs, metrics, receipts, or errors.
