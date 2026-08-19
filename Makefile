.DEFAULT_GOAL := help

.PHONY: install
install: ## Install locked dependencies and Git hooks
	@command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
	@echo "Synchronizing the Python workspace"
	@uv sync --locked --all-packages
	@echo "Installing pre-commit hooks"
	@uv run --locked pre-commit install --install-hooks

.PHONY: sync
sync: ## Synchronize the locked Python workspace
	@uv sync --locked --all-packages

.PHONY: format
format: sync ## Format repository files
	@git ls-files --cached --others --exclude-standard -z | xargs -0 uv run --locked pre-commit run --files || true
	@git ls-files --cached --others --exclude-standard -z | xargs -0 uv run --locked pre-commit run --files

.PHONY: lint
lint: sync ## Run non-mutating repository lint checks
	@uv lock --check
	@for hook in check-added-large-files check-case-conflict check-merge-conflict check-json check-toml check-yaml; do \
		git ls-files --cached --others --exclude-standard -z | \
			xargs -0 uv run --locked pre-commit run "$$hook" --files || exit $$?; \
	done
	@git ls-files --cached --others --exclude-standard -z -- '*.md' | xargs -0 uv run --locked mdformat --check --number
	@uv run --locked ruff check --no-fix packages
	@uv run --locked ruff format --check packages

.PHONY: typecheck
typecheck: sync ## Type-check Python package sources
	@uv run --locked pyright

.PHONY: docs-check
docs-check: ## Verify that docs contains only Markdown source files
	@files="$$(find docs -type f ! -name '*.md' -print)"; \
		test -z "$$files" || { echo "Only Markdown files are allowed under docs/:"; echo "$$files"; exit 1; }

.PHONY: docs-serve
docs-serve: sync ## Serve the documentation site locally
	@uv run --locked mkdocs serve

.PHONY: docs-build
docs-build: sync docs-check ## Build the documentation site in strict mode
	@uv run --locked mkdocs build --strict

.PHONY: test
test: sync ## Run Python workspace tests
	@uv run --locked python -m pytest

.PHONY: build
build: sync ## Build all Python workspace packages
	@uv build --all-packages

.PHONY: check
check: lint typecheck test build docs-build ## Run the complete Python and documentation merge gate

.PHONY: clean
clean: ## Remove generated local artifacts
	@rm -rf .pytest_cache .ruff_cache dist site target

.PHONY: help
help: ## Show available commands
	@echo "Usage: make [target]"
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
