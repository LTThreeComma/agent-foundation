# Resource Transfer

## Design Position

Resource transfer moves supported Service resource definitions and their references between Workspaces or compatible Service installations. Each supported resource type has a transfer representation derived from its existing configuration and version semantics. A bundle composes those representations with typed references and destination requirements.

Selecting an exact AgentRevision provides an export root: its retained references determine which child Revisions and Skill content to collect. For example, transferring a coordinator with a reviewer, a test writer, and a shared Skill preserves their configuration and dependency relationships without requiring the recipient to reconstruct source-specific IDs by hand. A Skill export uses the same Skill representation without requiring an Agent root.

Foundation owns resource transfer, not the definition of an application. Applications own their manifests, product entry points, and composition semantics and can consume these resource exports. A bundle is a transfer artifact; it introduces no Application or Deployment resource, application publication lifecycle, or new execution identity. Imported Agents run through existing invocation APIs.

## Ownership

| Concern                                                                            | Owner                                                                                                             |
| ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Transfer representations, bundle references, export, import planning, and recovery | This document                                                                                                     |
| Agent configuration, immutable Revisions, metadata, and dependency validation      | [Agent Management](28-agent-management.md)                                                                        |
| Skill keys, package content, Revisions, and selection policy                       | [Skill Management](31-skill-management.md) and the [Managed Skill Package Contract](../managed-skill-packages.md) |
| Destination resources, authorization, lifecycle, and mutation preconditions        | Their existing Service domains and [Platform API Conventions](../api-conventions.md)                              |
| Public resource operations and client composition                                  | [Service SDKs and Clients](37-service-sdks-and-clients.md)                                                        |

The CLI composes transfer operations through the Rust SDK and public management APIs. Resource domains retain validation and mutation authority; neither a bundle nor a local import receipt grants access. Transfer owns no infrastructure provisioning, runtime plugin installation, continuous reconciliation controller, or migration of live work. It does not redefine Harness UI's local resource format or export arbitrary embedded Python Agents.

## Resource Representations and Policies

The supported carried resource types are Agents and Skills. Their representations retain the following information; this table defines conceptual content, not a serialized wire schema.

| Resource | Transfer representation                                                                                                                                                                                                  | Destination behavior                                                                                                                                                                                  |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Agent    | Bundle-local identity; name and description as mutable metadata; selected source Revisions containing complete `AgentConfig`, authored instructions, and typed references derived from each Revision's retained bindings | Create a custom Agent, reuse confirmed target Revisions, or explicitly update a mapped custom Agent through ordinary Revision operations. Metadata changes remain separate from Revision publication. |
| Skill    | Bundle-local identity; stable package key and display name; selected source Revisions with normalized package manifests, content digests, and exact file content                                                         | Create or explicitly map one stable Skill and publish or reuse content through existing package operations. The package determines its key; transfer cannot silently rename it.                       |

An Agent export uses `AgentRevision.config` and its retained resource bindings, not a Run's `EffectiveAgentConfig`. It carries installed plugin and input-adapter configuration, output and protocol declarations, client-tool definitions, and edge-specific delegation, usage, and Environment policies under their existing schemas. Instruction text can be stored in bundle files, but file inclusion materializes ordinary configuration; it adds no expression evaluation or setup-script execution.

Names and descriptions are current resource metadata, not reconstructed historical Revision content. Reuse does not change them. Updates include only metadata changes explicitly shown in the plan and use their owning ETag preconditions. Source IDs, actors, timestamps, ownership, permissions, lifecycle state, and Revision history outside the selected definitions are not recreated. The source Agent's mutable default Environment template is not part of an exported AgentRevision; target defaults and Thread/Run Environment selection remain separately managed.

References outside the carried resource types follow explicit binding policies:

| Referenced kind or capability                                                                                                             | Recorded requirement                                                                                           | Destination authority                                                                                                                                                     |
| ----------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Model](30-model-management.md)                                                                                                           | Typed Model requirement; Agent-authored settings and characteristics remain in `AgentConfig`                   | Bind an authorized Model. Its Provider, calling API, defaults, credentials, and current execution configuration remain target-owned.                                      |
| [ConnectorConnection](40-connectivity/03-connectors-and-connections.md) and [MCPConnection](40-connectivity/06-remote-mcp-connections.md) | Distinct connection kinds, source-native tool selections, and deferred-loading policy                          | Bind authorized target connections. Discovery, account authorization, and tool eligibility remain current facts; export carries no credentials or frozen remote catalogs. |
| [EnvironmentTemplate](29-environment-management.md)                                                                                       | Exact template Revision requirement for a dedicated child; retain `none`, `shared`, or `dedicated` edge policy | Bind a compatible authorized target template Revision. Providers, credentials, infrastructure, and actual Environments are not copied.                                    |
| [Secret](27-secret-management.md)                                                                                                         | Declared keys, audiences, and requirements already owned by Agent configuration                                | Supply authorized credential bindings through existing invocation contracts. Export never reads Secret values.                                                            |
| [Installed plugins](36-installed-harness-plugins.md) and input adapters                                                                   | Trusted implementation keys and authored configuration                                                         | Compatible code is installed by the target operator. Import installs no packages or Worker images.                                                                        |
| External client tools                                                                                                                     | Agent-authored definitions and protocol policy                                                                 | The invoking client supplies compatible tool implementations and execution.                                                                                               |

The bundle records every supported managed-resource reference as either a carried dependency or an explicit external requirement. Collecting a dependency closure never means copying every referenced resource indiscriminately. Assets, Run input/output, Sessions, Threads, checkpoints, histories, schedules, ingress routes, and delivery subscriptions are outside this transfer scope. An unsupported required reference blocks a complete export instead of disappearing silently. Opaque plugin or adapter fields are not searched for IDs or credentials; resource references there require an explicit supported field mapping, and unresolved requirements remain visible.

## Bundle Identities and References

A bundle contains a format version, resource representations, referenced content files, typed external requirements, and the exact selections used as export roots. Roots describe the export selection and its target results; they do not define application entry points. The bundle composes the resource representations directly, without a separate application configuration schema.

Each resource has a bundle-local key independent of its source Service ID, display name, and target identity. Within that resource, the source's integer Revision version distinguishes selected immutable content. A carried reference identifies the resource kind, local resource key, and exact selected version when its owning reference is pinned. An external reference identifies a typed requirement to be resolved at the destination. Missing references, kind mismatches, duplicate local identities, and references to uncarried Revisions fail validation.

Repeated references to one exact source Revision share one exported definition. Different selected Revisions of the same source resource remain separate selections under its local key. For example, references to reviewer v3 share `(Agent, reviewer, 3)`, while reviewer v7 has its own selected content. Import records each selection's confirmed target resource ID, Revision ID, and version. Source IDs and version numbers are not assigned to the destination, and a target's current head never substitutes for an exact selection. Owning semantic no-op rules still apply; the mapping records returned Revisions rather than promising to reproduce source version numbers or history length. A mapping that loses distinct selected content or dependency relationships is blocked.

Agent child references come from the exact graph retained in the selected parent Revision, including edges whose authored `version` was omitted. Imported parents explicitly select the mapped target child versions. Edge names, descriptions, delegation context, usage limits, and Environment policies remain per-edge values even when their child content is shared.

Skill references preserve their owning pinned-or-current policy. A pinned binding carries its selected package and maps to an exact target Skill version. For an unpinned binding, export reads the retained stable Skill identity's current Revision once, records which carried Revision was observed as current, and retains the unpinned policy. Import initializes or explicitly updates the mapped Skill with that content but leaves the Agent selection unpinned; later Runs can follow subsequent target Skill changes. It never silently turns a current selection into a pin or retargets a retained binding through a reused source key. The captured package describes the export observation, not the content used by a past Run.

A bundle digest covers its normalized declarations and referenced file content, independently of destination IDs and local import progress. The format version describes artifact compatibility, not resource versions. Unsupported versions and unknown semantic fields fail validation rather than silently dropping configuration. Resource configuration and package compatibility remain governed by their owners.

## Export and Validation

Export resolves its requested roots to exact Revisions and collects their supported dependency closure through authorized public APIs. Shared exact reads and package transfers are reused without skipping authorization. Missing, unreadable, or unretained required content blocks a complete export and identifies the dependency path; an incomplete directory is never reported as a complete bundle. A failed read is not implicitly converted into an external binding.

Export does not read credential values, internal object keys, execution snapshots, or Worker caches. User-authored prompts and package files are carried as content; export does not claim to detect or remove secrets their authors put in them.

Local validation checks format compatibility, content digests, typed references, dependency closure, graph cycles, and existing resource and package limits. Content paths stay inside the bundle root, including after path resolution, and Skill packaging follows the shared package rules. Validation performs no Service mutations and executes no bundle-supplied code.

## Import Preview

An import plan resolves explicit bindings in one target Service and Workspace and translates resource representations into ordinary management requests. It reports each resource's chosen `create`, `reuse`, or `update` action, whether changes are needed, blocked dependencies, exact Revision mappings, and concurrency preconditions. Reuse requires readable, authorized content and compatible bindings, not merely a matching digest from the source's ID namespace.

Create is the default for carried resources. Reuse and update require explicit target mappings. Matching Agent names or Skill keys are conflicts to resolve, not evidence of ownership. A plan shows which existing heads or metadata will change, including effects on consumers outside the bundle. It neither adopts resources by name nor renames Skill keys to evade conflicts.

For a pinned selection, confirmed equivalent historical content can be reused without advancing the target head. For an unpinned Skill selection, matching a historical package alone is insufficient: the plan checks the mapped Skill's current content and reports any required update. When several selected Revisions map to one target resource, the plan shows the ordered operations and resulting head; it never substitutes one head for all selections. If a Skill has an unpinned consumer, the planned final head contains its captured current content before dependent Agents are imported, even when historical packages are also carried.

A plan records the bundle digest, target identity, bindings, and observed concurrency evidence. Changed content, bindings, or unexpected target changes invalidate the affected operations. A prior confirmed step can provide the next expected version within the same import. Planning is read-only evidence, not a reservation: each Service operation rechecks authorization, lifecycle, and its own preconditions when it commits.

Requirements that public APIs cannot verify remain explicitly unverified. In particular, Control does not load Worker plugin factories; a successful plan or Agent Revision publication cannot establish plugin business-configuration validity, installed-code availability, client-tool execution, or remote tool readiness.

## Import and Recovery

```mermaid
flowchart LR
    Bundle[Resource exports and typed references] --> Plan[Validate, bind, and preview]
    Plan --> Dependencies[Import or reuse dependencies]
    Dependencies --> Dependents[Import resources with confirmed references]
    Dependents --> Receipt[Return target mappings and per-operation results]
```

Import orders operations by their exact dependencies. Skill content is confirmed before Agent selections that require it; child Agent Revisions are confirmed before their parents. Shared definitions reuse the confirmed mapping while edge-specific configuration stays distinct. When an Agent is the export root, its Revision is imported after its dependencies as a consequence of this ordering, not as an application activation step.

Updates use the owning `expected_version` or `If-Match` precondition. The client does not overwrite concurrent edits, change permissions, delete resources absent from the bundle, or move Revision heads backward. Reapplying an unchanged bundle with confirmed mappings reuses equivalent Revisions and retains ordinary semantic no-op behavior; the bundle alone does not confer ownership of an earlier import.

Each mutation follows its public operation's idempotency contract. Before dispatch, the client durably records the target and operation, canonical request digest, original idempotency key where supported, and sufficient retained inputs and mappings to reproduce the same request. A local receipt records acknowledgements, target identities and versions, completed steps, and unresolved outcomes. It retains the bundle and binding digests, stays outside the shareable bundle, and contains no authentication credentials or resolved Secret values.

After interruption or a lost acknowledgement, the client reconciles dispatched operations using retained idempotency evidence or authoritative resource results before continuing. It neither generates a new key after timeout nor classifies an unknown outcome as failure. Expired evidence can require explicit reconciliation; matching a name alone never proves an earlier create committed. Where an operation supplies no safe replay evidence, the client reports its unknown outcome rather than blindly redispatching it. A receipt for another Service, Workspace, bundle, or binding set cannot silently resume an import.

Cancellation stops new dispatch and preserves completed and unresolved steps. A failed dependency blocks its dependents while previously committed resources remain. An import receipt reports partial results and supports reconciliation or continuation; it does not roll back other users' changes or delete apparently unused resources. Restoration uses ordinary resource operations after a new plan and current authorization.

## Completion Boundaries

Import completes when every required carried selection and binding is confirmed and the target mappings are returned. A pending, blocked, or unknown operation prevents complete success. Staging a package, sending a request, or writing a local receipt is not authoritative resource completion.

Dependency ordering provides no transaction across resources. An imported child head or shared Skill can affect existing consumers before a later parent operation succeeds. Existing parents retain their exact child Revision selections; unpinned Skill consumers can observe a newly published Skill Revision. The preview exposes these effects, and later target mutations remain governed by the resource owners.

Transfer completion does not imply execution readiness, identical model output, or application deployment. Import starts no Run, allocates no Environment, and enables no external ingress or subscriptions. Applications invoke the returned Agent references through existing APIs and manage their own product configuration.

The following observable invariants apply across Workspaces and compatible Service installations:

1. Agent-rooted export composes the same resource representations used for individual supported resource exports.
2. A shared dependency retains one resource mapping; different selected content and exact child references are preserved without substituting current heads.
3. Skill package content and pinned-or-current selection policy survive transfer independently.
4. Explicit bindings separate carried resources from target-owned infrastructure, credentials, and execution requirements.
5. Preview performs no mutations; import uses ordinary authorization, concurrency, and no-op rules.
6. Partial failure, cancellation, and lost acknowledgement retain per-operation outcomes for reconciliation without assuming rollback or success.

Resource traversal and content transfers are bounded by existing Service and package limits and reuse exact dependency reads. The CLI provides transfer commands only with working format validation and real Rust SDK/API composition; a missing operation is not replaced by direct storage access or a parallel HTTP client.
