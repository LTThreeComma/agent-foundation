# Service

The Service rewrite supports administrator bootstrap, secure login, workspace API keys, model-provider and model configuration, immutable agent revisions, and durable text-agent execution through the Harness. Public reads combine canonical input content with durable execution observations. Console and additional resource capabilities are still under implementation.

Use a new PostgreSQL database, Redis and a shared local object directory accessible to the control and worker processes. Keep these separate from old Service deployments. With an explicit configuration file, run:

```sh
a13n-service --config service.toml migrate
a13n-service --config service.toml bootstrap --email admin@example.com
a13n-service --config service.toml run --role control
a13n-service --config service.toml run --role worker
```

Bootstrap prompts for a password of at least 12 characters. It creates one organization, default workspace and administrator; a second invocation refuses without changing data. The password is stored as Argon2id. Configure HTTPS directly through the server TLS settings or terminate HTTPS at trusted ingress: browser login uses secure cookies and CSRF protection.

Control serves the API and scans for expired attempts and queued successors. Workers claim accepted runs within local capacity, renew their leases independently of model execution, persist checkpoints and display, record correlated usage, and seal outcomes. `run --role all` combines these roles. All/control optionally migrate on startup under a bounded PostgreSQL advisory lock; workers never migrate and refuse an incompatible schema.

`/healthz` reports liveness. `/readyz` checks runtime tasks, database/schema and Redis within a deadline. `/api/v1/openapi.json` describes the implemented API. Configure a provider, model and agent, then submit a message to the workspace's thread collection; use the returned run ID to read its status and items. Repeating the same submission with its `Idempotency-Key` returns the original accepted input.

Run `make live-test` for a disposable CLI/bootstrap/HTTPS/control/two-worker journey with a deterministic HTTP model. This proves the implemented path, not the entire planned feature and recovery matrix. Redis observation streams, Console, environments, additional resources and remaining lifecycle operations are under development.

See [configuration](configuration.md), [generated settings](configuration-reference.md) and [HTTP reference](api-reference.md). Independent SDKs and the remote CLI need new-contract updates in their owning repositories.
