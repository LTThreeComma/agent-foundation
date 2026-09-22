# Goals, constraints and scope

## Why rewrite

The rewrite addresses repeated authorization, locking, lifecycle, error and configuration mechanisms, tangled dependencies and feature-specific exceptions in the execution path. The result must make common behavior easy to find, understand and change while preserving the guarantees required below.

The rewrite is not a feature project. Most product capabilities survive. What changes is where they live and how many mechanisms carry them.

## What "good" means

Read two or three files of the core and be able to guess how every other resource and every run operation works. Concretely:

1. **Compressible.** Few rules, orthogonal, one way to do each kind of thing. Illegal states are prevented by database/type constraints where expressible; remaining transition rules have a single owner and adversarial validation.
2. **Stable.** Retained capabilities preserve the durability and concurrency guarantees specified in this design, each carried by structure (a constraint, a trigger, a type, a fence), not by code paths that happen to be correct. Removed capabilities and changed behavior do not remain requirements merely because legacy implements or tests them.
3. **Bounded.** Dependencies point one way and a tool checks it. Removing a provider or a distribution add-on never leaves the core pretending it can do what it cannot.
4. **Legible.** One word has one meaning in the whole tree. Directory and file names say what is inside, not which phase of a pipeline produced it.
5. **Extensible at real boundaries.** A provider/distribution uses typed registration and policy interfaces without copying the engine. A resource follows common conventions; new domain semantics may require explicit core changes, not a speculative framework.

And one rule to keep the other five honest: **no elegance for its own sake.** Two things that look alike are merged only when their rules and consequences are the same.

## Readers

Three audiences, and each shapes the code:

- **Internal engineers** must find, next to the code that carries a guarantee, why the guarantee exists, so the next refactor does not delete it as redundancy.
- **External contributors** must be able to read the core in one sitting and add a resource by copying one existing one.
- **Coding agents** copy the nearest example and do not hold the whole tree in context. Conventions therefore have to be mechanical: clear package ownership, consistent names and checked dependency rules. Internal file splitting follows [02](02-layout.md#package-tree), not a fixed file count.

## Hard constraints

The rewrite must preserve these constraints:

1. **Durability and concurrency guarantees.** Writer fences on attempts, lease and heartbeat, idempotent submission, immutable sealed runs and facts, at-most-once consumption of inbox entries, checkpoint recovery. Each is named in the document that owns it with the structure that carries it.
2. **Two open issues need a place to plug in**, not an implementation: [#411](https://github.com/converge-ai-labs/agent-foundation/issues/411) (shared foundations for EE and Cloud distributions) and [#410](https://github.com/converge-ai-labs/agent-foundation/issues/410) (spend budgets). The places are listed in [09-runtime.md](09-runtime.md#extension-points).

Tenant isolation, bounded credential authority and operationally recoverable state are part of those guarantees. Internal engineers, outside contributors and coding agents must be able to identify one owner for each rule. Reducing table/line/file counts cannot justify weakening these contracts.

The public API, Console, SDKs, schema, names and algorithms may change. Existing product choices below are the baseline; changes to the agreed behavior must be explicit and reflected in the owning design section. Neither 35 tables nor four files per resource is an acceptance criterion.

## What leaves the service

The following capabilities leave Service and are not implemented elsewhere as part of this rewrite. There is no `apps/bot` deliverable, Bot design task, platform integration or Bot-specific acceptance gate. Service retains its ordinary submission, observation, feedback and MCP interfaces for external callers.

| Excluded capability                             | Scope                                                                                |
| ----------------------------------------------- | ------------------------------------------------------------------------------------ |
| Bot application                                 | Progress cards, interactive cards, installation diagnostics, Bot memory and settings |
| Scheduled and event-triggered tasks (routines)  | Bot scheduling and platform event workflows                                          |
| Application accounts, targets and event ingress | Platform credentials, webhook reception, batching, throttling and deduplication      |
| Slack, Lark and GitHub native tools             | Platform-specific implementations, including a Bot-owned MCP server                  |

## What is dropped

| Dropped                                                                                                                                               | Replacement                                                                                                                                                                                     |
| ----------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Configuration assistant drafts, configuration sessions, candidate execution, verification, application history (25 modules, 4.2k lines, 14 endpoints) | The assistant is an ordinary builtin agent with read tools and a write tool that creates a revision through the normal API. See [04-resources.md](04-resources.md#the-configuration-assistant). |
| A2A: 4 tables, 13 routes, push delivery, its own error envelope and media type                                                                        | None. Spec 23 is withdrawn.                                                                                                                                                                     |
| Hosted AG-UI endpoint and its two binding tables                                                                                                      | None. AG-UI stays as the event vocabulary of the run stream; input goes through the native API. A translation layer for AG-UI clients is outside this rewrite.                                  |
| Inline hook subscriptions, subscription revisions, successor inheritance, the 33-name hook catalogue (21 unsubscribable)                              | Workspace-level webhook subscriptions on run lifecycle events.                                                                                                                                  |
| Agent-scoped role bindings, the second role lattice, the 84-member action enum                                                                        | One role set, two scopes (organization, workspace), four verbs.                                                                                                                                 |
| Per-run environment ownership and live mounts                                                                                                         | Thread desired mounts; the run freezes its mount set at acceptance for recovery and active-use arbitration.                                                                                     |
| Separate child execution engine and successor routing                                                                                                 | Ordinary child threads, one acceptance function and durable result delivery; origin eligibility remains explicit.                                                                               |
| Memory: providers, agent memory entries, subjects, the organization job and the three run-memory binding tables                                       | Nothing in this design. Memory is not designed until its scope is decided.                                                                                                                      |
| Feature-specific OAuth frameworks and background token ownership                                                                                      | One connection-service operation protocol; single refresh owner, generation checks and explicit unknown outcomes.                                                                               |
| Universal command-evidence framework                                                                                                                  | Execution command keys live on their result entity; ordinary CRUD does not claim unsupported generic replay.                                                                                    |
| Display projector process and event projection bookkeeping                                                                                            | Worker-owned display folding/publication, independent of execution checkpoints, with attempt segments.                                                                                          |
| Multiple queue-specific counters/protocols                                                                                                            | One inbox with bounded outstanding count/bytes and thread-version edits; capacity is defined in [05](05-runs.md#inbox-capacity).                                                                |
| Run `priority`, `queue_name`, `execution_deadline_at`, `execution_policy_version`, `max_handoffs`                                                     | `max_handoffs` becomes a deployment setting. The others had no callers.                                                                                                                         |
| `/labels` sub-resources (14 operations on 7 resources)                                                                                                | `labels` is a column edited through the resource's own `PATCH`.                                                                                                                                 |
| Organization/workspace mirrored route pairs (22)                                                                                                      | Each resource lives at exactly one scope.                                                                                                                                                       |

## What is ported by reading, not by copying

envd support in this iteration is HTTP(S) only, using the existing `http_envd` provider. Reverse WebSocket ingress, device pairing, connection tickets, presence, reconnect takeover and the cross-process relay are deferred. The scope and connection rules are owned by [06: envd over HTTP](06-environments.md#envd-over-http).

The following old mechanisms are useful implementation references for guarantees retained by this design. Read them where relevant; do not depend on legacy at runtime or treat its structure and tests as a migration checklist. New test ownership and acceptance are defined in [12](12-validation.md#new-tests-and-live-journeys).

- the claim transaction (`interactions/scheduling.py:152-286`): pure SQL, one statement claims and fences;
- the writer fence on checkpoints (`interactions/objects.py`, `state_admission.py`);
- usage ingestion (`interactions/attempts.py:357-470`): idempotent by record id, digest guard on conflicting content, late records retained;
- the outbox (`durable_operations/outbox.py`) and `EntityRequestKey`;
- `ids.py`, `collection_cursors.py`, `background.py` (`Sweep`), `database/migration.py`;
- the object store (`storage/object_store/*`, local and S3);
- `ResourceCredential` and `SecretProtector` (AES-GCM with authenticated data);
- the provider catalogue bridge (`provider_plugins/catalog.py`) and the two trace query backends.

The contents of `dev/live_tests` are replaced with journeys for the new design. Old Service tests need not be ported, maintained or made to pass; retained guarantees must be demonstrated by the new implementation's tests.

## Design acceptance standard

Review complete paths, not isolated signatures. For submit, continue, feedback, recovery, fork, child completion and retirement, identify the authority, immutable input, transaction boundary, external uncertainty, recovery owner and user-visible result. At-most-once applies to a defined durable input/state boundary; external effects cannot be called exactly-once without provider evidence.

A design is acceptable when its common path is simple, exceptions follow the same model, all intermediate states have a recovery or an explicit unresolved outcome with a responsible owner, and schema/API/protocol agree. This does not require a generic blocked state; each domain owns its states and failure evidence. Include scheduling, memory/storage bounds, backpressure and cleanup cost when comparing simplicity. Moving complexity into an adapter or the Console does not remove it. #410/#411 seams must be exercised by a small concrete extension fixture.

The evidence ladder is explicit: reasoning and counterexample; isolated protocol probe; real Harness/database/provider integration; fault injection and measured concurrency. Passing one level is not permission to claim the next. [12-validation.md](12-validation.md) records the scenarios and implementation gates. The first vertical slice must prove the execution spine before parallel feature expansion or estimates by resource count.
