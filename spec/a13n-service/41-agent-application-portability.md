# Agent Application Portability

## Design Position

An Agent application is a shareable definition of an entry Agent, its finite subagent graph, prompt content, packaged Skills, and external resource requirements. A developer can keep that definition in Git, review changes, and publish it into another Workspace or compatible Service installation after binding the target's resources.

For example, a code-maintenance application contains a coordinator, a reviewer, and a test writer. Moving it preserves their instructions, delegation policies, shared dependencies, and Skill content while replacing source-specific Model, connection, and Environment references. Copying only the coordinator's `AgentConfig` does not describe that complete operation.

An application bundle is an authoring artifact, not a new Service resource or execution identity. Publishing it creates or revises ordinary Agents and Skills. Invocation still selects an AgentRevision and uses existing Session, Thread, Run, authorization, and Environment semantics. Portability covers application definitions; it does not promise migration of running work or identical model output across installations.

## Ownership and Scope

| Concern                                                                                                 | Owner                                                                                                             |
| ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Bundle composition, dependency traversal, target bindings, change plans, and local publication receipts | Client-side application portability, through the [Service CLI and Rust SDK](37-service-sdks-and-clients.md)       |
| Canonical Agent configuration, exact child Revision binding, validation, and publication                | [Agent Management](28-agent-management.md)                                                                        |
| Skill content, stable keys, immutable Revisions, and content delivery                                   | [Skill Management](31-skill-management.md) and the [Managed Skill Package Contract](../managed-skill-packages.md) |
| Model, connection, Secret, and Environment authority and lifecycle                                      | Their existing Service resource domains                                                                           |
| Executable plugin code and compatible Worker capacity                                                   | [Installed Harness Plugins](36-installed-harness-plugins.md)                                                      |

The feature provides export, local validation, target planning, publication, and interrupted-publication reconciliation as one complete authoring workflow. The CLI performs network operations through the Rust SDK and public management APIs. Neither the bundle nor its receipt grants resource access or bypasses Service validation.

The workflow owns no hosted Application or Deployment lifecycle, continuous reconciliation controller, application marketplace, infrastructure provisioning, or runtime installation of executable plugins. It does not change Harness UI's local resource format or promise that arbitrary embedded Python Agents can be exported as Service configuration.

## Bundle Model

A bundle contains a format version, one entry Agent definition, named Agent definitions, referenced prompt files, packaged Skill content, and typed external resource requirements. Local names identify definitions inside the bundle; they are independent of Service IDs, display names, and authorization identities. Target bindings select resources in one target Service and Workspace.

The following illustrates file responsibilities, not an available CLI command or a serialized schema:

```text
code-maintenance/
  manifest                 # Entry, definitions, and external requirements
  agents/                  # Coordinator, reviewer, and test-writer configuration
  prompts/                 # Referenced instruction text
  skills/                  # Exact packages with their own SKILL.md files
```

Agent definitions reuse the fields and meaning of `AgentConfig`. The authoring format adds local file and dependency references only at defined reference positions; lowering replaces them with instruction text and exact target resource selections. It does not create a second execution configuration, evaluate arbitrary expressions, execute setup scripts, or search opaque strings for IDs to rewrite. Provider-specific settings and tool-selection policies retain their owning schemas.

Shared references to the same exact AgentRevision identify one definition. Different source Revisions remain distinguishable even when they belong to the same stable Agent. Export never replaces an edge's frozen child Revision with that child's current head. Publication preserves each exact selection; an incompatible target mapping blocks rather than collapsing definitions.

A bundle content digest covers its normalized declaration and referenced file content, independently of target resource IDs and local publication progress. Format versions describe bundle compatibility, not Service resource versions. Unsupported versions and unknown semantic fields fail validation; implementations cannot silently discard configuration they do not understand.

## Portable Content and Target Requirements

| Dependency                 | Carried in the bundle                                                                                 | Supplied or checked at the target                                                                           |
| -------------------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Entry and subagents        | Complete authored configuration, exact graph selections, edge context, usage and Environment policies | New Agent identities, or explicitly selected existing Agents for updates                                    |
| Instructions and Skills    | Prompt text and exact normalized Skill package content with digests                                   | Skill identities and local integer versions; published Agent configurations pin the imported content        |
| Models                     | Agent-authored settings, characteristics, and a named Model requirement                               | An authorized Model; Provider, defaults, calling API, credentials, and compatibility remain target-owned    |
| Connector and MCP tools    | Connection requirements, source-native tool selection, and deferred-loading policy                    | Explicit authorized target connections; tool availability and eligibility remain current facts              |
| Environments               | Template requirements and each child's `none`, `shared`, or `dedicated` policy                        | Compatible target templates and exact dedicated-child template Revisions; no running sandbox is transferred |
| Secrets                    | Declared keys, audiences, and requirements                                                            | Authorized credential references through existing invocation contracts; no credential values                |
| Plugins and input adapters | Installed keys and authored configuration                                                             | Compatible trusted target code; import installs no Python packages or Worker images                         |
| External client tools      | Definitions and execution requirements                                                                | The invoking application's existing client-tool executor                                                    |

Root Environment selection remains a Thread/Run concern. Bindings can describe a template to select when invoking the imported entry, but publication does not rewrite an existing Agent's default Environment or create a running Environment implicitly. Environment Provider credentials and connection setup stay outside the bundle.

The workflow carries the declared Agent graph and Skill content. Assets, Run inputs and outputs, Sessions, Threads, checkpoints, histories, live Environment state, role bindings, provider setup, schedules, ingress routes, and delivery subscriptions are not copied. External entry points are enabled separately after publication. References embedded in opaque plugin configuration require an explicit supported adapter or a reported manual requirement; they are never guessed.

Export reads configuration and package content through authorized public APIs. It does not read credential values, internal object keys, execution snapshots, or Worker caches. Prompt and Skill files remain user-authored content; export does not claim to detect or remove secrets their author placed in those files.

## Export and Planning

Export selects one exact entry AgentRevision and walks its retained child Revision graph. Shared definitions and Skill packages reuse exact reads and content transfers without skipping resource authorization. Missing, unreadable, or unretained required content blocks a complete export and identifies the dependency path; a partial directory is not reported as a portable application.

Pinned Skill selections export their exact content. An unpinned selection resolves the current Revision once, records that content, and becomes an explicit pin in the bundle. This captures a definition at export time rather than reproducing what an earlier Run used. Import does not preserve a floating Skill dependency implicitly.

Local validation checks format compatibility, file paths, digests, typed references, complete dependency closure, cycles, and existing graph and package limits. File reads stay inside the bundle root, and Skill packaging follows the shared package rules. Validation performs no Service mutations and executes no bundle-supplied code.

Target planning resolves explicit bindings, inspects readable resources, and lowers the graph into ordinary management requests. It reports `create`, `update`, `unchanged`, and `blocked` entries, exact dependency choices, resource-sharing effects, and update preconditions. A matching display name or Skill key is a conflict to resolve explicitly, not evidence of ownership. Skill keys retain their package-defined meaning; import does not silently rename them to avoid a collision.

A plan records the bundle digest, target identity, binding digest, and observed concurrency evidence. Changed content, bindings, or unexpected target-version changes invalidate the planned mutation. Planning is read-only evidence, not a reservation or authorization promise; Service performs authoritative checks when each request commits.

Plugin and adapter preparation retain their existing execution boundaries. Control does not load Worker plugin factories. A successful plan or Agent publication cannot prove installed-plugin availability or business-configuration validity. Requirements unavailable to public preflight remain visible as execution checks rather than being reported as verified.

## Publication and Recovery

```mermaid
flowchart LR
    Bundle[Bundle and target bindings] --> Plan[Validate and plan]
    Plan --> Skills[Publish exact Skill content]
    Skills --> Children[Publish child Agent Revisions]
    Children --> Entry[Publish entry Agent Revision]
    Entry --> Receipt[Return exact resource references]
    Receipt --> Run[Explicit invocation through existing APIs]
```

Publication prepares Skill content, publishes children in dependency order, and publishes the entry after its exact dependencies are confirmed. A shared definition is prepared once, while edge-specific policies remain distinct. Every published parent explicitly selects confirmed target child versions; it never follows a moving head during publication.

Existing resource updates require explicit target mappings and the owning `expected_version` or `If-Match` precondition. The CLI does not overwrite concurrent edits, adopt same-named resources, change permissions, or delete resources removed from the bundle. Confirmed exact historical dependencies are reused without republishing them or advancing their heads; unchanged head publications retain their owning semantic no-op behavior. Plans expose changes to resources used outside this application, including independently invoked children.

Each request uses the existing operation's idempotency contract. Before dispatch, the client durably records the target, operation identity, canonical request digest, and retained idempotency key in a local receipt. After acknowledgement it records the returned identities and versions. The receipt retains bundle and binding digests, logical-to-target mappings, and confirmed or unresolved progress. It contains no authentication credential or secret value and stays outside the shareable bundle.

After interruption, the client reconciles dispatched operations through retained keys or authoritative resource receipts before continuing. It does not generate a new key after timeout or classify an unknown outcome as failure. Expired idempotency evidence or missing local evidence can require explicit reconciliation; name matching alone never proves a previous create committed. A receipt for another Service, Workspace, bundle, or binding set cannot silently resume publication.

Cancellation stops new dispatch and retains completed and unresolved steps. It does not undo committed changes. Concurrent publishers rely on existing Service preconditions and uniqueness constraints; there is no distributed lock or application-wide transaction.

## Completion and Failure Boundaries

Publication completes when the exact entry Revision and its bundled dependencies are confirmed and their target references are returned. It does not run the Agent or enable external entry points automatically. A separately requested smoke Run verifies real Worker preparation and execution through ordinary Run semantics and can incur normal external effects.

If a dependency fails before the entry request is dispatched, this publication has not changed the entry head. Published Skill and child Revisions can remain. An unknown entry-publication outcome remains unresolved until reconciled; a lost response does not prove the old entry is still current.

Dependency-first publication does not provide an atomic application upgrade. Existing parents retain exact child Revision selections, but independently invoked child heads can change before the entry does. A pre-existing Agent with an unpinned selection can also observe a newly published shared Skill. The plan exposes these effects and requires explicit existing-resource mappings; imported Agent configurations pin their packaged Skills. Resource lifecycle gates, Model configuration, credentials, remote tools, and compatible Worker code remain governed by their owners.

Recovery reconciles or resumes individual operations. It does not automatically roll back other users' changes, delete apparently unused resources, or move Revision heads backward. Restoration uses ordinary Revision publication or Restore after a new plan and current authorization.

## Implementation Completion Criteria

The feature ships only when its versioned format, validation, export, plan, publication, receipt reconciliation, and required Rust SDK/API operations work together. This contract does not imply commands already exist. No alternate HTTP client or internal storage access substitutes for a missing public operation.

Acceptance covers an application with three Agents, a shared child, and a packaged Skill moving between two Workspaces and two compatible Service installations. It verifies exact graph selections, explicit bindings, no-op reapplication, stale-version conflicts, missing dependencies, multiple selected Revisions of one source Agent, interrupted and unknown mutations, entry-publication failure, and the effects of updating a shared unpinned Skill. Credential values and execution state remain absent from exported artifacts.

Graph processing reuses exact dependency reads and performs bounded transfers under existing Service limits. Publication remains a finite client operation, without background reconciliation or new runtime lookups. Performance claims require measurements of graph size, API calls, transferred bytes, and planning/publication time.
