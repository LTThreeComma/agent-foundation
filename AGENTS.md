# Repository Guide

Agent Foundation is a Python-first open-source cloud foundation for building agents and multi-agent systems, with an embeddable Agent Harness, hosted agent services, and built-in observability.

## Sources of Truth

- [CONTRIBUTING.md](CONTRIBUTING.md) owns contribution workflow, setup, and validation; consult the sections relevant to the requested work.
- [DEVELOPMENT.md](DEVELOPMENT.md) owns service engineering standards; read the relevant sections for service implementation changes.
- [spec/repository-model.md](spec/repository-model.md) owns repository structure and workflow boundaries; consult it when changing either.
- [spec/README.md](spec/README.md) locates accepted product and architecture contracts when the owning specification is not already known.
- `docs/` contains Markdown user documentation published with MkDocs Material; `mkdocs.yml` owns site configuration and navigation.
- [MAINTAINERS.md](MAINTAINERS.md) owns semantic reviewer routing.

Read the directly affected context, consulting owning contract sections when changing behavior or boundaries. Reuse context already read and expand to other owners and consumers when the change affects them. This guide and skills summarize operational rules; they do not replace owning contracts or turn tool-specific defaults into repository requirements.

Write repository content in English, as required by [CONTRIBUTING.md](CONTRIBUTING.md#repository-language).

## Scope and Authorization

- Carry requested changes through implementation, relevant validation, and inspection of the result; fix problems introduced by the change. Resolve routine choices from repository evidence and existing authorization, including follow-ups. Ask only when missing information materially affects correctness, scope, or authorization.
- An audit or review is read-only unless fixes are requested. Local editing does not itself authorize committing, pushing, GitHub writes, merging, deploying, or releasing. Each action must be covered by the request or established authorization; loading a skill grants none of these permissions.
- Preserve unrelated work and secrets. History rewrites, destructive cleanup, and changes to shared or deployed state require authorization covering the concrete operation and target.
- Unresolved product, architecture, security, compatibility, or scope decisions follow the Issue-to-PR workflow. Complete independent, authorized work while those decisions remain open. Routine corrections do not require a new Issue, and the workflow does not authorize posting one on the user's behalf.
- Explicit user instructions take precedence over skill guidance within the execution environment's rules. Preserve existing authorization; when blocked, identify the concrete missing decision or authority and continue independent authorized work.

Keep diffs focused and update affected contracts, implementation, tests, docs, and automation together. Report the outcome, changed files, validation, and material limitations concisely.

## Package and Release Boundaries

Python 3.13 packages under `packages/` use `uv`; Rust crates live under `crates/`. Workspace directories omit the project prefix, distributions use `a13n-`, and Python imports use `a13n_`. SDKs under `sdk/` and the companion Foundation CLI stay outside the root workspaces.

For package layout, dependency, build, or release changes, read the relevant [repository boundaries](spec/repository-model.md#repository-surfaces), [release rules](CONTRIBUTING.md#releases), and owning workflow. They define release groups, published version pins, private build inputs, and artifact requirements.

## High-Risk Engineering Rules

Retain these constraints for the affected surfaces and consult the relevant [engineering standards](DEVELOPMENT.md):

- Keep service I/O async and use canonical storage helpers. Never hold a database session or transaction across agent execution, external I/O, waits, background work, or streams. Streaming routes must not receive yielded database sessions, including through authentication dependencies.
- Generate migrations with the owning Make target against a disposable database, then review rollout safety; never write revisions from scratch. Worker and connectivity roles never migrate. The `all` and `control` roles auto-migrate under bounded PostgreSQL advisory locking; a dedicated migration job disables replica auto migration.
- Build one non-root service image with runtime role selection. Libraries use namespaced `a13n-logging` loggers; executables configure logging once.
- Keep model-visible and user-trace identifiers concise and kind-prefixed. Preserve entropy where unpredictability is part of a security or protocol contract.

## Validation

Follow [Local Validation](CONTRIBUTING.md#local-validation) for scoped file checks, component Make targets, and full-gate requirements. Add meaningful tests for behavior changes and reuse successful checks while their relevant inputs remain unchanged.

- `make check` applies formatting before running fast checks; review any resulting edits.
- Run `make docs-build` for changes to `docs/`, navigation, or site configuration.
- Complete applicable component, image, and migration checks. Report unavailable checks and failures accurately.
