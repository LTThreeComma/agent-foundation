# Repository Guide

Agent Foundation is a Python-first open-source foundation for building agent products and operating agents as internal services. The repository is currently in its architecture and specification phase.

## Sources of Truth

- `spec/` contains only the current accepted product and architecture design.
- `docs/` contains Markdown user documentation published with MkDocs Material.
- GitHub Issues are the primary venue for proposals, discussion, open questions, coordination, and progress tracking.
- Pull requests are the reviewed mechanism for changing specifications, documentation, code, tests, and automation.
- `CONTRIBUTING.md` defines local setup and validation.
- `MAINTAINERS.md` defines semantic reviewer routing.

Read [spec/repository-model.md](spec/repository-model.md) before changing repository structure or workflow.

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

```bash
make install
make format
make lint
make typecheck
make test
make build
make check
```

Use narrower commands while iterating, but run `make check` before finalizing a broad change. Add implementation-specific checks behind the existing Make targets as packages are introduced.
