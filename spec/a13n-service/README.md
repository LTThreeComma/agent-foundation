# a13n-service rewrite

This directory specifies the new `packages/a13n-service`. First archive the old Service code, specifications, user documentation and exported contracts in their corresponding `a13n-service-legacy` directories. The new system uses the canonical paths, distribution, import and command names from the start. [11: transition](11-transition.md#package-placement-and-build-boundary) owns this arrangement. There is no separate next package.

This directory is the current product and architecture contract of the Service. Historical decisions, reviews and research reports are intentionally omitted; no requirement depends on reading them. [acceptance.md](acceptance.md) records implementation evidence against [12](12-validation.md). If a contract conflict is found, identify the exact conflict and resolve it before implementing the affected behavior; continue independent work.

## The shape in one paragraph

The service is a managed-agent runtime. A tenant configures resources (agents and their revisions, skills, models, connections, environment templates, secrets), submits input to a thread, and the service turns that input into runs: it accepts the input, a worker claims the run, executes it against the Harness using the run's accepted thread mounts, and seals the outcome as immutable facts. The main integration path for a bot or chat frontend is submit input and observe runs; configuration, credentials, feedback and control use the ordinary resource/runtime API. Everything the service calls while a run executes, a model, a sandbox, a tool server, is a provider selected by type. The code is four business packages, `tenancy`, `resources`, `runs` and `providers`, on top of shared mechanisms under `infra/`, assembled by one `build_app(distribution)`.

## How to read this

| If you want to know                                            | Read                                                 |
| -------------------------------------------------------------- | ---------------------------------------------------- |
| why we are rewriting, what is out of scope, what cannot change | [01-goals.md](01-goals.md)                           |
| where code goes and how things are named                       | [02-layout.md](02-layout.md)                         |
| who can do what                                                | [03-tenancy.md](03-tenancy.md)                       |
| what a tenant configures                                       | [04-resources.md](04-resources.md)                   |
| how input becomes a sealed run                                 | [05-runs.md](05-runs.md)                             |
| where a run executes                                           | [06-environments.md](06-environments.md)             |
| what the service remembers and tells others                    | [07-facts-and-delivery.md](07-facts-and-delivery.md) |
| what a run calls                                               | [08-providers.md](08-providers.md)                   |
| processes, background work, assembly, extension points         | [09-runtime.md](09-runtime.md)                       |
| the HTTP surface                                               | [10-api.md](10-api.md)                               |
| how the old package maps onto this one                         | [11-transition.md](11-transition.md)                 |
| how protocols are challenged and implementation is accepted    | [12-validation.md](12-validation.md)                 |
| what a word means                                              | [glossary.md](glossary.md)                           |

Start with [01-goals.md](01-goals.md), [11-transition.md](11-transition.md) and [12-validation.md](12-validation.md) to establish scope, delivery and acceptance. Read [02-layout.md](02-layout.md), [glossary.md](glossary.md) and the remaining design before implementing the corresponding paths; cross-cutting authorization, persistence and runtime rules apply to every feature. Legacy tests do not define the new behavior or block implementation.

## Rule ownership

Define a complete rule once, in its owning section. Other chapters explain their own interface or storage contribution and link to that rule; they do not copy its full algorithm. Validation lists scenarios and expected observations, not a competing protocol. Update the owner, affected interfaces and validation requirements together when behavior changes.

| Rule                                                                                                   | Owning section                                                                                    |
| ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| Package responsibilities, import direction and internal splitting                                      | [02: layout](02-layout.md#package-tree)                                                           |
| Provider resources, definitions, handles and capability boundaries                                     | [08: providers](08-providers.md)                                                                  |
| Authorization and credential scope, including key issuance                                             | [03: tenancy](03-tenancy.md#authorization), [issuance](03-tenancy.md#flows)                       |
| Resource lifecycles, what a run freezes, MCP header validation and inheritance                         | [04: resources](04-resources.md#two-lifecycles), [caller headers](04-resources.md#caller-headers) |
| Input capacity, source selection, resume, assignment, the checkpoint commit and terminal disposition   | [05: runs](05-runs.md#submit-and-accept)                                                          |
| Environment phases, operation identity and maintenance cadence                                         | [06: external lifecycle](06-environments.md#one-outstanding-external-operation)                   |
| HTTP envd scope, direct connection and connect-only ownership                                          | [06: envd over HTTP](06-environments.md#envd-over-http)                                           |
| Immutability, run objects and their cleanup, display, the thread stream, outbox and lifecycle webhooks | [07: facts and recovery](07-facts-and-delivery.md)                                                |
| Worker wakeups and admission/budget semantics                                                          | [09: runtime](09-runtime.md)                                                                      |
| Routes, request/response shapes, preconditions and request-key replay                                  | [10: API](10-api.md)                                                                              |
| Canonical identities, the four legacy directories, consumer/tooling updates and switch sequence        | [11: transition](11-transition.md)                                                                |
| New test ownership, live journeys and implementation completion evidence                               | [12: validation](12-validation.md)                                                                |

The glossary gives short definitions and links; it does not repeat recovery or transition rules.

## Conventions in these documents

- Tables are shown as column lists. `NULL` marks a nullable column; every other column is NOT NULL. Types are omitted where obvious: `*_id` columns hold ids, `*_at` columns hold UTC timestamps, and `labels`, `config`, `payload`, `settings`, `failure` and the like are typed JSONB. `*_ref` holds an immutable payload key, expanded to ObjectRef at the API. `runs.checkpoint` and `runs.display` are typed pointers to the run's committed immutable state and display objects.
- Code is Python 3.13, SQLAlchemy 2 (async), FastAPI, Pydantic 2. Snippets are illustrative signatures, not final code.
- Historical old-code references use the original `packages/a13n-service` path at `e35a9637` on 2026-09-21 unless another revision is named. After the move, those modules belong to legacy, not the new canonical package; [11](11-transition.md) explains baseline tracking.
- Guarantees the old package already provides and this design must keep are marked **Keeps:** in the section that owns them, together with the structure that carries them.
