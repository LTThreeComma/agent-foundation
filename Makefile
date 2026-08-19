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

.PHONY: setup
setup: sync ## Start local PostgreSQL and Redis
	@docker compose -f dev/compose.yaml up -d --wait

.PHONY: dev
dev: setup ## Upgrade the local schema and run foundation-service
	@uv run --locked foundation-service db upgrade
	@uv run --locked foundation-service serve

.PHONY: dev-down
dev-down: ## Stop local infrastructure and remove its data volumes
	@docker compose -f dev/compose.yaml down --volumes --remove-orphans

.PHONY: format
format: sync ## Format repository files
	@git ls-files --cached --others --exclude-standard -z | xargs -0 uv run --locked pre-commit run --files || true
	@git ls-files --cached --others --exclude-standard -z | xargs -0 uv run --locked pre-commit run --files

.PHONY: deps-check
deps-check: sync ## Check Python package dependency declarations
	@(cd packages/agent-harness && uv run --locked deptry converge_agent_harness)
	@(cd packages/logging && uv run --locked deptry converge_logging)
	@(cd packages/foundation-service && uv run --locked deptry converge_foundation_service)

.PHONY: lint
lint: sync deps-check ## Run non-mutating repository lint checks
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

.PHONY: db-migrate
db-migrate: sync ## Generate a migration (usage: make db-migrate msg="description")
	@bash dev/db-migrate.sh "$(msg)"

.PHONY: db-upgrade
db-upgrade: sync ## Upgrade the local foundation-service database to all heads
	@uv run --locked foundation-service db upgrade

.PHONY: db-downgrade
db-downgrade: sync ## Downgrade the local database by one reviewed revision
	@uv run --locked foundation-service db downgrade

.PHONY: db-current
db-current: sync ## Show the current foundation-service database revision
	@uv run --locked foundation-service db current

.PHONY: db-check
db-check: sync ## Fail unless the foundation-service database is at all heads
	@uv run --locked foundation-service db current --check-heads

.PHONY: db-history
db-history: sync ## Show foundation-service migration history
	@uv run --locked foundation-service db history

.PHONY: image-foundation-service
image-foundation-service: ## Build the local foundation-service container image
	@docker build -f Dockerfile \
		-t "$${FOUNDATION_SERVICE_IMAGE:-converge-foundation-service:local}" .

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
