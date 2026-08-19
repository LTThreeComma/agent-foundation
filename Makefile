.DEFAULT_GOAL := help

FOUNDATION_SERVICE_IMAGE ?= agent-foundation-service:local
SANDBOX_IMAGE ?= agent-foundation-sandbox:local

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
format: sync ## Format Python, Markdown, configuration, and Rust files
	@git ls-files --cached --others --exclude-standard -z | xargs -0 uv run --locked pre-commit run --files || true
	@git ls-files --cached --others --exclude-standard -z | xargs -0 uv run --locked pre-commit run --files
	@cargo fmt --all

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
	@uv run --locked ruff check --no-fix packages scripts
	@uv run --locked ruff format --check packages scripts

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

.PHONY: python-build
python-build: sync ## Build all Python workspace distributions
	@rm -rf dist
	@uv build --all-packages

.PHONY: rust-format-check
rust-format-check: ## Check Rust formatting
	@cargo fmt --all -- --check

.PHONY: rust-lint
rust-lint: ## Run Clippy with warnings denied
	@cargo clippy --workspace --all-targets --all-features --locked -- -D warnings

.PHONY: rust-test
rust-test: ## Run Rust workspace tests
	@cargo test --workspace --all-features --locked

.PHONY: rust-build
rust-build: ## Build the Rust workspace
	@cargo build --workspace --all-features --locked

.PHONY: rust-package
rust-package: ## Verify the agent-envd crates.io package
	@cargo package --locked --allow-dirty --package converge-agent-envd

.PHONY: rust-check
rust-check: rust-format-check rust-lint rust-test rust-build rust-package ## Run the complete Rust merge gate

.PHONY: build
build: python-build rust-build ## Build all Python distributions and Rust binaries

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

.PHONY: release-check
release-check: ## Validate release versions (component=foundation|agent-envd version=X.Y.Z)
	@test -n "$(component)" || { echo "component is required"; exit 2; }
	@test -n "$(version)" || { echo "version is required"; exit 2; }
	@uv run --locked python scripts/check-release-version.py "$(component)" "$(version)"

.PHONY: image-foundation-service
image-foundation-service: ## Build the local foundation-service container image
	@docker build -f Dockerfile -t "$(FOUNDATION_SERVICE_IMAGE)" .

.PHONY: image-sandbox
image-sandbox: ## Build the local sandbox image with agent-envd
	@docker build -f Dockerfile.sandbox -t "$(SANDBOX_IMAGE)" .

.PHONY: images
images: image-foundation-service image-sandbox ## Build all local container images

.PHONY: image-check
image-check: images ## Build and smoke-check all container images
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(FOUNDATION_SERVICE_IMAGE)")" = "app"
	@test "$$(docker image inspect --format '{{.Config.User}}' "$(SANDBOX_IMAGE)")" = "sandbox"
	@docker run --rm --entrypoint agent-envd "$(SANDBOX_IMAGE)"

.PHONY: python-check
python-check: lint typecheck test python-build docs-build ## Run the Python and documentation merge gate

.PHONY: check
check: python-check rust-check ## Run the complete repository merge gate

.PHONY: clean
clean: ## Remove generated local artifacts
	@rm -rf .pytest_cache .ruff_cache dist site target

.PHONY: help
help: ## Show available commands
	@echo "Usage: make [target]"
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-26s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
