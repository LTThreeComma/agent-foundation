# Local Storage and Recovery

## Design Position

Agent UI uses a hybrid local store optimized for durable continuation, indexed product queries, transparent inspection, and bounded write amplification:

1. human- and agent-editable files own desired configuration;
2. one SQLite database owns mutable metadata, control state, references, and query projections;
3. immutable Zstandard-compressed files own resolved snapshots, `HarnessState`, provider resource state, and retained AG-UI event data;
4. OpenTelemetry leaves the store through configured exporters and is never persisted in SQLite or Session object files.

The design does not claim a cross-file transaction between SQLite and the filesystem. It establishes publication ordering: an immutable file is completely written, synchronized under the selected durability profile, verified, and atomically published before a SQLite transaction can reference it. SQLite can therefore lag an already published unreferenced object, but a committed metadata row must not intentionally lead a missing object.

## Boundaries

| Concern                                                                      | Primary authority                                  | Relationship                                                          |
| ---------------------------------------------------------------------------- | -------------------------------------------------- | --------------------------------------------------------------------- |
| Desired process and product configuration                                    | Reloadable configuration files                     | SQLite stores accepted-generation indexes and diagnostics only        |
| Mutable Session, Thread, Turn, Environment, job, queue, and display metadata | SQLite                                             | Authoritative local control state and selected object references      |
| Resolved Agent and Environment snapshots                                     | Compressed immutable object files                  | SQLite and Sessions reference exact content digests                   |
| Complete Harness continuation                                                | Compressed immutable `HarnessState` object files   | SQLite checkpoint row selects one existing verified object            |
| Pending deferred resume authority                                            | Compressed immutable deferred-request object files | SQLite waiting Turn selects one exact unconsumed request object       |
| Provider resource state                                                      | Compressed immutable provider-state object files   | SQLite owns lifecycle/fencing metadata and selected state reference   |
| Retained processed AG-UI sequence                                            | Compressed immutable event segments                | SQLite indexes segment ranges, cursors, Items, and search projections |
| Live execution, tasks, streams, clients, attachments, credentials            | Process memory and owning runtime                  | Never reconstructed by reading local storage alone                    |
| OpenTelemetry                                                                | Configured OTel SDK/exporter                       | Independent diagnostic delivery; no local lifecycle authority         |
| Ordinary application logs                                                    | `converge-logging` process boundary                | Separate from SQLite and Session history                              |

SQLite is not a disposable cache as a whole. Some tables are authoritative control state, while resource and event indexes can be rebuilt from their owning files. Each table documents which category it belongs to; recovery never guesses SQLite-owned facts from display history.

## Storage Topology

The logical data-root layout is stable enough for backup, inspection, and recovery tooling, while exact sharding depth and temporary names remain implementation details:

```text
agent-ui-home/
├── config.toml
├── definitions/
├── metadata.sqlite3
├── objects/
│   ├── agent-snapshots/
│   ├── environment-snapshots/
│   ├── harness-states/
│   ├── deferred-requests/
│   └── provider-states/
├── sessions/
│   └── YYYY/MM/DD/<session-id>/
│       └── events/
├── staging/
└── quarantine/
```

Configured project definition roots can live outside `agent-ui-home`; the configuration loader preserves their authority and source boundary. SQLite `-wal` and `-shm` files are ordinary sidecars, not separate logical stores.

Object paths are derived from validated kind, schema version, digest, and storage-owned sharding. A Session value, model value, API value, event field, or provider payload cannot provide an arbitrary filesystem path. Paths are local locators and grant no authority.

## SQLite Metadata Store

SQLite runs in WAL mode with foreign keys enabled, bounded busy timeout, and short transactions. One transaction never spans model execution, tool execution, provider I/O, filesystem compression, sleeps, background work, or streaming delivery.

Conceptual table groups are:

| Group                             | Representative facts                                                                            | Authority                                                                         |
| --------------------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Configuration index               | accepted generation, resource IDs/digests, source provenance, diagnostics                       | Generation selection and diagnostics; resource content remains file-backed        |
| Sessions and Threads              | identity, title, archive/pin/order, lineage, Agent/Environment snapshot refs, control revisions | SQLite-owned                                                                      |
| Turns and checkpoints             | acceptance, state, base/selected checkpoint refs, Run correlation, terminal outcome             | SQLite-owned lifecycle and selection; state payload file-owned                    |
| Environment resources             | provider spec ref, lifecycle state, operation fence, provider-state ref, cleanup status         | SQLite-owned lifecycle; provider payload file-owned                               |
| Background jobs and pending input | accepted work metadata, process generation, delivery state, queued commands                     | SQLite-owned                                                                      |
| Event segment index               | Session sequence ranges, segment digest/path, previous segment, projection watermark            | Rebuildable from verified event headers except mutable cursor/retention selection |
| Item and search projection        | messages, tools, child observations, previews, bounded searchable text                          | Rebuildable from AG-UI event files                                                |
| Store maintenance                 | schema version, leases, recovery/quarantine records, GC watermarks                              | SQLite-owned control state                                                        |

A single integer does not pretend to serialize every concern. The store uses distinct revisions:

- `configuration_generation` for accepted resource catalogs;
- `session_control_revision` for title, archive, pin, Agent/Environment selection, and lineage operations;
- `thread_commit_revision` for accepted Turn and selected checkpoint advancement;
- `job_revision` for background job outcome/delivery changes;
- `presentation_sequence` and `retention_generation` for AG-UI replay;
- `projection_watermark` for rebuildable indexing.

A command declares the exact expected revision it protects. Renaming a Session does not conflict with an unrelated live event projection, while two stale Turn submissions cannot both advance one Thread checkpoint.

## Immutable Object Contract

Every immutable object is self-describing after decompression and contains:

- object kind and object-schema version;
- logical content digest;
- creation time;
- owning Session/Thread/Turn identities where applicable;
- producer release and payload codec versions needed for compatibility;
- finite typed payload.

The logical digest covers canonical uncompressed content with the envelope's own `logical_digest` field omitted from the digest input. The object is stored as a Zstandard frame with checksum enabled and a bounded decompressed size. Readers verify object kind, schema, digest, size, internal identities, and expected SQLite reference before returning a value.

Publication follows one contract:

1. serialize and validate canonical content;
2. compress into a staging file under the same storage root;
3. flush and synchronize according to the configured durability profile;
4. read and verify the staged object;
5. publish to its digest-derived final path by no-replace atomic publication;
6. synchronize the containing directory when required by the durability profile;
7. only then commit a SQLite reference.

If the final object already exists, the store verifies exact logical identity and reuses it. A collision or incompatible object at the same digest fails closed. A failure before SQLite selection leaves an unreferenced object eligible for later garbage collection; it does not become a selected checkpoint or provider state.

## Harness State Objects

Each complete checkpoint uses one compressed immutable state object. Conceptually, the decompressed envelope is:

```python
class StoredHarnessState(BaseModel):
    object_schema_version: str
    session_id: str
    thread_id: str
    turn_id: str | None
    checkpoint_id: str
    harness_release: str
    harness_state_schema: str
    logical_digest: str
    harness_state: HarnessState
    exported_at: datetime
```

The payload contains the complete exported public `HarnessState`, including its Thread identity and Capability namespaces. It never contains model clients, provider credentials, live Environment bindings, plugin objects, tasks, locks, or presentation cursors.

Writing a state object does not select it. The authoritative selected checkpoint is the SQLite checkpoint/Thread transition that references the verified object. Terminal commit writes the object first, then uses one short SQLite transaction to:

- validate the expected Thread commit revision;
- register the checkpoint reference;
- complete the Turn under the Harness result outcome;
- select the checkpoint when policy permits;
- increment the Thread commit revision.

A database failure after file publication leaves the prior checkpoint selected. External model, tool, and Environment effects remain unknown where applicable; the Host never infers rollback from the unselected object.

## Deferred Request Objects

A suspended Harness result carries complete public `DeferredToolRequests` outside `HarnessState`. Agent UI stores that exact value in a separate compressed immutable object before a Turn can enter `waiting`:

```python
class StoredDeferredRequests(BaseModel):
    object_schema_version: str
    session_id: str
    thread_id: str
    turn_id: str
    source_run_id: str
    agent_snapshot_digest: str
    harness_release: str
    request_codec_version: str
    request_digest: str
    tool_surface_lock: DeferredToolSurfaceLock
    requests: DeferredToolRequests
    exported_at: datetime
```

`request_digest` covers the canonical complete request value, including the exact distinction and identities of deferred calls and approvals. `tool_surface_lock` identifies the resolved Agent/tool/plugin/Capability surface required to interpret those requests; it contains no live tool or authority. The object is continuation input but is not itself `HarnessState`, an AG-UI projection, or proof that a response has been authorized.

The SQLite waiting transition atomically selects the verified checkpoint object and deferred-request object, records their digests, preserves the request as unconsumed, and advances the Thread revision. A response command verifies both objects, exact pending identities, pinned snapshots, codec compatibility, and unconsumed status. Before dispatch it records one consuming `run_id` and transitions the Turn to `running` in SQLite; process loss after possible dispatch becomes interrupted and never reuses the request automatically. A later suspended result publishes and selects a new complete request object.

## Provider State and Resolved Snapshots

Resolved Agent and Environment snapshots use the same compressed immutable object contract. Their content is authority-neutral and content-addressed. Removing or changing current source configuration does not remove an object referenced by a retained Session.

Provider resource state is stored separately from Environment snapshots and `HarnessState`. Its envelope records provider key, provider state version, resource identity correlation, operation fence, and opaque provider-owned payload. SQLite owns which provider-state object is selected for one Session Environment resource and the surrounding lifecycle state. A fresh credential and provider runtime are still required to resume, pause, inspect, or destroy it.

Provider-state objects receive owner-only local file permissions and are excluded from ordinary Session export, model-visible tools, AG-UI events, and telemetry. They contain no credential according to the Provider contract, but remain sensitive operational state.

## AG-UI Event Segments

Agent UI retains the complete post-processor AG-UI event sequence selected by its Host persistence policy as immutable `.jsonl.zst` segments. It does not write AG-UI events into SQLite payload columns.

The first decompressed JSON line is a segment header:

```python
class AguiSegmentHeader(BaseModel):
    record_type: Literal["segment_header"]
    schema_version: str
    session_id: str
    first_sequence: int
    last_sequence: int
    event_count: int
    previous_segment_digest: str | None
    logical_digest: str
    created_at: datetime
```

Subsequent lines are Host-owned records:

```python
class StoredAguiEvent(BaseModel):
    record_type: Literal["agui_event"]
    event_id: str
    presentation_sequence: int
    session_id: str
    thread_id: str
    turn_id: str | None
    run_id: str | None
    observed_at: datetime
    stream: PresentationStreamRef
    event: AguiEvent
```

`presentation_sequence` is monotonic within one Session across root and exposed child streams. `stream` preserves root/child correlation without deriving authority from presentation identity. Segments cover contiguous non-overlapping ranges and link to the prior retained segment digest. The chain detects gaps and wrong ordering; it is an integrity structure, not a tamper-proof audit log.

The application service accumulates bounded event batches, serializes them into an immutable compressed segment, publishes the file, and then registers its range in a short SQLite transaction. Live subscribers can observe an event before that event is in a durable segment. The delivery record identifies live versus durable replay origin, and a live observation never strengthens Turn or checkpoint completion.

At a terminal Harness result, the coordinator flushes the terminal AG-UI batch to a published segment before publishing the durable terminal Session projection. Event-segment registration and checkpoint selection use independent short SQLite transactions; neither waits inside the other, and no cross-store transaction is claimed. If event registration fails while checkpoint selection succeeds, execution continuation remains valid and presentation recovery indexes the verified segment or reports an explicit gap.

AG-UI files are presentation history, not `HarnessState`, a provider operation journal, OpenTelemetry, or an authorization log. They can reconstruct WebUI/TUI Items and protocol inspection, but cannot resume a pending tool call, recreate a background task, restore Environment authority, or prove absence of an external side effect.

## Projection and Query

SQLite projects compressed AG-UI segments into bounded query tables for:

- Session previews and last activity;
- paginated Turns and Items;
- message/tool/child summaries;
- current Item values;
- text search;
- replay cursor resolution.

Projection reads only verified complete segments. It advances segment digest, last sequence, and projection watermark in one SQLite transaction with the derived rows. Projection failure leaves the watermark behind durable event files. Startup or on-demand read repair resumes from the last verified segment; rebuild can discard only projection-owned tables and recreate them from retained segments.

The projector never writes a new checkpoint, changes a Turn outcome, delivers a background result, or repairs SQLite-owned Session control state. Search ranking and Item summaries are observations derived from presentation history.

## Configuration and Event File Visibility

Configuration files are readable desired state. Immutable object files are machine-oriented but intentionally use canonical JSON or JSON Lines before standard Zstandard compression, stable envelopes, compact identifiers, and documented codecs. Local tools and Agents with separately authorized filesystem access can inspect them using ordinary decompression and JSON tooling; no SQLite-internal binary encoding is required for large state or event payloads.

This inspectability grants no application command authority. Directly editing an immutable object or SQLite file is corruption, not a supported mutation API. Product mutations flow through configuration reload or the application service.

## Recovery

Startup recovery proceeds before command acceptance:

1. acquire the configured application/store ownership lease;
2. open SQLite, validate schema, apply owned migrations, and verify integrity needed for authoritative tables;
3. validate the latest accepted configuration generation or accept a newer complete file generation;
4. reconcile staging files and quarantine malformed objects;
5. verify every selected Agent snapshot, Environment snapshot, provider-state reference needed for lifecycle, selected checkpoint, and pending deferred-request object;
6. scan registered AG-UI segment chains and repair rebuildable projection lag;
7. mark prior-process `accepted` or `running` Turns and live jobs interrupted, while preserving a `waiting` Turn whose selected complete checkpoint and exact deferred correlations validate;
8. publish the recovered application view.

Unreferenced valid immutable objects survive a grace period before garbage collection so a failed SQLite commit or interrupted publication cannot race immediate deletion. Referenced missing or corrupt objects have type-specific outcomes:

| Missing or corrupt value                     | Recovery outcome                                                                                                                   |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Current source configuration                 | Candidate reload fails; last retained accepted generation can remain queryable, but new composition requiring the source is denied |
| Pinned Agent or Environment snapshot         | Affected Session fails closed for execution                                                                                        |
| Selected `HarnessState`                      | Affected Thread fails closed for continuation; AG-UI history is not promoted                                                       |
| Selected deferred-request object             | Waiting Turn fails closed; identifiers or AG-UI cannot reconstruct the request                                                     |
| Unselected checkpoint/deferred object        | Quarantine or remove after reference analysis; selected state is unchanged                                                         |
| Selected provider resource state             | Provider lifecycle operation fails closed; no replacement resource is silently created                                             |
| AG-UI segment with valid selected checkpoint | Continuation can remain available; replay exposes an explicit sequence gap                                                         |
| Rebuildable projection rows/database pages   | Rebuild from verified event files                                                                                                  |
| SQLite-owned control state                   | Fail closed or restore from a verified SQLite backup; event/state files do not invent titles, pins, lifecycle, or selections       |

The database and its WAL/SHM sidecars are quarantined together when corruption requires replacement. A recovery tool can scan self-describing files and produce an importable evidence report, but partial reconstruction is never silently installed as authoritative control state.

## Concurrency and Leases

One application-service process owns write coordination for a selected data root. A store lease records an unguessable process generation and heartbeat in SQLite, with platform process-liveness evidence where available. A second WebUI or TUI process either attaches through an explicitly supported local client path or reports the active owner; it does not start another writer silently.

Within the application process, one Thread has at most one advancing foreground Turn. Expected Thread revisions are verified in SQLite before dispatch and at terminal selection. Independent Sessions can run concurrently subject to Host limits. Filesystem publication can occur concurrently for distinct digests, while SQLite transactions remain short and retry bounded busy conflicts.

Reclaiming an abandoned lease authorizes recovery inspection. It does not prove prior model, tool, Environment, or provider work stopped without side effects.

## Archive, Retention, Export, and Delete

Archive is SQLite-owned display/control metadata and does not weaken state or event references. Implementations can move compressed event trees for storage management only through a staged move plus SQLite path update and compensation/reconciliation on failure; logical identity uses digest rather than location.

Retention can delete only objects and event segments with no retained Session, fork, selected checkpoint, provider lifecycle, background result, or export reference. Because AG-UI segments are compressed and supply complete presentation replay, lossy compaction is explicit: it creates a new retention generation and a complete semantic snapshot plus gap metadata before removing detailed prior segments. It never changes `HarnessState` or claims byte-for-byte replay after compaction.

Export copies a consistent SQLite projection plus referenced safe Agent/Environment snapshots, selected `HarnessState` objects, and AG-UI segments according to export policy. Provider resource state, credentials, browser capabilities, and live authority are excluded by default. Import validates all envelopes and creates new local control records rather than trusting source paths.

Hard delete conflicts with active work, marks deletion intent, performs provider-resource cleanup according to [Session Environment ownership](04-sessions-environments-and-state.md), removes SQLite references, and later garbage-collects unreferenced files. Local deletion does not roll back external effects.

## OpenTelemetry Separation

Agent UI configures repository-standard OpenTelemetry at the process boundary. Pydantic AI and the Harness retain ownership of their model, tool, run, and Environment spans. Agent UI adds Host spans and metrics for application commands, configuration reload, Session locking, Environment management, immutable-object publication, SQLite transactions, projection lag, replay gaps, background routing, and surface transport.

OTel records are sent to configured exporters through bounded asynchronous buffering. They are never inserted into `metadata.sqlite3`, AG-UI event segments, state objects, provider-state files, or configuration snapshots. Exporter delay, rejection, or outage cannot change command acceptance, Harness outcome, checkpoint selection, Environment lifecycle, job delivery, or shutdown correctness.

Session, Thread, Turn, Run, and safe provider correlation can appear as bounded attributes. Prompt content, model/tool payloads, AG-UI values, credentials, provider-state payloads, filesystem content, browser capabilities, and private exception text are absent by default. A required durable product or audit fact belongs in the owning metadata or event store; OTel is not an audit authority or recovery input.

## Durability Profiles

The Host declares one local durability profile governing file flush, file synchronization, directory synchronization, and SQLite synchronous behavior. All profiles preserve logical ordering and integrity; weaker profiles can lose recently acknowledged local data after device or OS failure and must expose that trade-off explicitly. Atomic rename alone never claims device-level durability.

Application acknowledgement distinguishes:

- command accepted in SQLite;
- event observed live;
- event segment durably registered;
- Harness result observed;
- checkpoint selected;
- Environment state selected;
- OTel export attempted or completed.

None substitutes for another.

## Failure Semantics

| Failure                                                    | Outcome                                                                                            |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Compression or object validation fails                     | No SQLite reference is committed                                                                   |
| Object publishes but SQLite commit fails                   | Object is orphaned and cleanup-safe; prior selected metadata remains                               |
| SQLite selects a reference but later file loss is detected | Type-specific corruption outcome; no fallback fabrication                                          |
| AG-UI projection update fails                              | Event file remains durable; projection catches up later                                            |
| AG-UI event file fails after live delivery                 | Replay contains an explicit gap; live delivery does not become durable history                     |
| Checkpoint commit succeeds but AG-UI registration lags     | Thread can continue; presentation repair indexes verified files or reports a gap                   |
| SQLite busy timeout expires before dispatch                | Command conflicts/fails before Harness or provider side effects                                    |
| SQLite commit fails after external work                    | Prior selected state remains; effect outcome is unknown and requires reconciliation                |
| Store lease owner disappears                               | Recovery interrupts prior active execution, preserves validated waiting Turns, and never auto-runs |
| OTel exporter or ordinary logging fails                    | Diagnostic loss only; product lifecycle facts are unchanged                                        |

## Compatibility

SQLite schema, each immutable object schema, Harness state schema, Environment provider-state version, Agent/Environment snapshot schema, AG-UI event version, segment envelope, compression codec, projection schema, and OTel semantic conventions evolve independently.

A migration never rewrites content under an existing logical digest. It writes and verifies new immutable objects before switching SQLite references. Projection schema can rebuild from retained event files. Authoritative SQLite migrations preserve control semantics or fail before command acceptance. Readers reject unknown codecs and excessive decompressed sizes before allocation.

## Trade-offs

### SQLite metadata and compressed payload files

SQLite provides efficient mutation, conflict detection, pagination, and search without storing large evolving state or event payloads in database pages. File publication introduces ordered multi-store commits and orphan cleanup, but state and history remain inspectable with standard Zstandard and JSON tooling.

### Immutable segments instead of one append file

Immutable compressed segments avoid in-place compressed-tail corruption and support atomic publication, digest verification, concurrent readers, and retention generations. They create more filesystem objects and require a SQLite range index.

### OTel outside local persistence

Separating telemetry prevents diagnostic volume or exporter failure from corrupting Session storage and avoids building another tracing database. Local offline telemetry search depends on the configured collector or log destination rather than the Agent UI metadata store.

## Invariants

01. SQLite stores metadata, control state, object references, and projections; it never stores complete `HarnessState`, `DeferredToolRequests`, provider resource-state payloads, or AG-UI event payloads.
02. Every selected state, deferred request, snapshot, provider-state payload, and retained AG-UI segment is an immutable verified Zstandard-compressed file.
03. A file is completely published before any SQLite transaction can select or index it.
04. There is no claimed cross-file ACID transaction; orphan files are safe and SQLite references fail closed when payloads are missing.
05. `HarnessState` remains the only Agent state authority; a waiting Turn additionally requires its exact unconsumed `DeferredToolRequests`, and event/projection data can replace neither.
06. AG-UI segments own presentation replay; SQLite Item/search tables are rebuildable projections and cannot strengthen execution facts.
07. SQLite-owned mutable control facts are never guessed from AG-UI or state files after corruption.
08. OpenTelemetry and ordinary logs remain outside SQLite, state objects, and AG-UI files and never determine product completion.
09. No transaction or database session remains open across Harness execution, provider I/O, background waits, or a streaming response.
10. Retention never deletes an object referenced by a retained Session, selected checkpoint, provider lifecycle, fork, or background outcome.
