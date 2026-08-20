# Specification Structure and Contract Template

Use this reference selectively. A specification is not required to contain every section; include only the sections needed to make its contract complete.

## Information Hierarchy

| Layer                        | Owns                                                                                                               | Avoids                                                     |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------- |
| `spec/README.md`             | Platform definition, component boundaries, dependency direction, global authority, top-level navigation            | Subsystem field tables and detailed lifecycle rules        |
| `spec/<subsystem>/README.md` | Subsystem scope, document catalog, reading paths, authority rules, terminology conventions                         | Detailed contracts already owned by numbered documents     |
| `00-overview.md`             | Subsystem design position, boundaries, major components, end-to-end flow, completion boundaries, stable principles | Repeating every detailed schema or failure table           |
| Numbered detail document     | One cohesive domain, API, lifecycle, protocol, security, compatibility, or packaging contract                      | Unrelated concerns and copied contracts from another owner |

The hierarchy is broad to deep. A reader starts with the platform, enters one subsystem, understands its architecture, and follows links to the contract being changed.

## Decide Whether to Add a Document

Update an existing document when:

- the concept already has an owner;
- the change refines that owner's schema, behavior, or boundary;
- a new section remains cohesive with the existing contract.

Add a detail document when:

- the contract has a distinct authority or lifecycle;
- several existing documents need to reference it;
- keeping it in the current owner would mix unrelated responsibilities;
- the design is accepted and substantive, not a placeholder.

Split a document when independent facts have different owners or reading paths. Do not split merely because a file is long.

## Adaptive Detail-Document Shape

```markdown
# <Contract Name>

## Design Position

<One to three decisive paragraphs: what the contract is, why this boundary exists,
and which upstream or host layer remains authoritative.>

## Boundaries

<Ownership table, explicit in-scope/out-of-scope statements, and links to adjacent owning contracts.>

## <Core Model or Contract>

<Typed conceptual schema, field meanings, relationships, and authority.>

## <Flow or Lifecycle>

<Mermaid flow, sequence, or state diagram plus semantics not visible in the diagram.>

## Failure Semantics

<Failure classes, observable outcomes, retry or unknown-effect behavior.>

## Compatibility

<Version axes, additive/breaking behavior, migration owner, upstream compatibility.>

## Trade-offs

<Costs of the selected design, without preserving unresolved or rejected alternatives.>

## Invariants

<Short numbered statements that implementations and reviews can verify.>
```

`Design Position` and one clear `Boundaries` section are normally required for a detailed contract. Do not add both `Boundary` and `Boundaries` sections with overlapping content. Flow, failure, compatibility, security, trade-offs, and invariants are conditional on the subject.

## Useful Content Patterns

### Ownership table

Use when several layers participate in one flow:

```markdown
| Concern | Owner | Relationship |
| --- | --- | --- |
| Process-local result | Harness | Observation supplied to host |
| Durable completion | Host | Commits its own lifecycle transition |
```

### Conceptual typed schema

Use Python-like Pydantic or dataclass syntax when type relationships are clearer than prose. State whether it is conceptual or a wire format. Explain authority and field semantics after the schema; do not use code only as decoration.

### State diagram

Use for public or durable states and legal transitions. Do not turn internal implementation steps into public states. Pair the diagram with ownership, transition preconditions, terminal meaning, and retry semantics.

### Sequence diagram

Use for cross-boundary interactions. Name the authority that accepts or commits each fact. Keep external delivery, telemetry, usage settlement, and execution completion separate when they are independent.

### Failure table

Use columns such as `Failure`, `Observable outcome`, `Retry or reconciliation`, and `Authority`. Distinguish failure before dispatch from unknown outcome after possible side effects.

### Trade-off section

Describe what the accepted design gains, what cost it accepts, and which owner absorbs that cost. Do not narrate the meeting or enumerate still-open choices.

## Cross-Reference Rules

- Link to the owner on the first material use of a foreign concept.
- Use relative Markdown links and meaningful link text.
- Prefer a section anchor when only one part of a large owner is relevant.
- Update catalogs and reading paths when navigation changes. A complete catalog lists every owned document; a curated entry list says explicitly that it is selective.
- Summaries may repeat a one-sentence conclusion, but not a schema, state machine, field table, or complete invariant list.
- Preserve exact capitalization for named contracts such as `AgentContext`, `HarnessState`, and `HarnessRunStream`.
- A `Ref` names another entity; a `Receipt` records an observed outcome; neither implies authority unless the owning contract says so.

## Language Rules

- Use present tense: “The host owns durable completion,” not “The host will own durable completion.”
- Prefer explicit verbs: owns, selects, validates, commits, emits, rejects, preserves.
- Use `must` for a genuine invariant or compatibility requirement, not for project management urgency.
- Separate identity from authority, observation from commitment, routing from authorization, and acceptance from delivery.
- Define terms before using abbreviations or overloaded words such as run, session, state, checkpoint, and completion.
- Avoid `currently`, `eventually`, `planned`, `phase`, and delivery-status qualifiers.
- Avoid implementation-specific filenames and private APIs unless stability of that surface is itself the accepted contract.

## Final Contract Review

Confirm that:

- a new reader can navigate from the root index to this contract;
- every durable fact and state transition has one owner;
- every referenced concept either has a local definition or a link to its owner;
- diagrams, schemas, tables, and prose use the same terms and states;
- failure, cancellation, retry, side-effect uncertainty, and cleanup are explicit where material;
- security and compatibility behavior are enforceable rather than aspirational;
- broad documents remain readable after the change;
- the final text contains no discussion history, unresolved choice, implementation status, roadmap, adjacent duplicate, or truncated editing residue.
