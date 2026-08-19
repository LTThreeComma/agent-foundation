# Repository Guide

Agent Foundation is a Python-first open-source foundation for building agent products and operating agents as internal services. The repository is currently in its architecture and specification phase.

## Sources of Truth

- `spec/` contains only the current accepted product and architecture design.
- `docs/` contains Markdown user documentation published with MkDocs Material.
- GitHub Issues are the primary venue for proposals, discussion, open questions, coordination, and progress tracking.
- Pull requests are the reviewed mechanism for changing specifications, documentation, code, tests, and automation.
- `CONTRIBUTING.md` defines the contribution workflow, local setup, and validation.
- `DEVELOPMENT.md` defines repository-wide engineering standards for deployable services.
- `MAINTAINERS.md` defines semantic reviewer routing.

Read [spec/repository-model.md](spec/repository-model.md) before changing repository structure or workflow. Read [DEVELOPMENT.md](DEVELOPMENT.md) before changing service code, persistence, migrations, streaming endpoints, workers, logging, or container behavior.

## Workflow

- Start material product, architecture, security, compatibility, or scope discussions in a GitHub Issue.
- Do not add RFCs, discussion logs, issue summaries, roadmaps, or progress tracking to `spec/`.
- When an issue reaches a conclusion, update the accepted design directly through a pull request.
- Keep changes focused and update affected specs, implementation, tests, docs, and automation together.
- Use the semantic areas in `MAINTAINERS.md` when requesting review.

## Documentation

- Keep documentation source files under `docs/` as Markdown.
- Configure site behavior and navigation in the root `mkdocs.yml`.
- Run `make docs-build` after changing docs content, navigation, or site configuration.
- Use `make docs-serve` for local preview.

## Development

The Python 3.13 environment and `packages/*` workspace are managed with `uv`. All project package names use the `converge-` prefix. Rust crates live under `crates/`; Rust checks are not yet part of the repository merge gate.

Follow these service invariants; the complete contract and rationale live in [DEVELOPMENT.md](DEVELOPMENT.md):

- Use async I/O on service paths and keep blocking work off the event loop.
- Use the canonical engine, session, and short-transaction helpers rather than constructing local variants. Use `converge-logging`; libraries obtain namespaced loggers, while executables configure output once at the process boundary.
- Never hold a database session or transaction across agent execution, external I/O, sleeps, background work, or a streaming response.
- SSE, WebSocket, and other streaming routes must not receive a yielded database session through their FastAPI dependency graph. Finish authorization and initial reads in a closed short session; open fresh short sessions inside the stream only when needed.
- Generate migration revisions through the repository Make target against disposable PostgreSQL, then review the generated operations and rollout safety. Never create a revision file from scratch.
- Execution-only processes never migrate. The shared image lets compatible control or all-in-one replicas auto-migrate under bounded PostgreSQL advisory locking; deployments with a dedicated migration job disable replica auto migration.
- Build one non-root service image for all-in-one, control-plane, and execution-plane roles; select the role at runtime.

```bash
make install
make setup
make dev
make db-migrate msg="description"
make format
make lint
make deps-check
make typecheck
make test
make build
make image-foundation-service
make check
```

Use narrower commands while iterating, but run `make check` before finalizing a broad change. Add implementation-specific checks behind the existing Make targets as packages are introduced.
