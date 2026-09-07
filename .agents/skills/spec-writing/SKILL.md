---
name: spec-writing
description: Use when authoring, reviewing, or reorganizing accepted technical contracts under spec/.
---

# Specification Authoring

Maintain `spec/` as the current, internally consistent accepted design. Follow `AGENTS.md` and `CONTRIBUTING.md`; [Repository Model](../../../spec/repository-model.md) defines what belongs in specifications.

## Establish Authority

Start with the sections that own the affected concepts. Use `spec/README.md` and subsystem indexes when the owner or cross-component scope is unclear. Search affected terms, states, schemas, and claims across `spec/`, and read dependent contracts when their meaning is affected. Reuse context already read.

Use the accepted outcome supplied by the user or concluded discussion. Do not infer a new product or architecture decision from implementation behavior alone. If material alternatives remain unresolved, identify the decision and continue independent authorized work; keep the unsettled design out of `spec/`. The Issue-to-PR workflow does not itself authorize posting to GitHub.

## Choose the Owner

Each durable fact has one owning contract. Update that owner first, then affected summaries, catalogs, reading paths, and incoming links. Preserve established terms and ownership unless the accepted change replaces them.

Read [structure-and-template.md](references/structure-and-template.md) when adding or splitting documents, changing ownership/navigation, or choosing a contract's structure. Ordinary edits can retain the existing structure.

Keep platform definition in `spec/README.md`, subsystem navigation in its `README.md`, architecture in `00-overview.md`, and detailed contracts in cohesive numbered documents. Overviews link to detailed schemas and state machines rather than becoming competing owners. Do not renumber stable files casually or create empty templates/placeholders in `spec/`.

## Write the Contract

Write the resulting design in present tense with testable statements. Explain purpose, ownership, authority, and observable behavior. Include applicable data types, lifecycle/completion boundaries, failure/cancellation/retry/unknown-outcome semantics, security, compatibility, and verifiable invariants.

Read [domain-modeling-and-naming.md](references/domain-modeling-and-naming.md) when adding or reshaping concepts, schemas, identities, lifecycles, or shared terms. Derive models from representative flows before drafting fields or APIs.

Use upstream public primitives when they already own the semantics. Introduce project abstractions only for a stable cross-host or cross-provider contract. Label conceptual Python-like schemas explicitly; distinguish them from serialized wire formats.

Omit private classes, layouts, hooks, and algorithms that can change without affecting observable behavior, authority, security, or compatibility. Keep accepted trade-offs when they explain the cost of the selected design. Proposals, rejected alternatives, discussion history, progress, tutorials, runbooks, and contributor procedures belong in the surfaces assigned by Repository Model.

Use a diagram or table when it clarifies the contract. Prefer Mermaid for architecture, state, or interaction diagrams, and keep diagrams, schemas, tables, and prose semantically aligned.

## Check Consistency and Validate

- Search changed terminology and update stale summaries, incoming links, and dependent contracts.
- Verify one owner for each fact, consistent identity/version/state terms, and explicit independent completion boundaries.
- Distinguish process-local observations from durable facts and transport delivery from execution authority.
- Make security and compatibility failures explicit. Remove stale text, orphan sections, duplication, and editing residue so the result stands alone without issue history.

Follow [Local Validation](../../../CONTRIBUTING.md#local-validation) for prose-only changes and accompanying implementation or tooling. Report validation and unresolved decisions without claiming that formatting proves semantic correctness.
