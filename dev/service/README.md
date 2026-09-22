# Local Service foundation

The new Service uses fresh checkout-owned PostgreSQL storage. `make setup` creates and migrates it; `make service-dev` runs the control foundation in the foreground. `make dev-status` reports the checkout identity, Service listener, database host/port/name and generated config path without credentials. `make dev-down` stops this checkout's containers without deleting volumes.

`make db-migrate msg="description"` uses a separate disposable Compose project/database and removes only that project's resources after generation. It never uses a deployment URL. Package integration tests own their own containers too.

Use `make live-test` for the disposable public execution journey and `make live-test-console CONSOLE_LIVE_DIR=/tmp/a13n-console-review` for a real browser stack. These fixture-owned commands use independent stores and ports; they do not seed or reset this checkout's ordinary database. Ordinary combined Console orchestration, resets and Service Langfuse integration remain unavailable. Do not use the former development database with the new schema. Bootstrap with `a13n-service --config <printed-config-path> bootstrap --email admin@example.com` after setup.

`make db-upgrade` and `make db-check` use `var/service-rewrite/local.toml`, the same configuration created by setup. They fail before setup. Set `SERVICE_CONFIG=/path/to/config.toml` only to intentionally select a different database; setup and status always describe this checkout's owned instance.
