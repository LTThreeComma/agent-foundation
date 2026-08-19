# Contributing

Contributions to Agent Foundation are welcome. The project uses GitHub Issues for discussion and progress tracking, and pull requests for every reviewed change to specifications, documentation, code, tests, and automation.

## Before You Start

Search the existing [issues](https://github.com/converge-ai-labs/agent-foundation/issues) before opening new work.

Open an issue before implementing a change with unresolved product, architecture, security, compatibility, or scope questions. Use the issue to describe the problem, constraints, alternatives, and progress. Trivial corrections may go directly to a pull request when no material discussion is needed.

Do not add proposals, RFC drafts, discussion logs, or progress tracking to `spec/`. Once an issue reaches an accepted conclusion, update the specification directly in the same pull request as the implementation or as a focused specification pull request.

## Local Setup

Requirements:

- Git
- Python 3.13
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Make

Clone your fork and install the locked development environment and Git hooks:

```bash
git clone git@github.com:YOUR_NAME/agent-foundation.git
cd agent-foundation
make install
```

The repository selects Python 3.13 through `.python-version`. Python packages are uv workspace members under `packages/`; the Rust workspace under `crates/` is currently validated separately from the Python merge gate.

## Local Validation

Use the Makefile as the stable development interface:

| Command           | Purpose                                                                |
| ----------------- | ---------------------------------------------------------------------- |
| `make help`       | List available commands                                                |
| `make install`    | Synchronize the locked Python environment and install pre-commit hooks |
| `make format`     | Apply repository formatting hooks                                      |
| `make lint`       | Run non-mutating repository lint checks                                |
| `make typecheck`  | Type-check Python package sources with Pyright                         |
| `make docs-serve` | Start the local MkDocs development server                              |
| `make docs-build` | Build the documentation site in strict mode                            |
| `make test`       | Run Python workspace tests                                             |
| `make build`      | Build every Python workspace package                                   |
| `make check`      | Run the complete Python and documentation merge gate                   |

Run the full gate before opening or updating a broad pull request:

```bash
make check
```

## Documentation Changes

- Keep user-facing documentation in `docs/`.
- Every source file under `docs/` must be Markdown.
- Update `mkdocs.yml` when adding, removing, or moving a page.
- Run `make docs-build` after documentation or site configuration changes.
- The `Docs` GitHub Actions workflow publishes build artifacts for pull requests and deploys `main` to the `agent-foundation-docs` Cloudflare Pages project.

## Specification Changes

- Keep accepted product and architecture design in `spec/`.
- State the resulting design directly and consistently.
- Keep alternatives under debate, open questions, meeting notes, progress, and issue history in GitHub Issues.
- Link the issue that established the motivation and agreement from the pull request rather than copying its discussion into the specification.

See [spec/repository-model.md](spec/repository-model.md) for the normative repository boundaries.

## Pull Requests

A pull request should:

1. Link the relevant issue when one exists.
2. Explain the motivation and material changes.
3. Update affected specifications, docs, tests, and automation.
4. Report the exact validation commands and outcomes.
5. Request reviewers according to `MAINTAINERS.md`.

Keep commit messages in English. Do not add an agent as a co-author. If assistant attribution is required, use only:

```text
Assisted-by: NAME <email>
```
