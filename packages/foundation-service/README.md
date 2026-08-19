# foundation-service

`foundation-service` is the hosted control and execution service for Agent Foundation. The initial service skeleton provides FastAPI process lifecycle, async SQLAlchemy infrastructure, Alembic migrations, shared pretty/JSON logging through `converge-logging`, local PostgreSQL and Redis, and one container image for all deployment roles.

## What Is Ready

- `foundation-service serve` starts FastAPI with `all`, `control`, or `execution` role selection.
- `/healthz` is process liveness; `/readyz` verifies database access.
- One lifespan-owned async engine and session factory are shared by the process.
- Service HTTP integrations and ASGI tests use `httpx2`.
- `short_session()` and `transaction()` provide short database scopes with bounded cancellation cleanup.
- Alembic uses service metadata, a dedicated unpooled sync connection, bounded PostgreSQL timeouts, and a session advisory lock.
- Local PostgreSQL 17 and Redis 7 are defined in `dev/compose.yaml`.
- `make db-migrate` generates revisions from a disposable database inside the local PostgreSQL service.
- The production image runs as non-root and selects `all`, `control`, or `execution` at runtime.

Business APIs, durable models, schedulers, queues, and execution workers are intentionally not stubbed. Add them as end-to-end capabilities rather than placeholder abstractions.

## Local Development

Start PostgreSQL and Redis, apply migrations, and run the service:

```bash
make dev
```

`make dev` runs the setup and upgrade steps. Use `make setup` or `make db-upgrade` separately when only that operation is needed.

The default endpoints are:

```text
http://127.0.0.1:8000/healthz
http://127.0.0.1:8000/readyz
```

Stop local infrastructure and remove its volumes when a clean database is needed:

```bash
make dev-down
```

## Add an ORM Model

1. Add a model under `converge_foundation_service/db/models/` using the shared `Base`.

2. Import the model from `db/models/__init__.py` so Alembic metadata includes it.

3. Generate a revision from a disposable database:

   ```bash
   make db-migrate msg="add conversation table"
   ```

4. Review `upgrade()` and `downgrade()` for names, constraints, indexes, locks, rolling compatibility, and rerun behavior.

5. Apply and verify it locally:

   ```bash
   make db-upgrade
   make db-check
   make test
   ```

The generator starts local PostgreSQL if necessary, creates an isolated temporary database, replays all existing revisions, autogenerates the model diff, formats the new file, and drops the temporary database in `finally`. It never compares models with the normal development database.

Do not create revision files manually. The empty `versions/.gitkeep` is intentional until the first durable model is accepted.

## Database Usage

Import shared infrastructure from `converge_foundation_service.db`:

```python
async with transaction(session_factory) as session:
    session.add(record)
```

A transaction covers one database unit of work. Do not hold it across HTTP calls, model or tool execution, queue waits, sleeps, background tasks, or streaming responses. Repositories may flush; the application service owns the transaction boundary.

Streaming routes must finish database-backed authentication and initial reads before constructing the response. Pass immutable values to the generator and open a fresh short session only for a bounded read or write.

## Commands

```bash
make setup
make dev
make dev-down
make db-migrate msg="description"
make db-upgrade
make db-downgrade
make db-current
make db-check
make db-history
make image-foundation-service
```

The underlying CLI is also available through `uv run foundation-service --help`.

## Configuration

All settings use the `FOUNDATION_` prefix. See the root `.env.example` for a local template.

| Setting                                                 | Default  | Purpose                                                    |
| ------------------------------------------------------- | -------- | ---------------------------------------------------------- |
| `FOUNDATION_ROLE`                                       | `all`    | `all`, `control`, or `execution` process role              |
| `FOUNDATION_AUTO_MIGRATE`                               | `false`  | Allow a migration-owning container to upgrade before serve |
| `FOUNDATION_DATABASE_CONNECT_TIMEOUT_SECONDS`           | `10`     | Maximum PostgreSQL connection establishment time           |
| `FOUNDATION_DATABASE_STATEMENT_TIMEOUT_SECONDS`         | `30`     | Maximum normal application statement duration              |
| `FOUNDATION_DATABASE_READINESS_TIMEOUT_SECONDS`         | `3`      | Maximum readiness database check duration                  |
| `FOUNDATION_MIGRATION_ADVISORY_LOCK_TIMEOUT_SECONDS`    | `900`    | Maximum wait to serialize migration runners                |
| `FOUNDATION_MIGRATION_LOCK_TIMEOUT_SECONDS`             | `3`      | Maximum DDL lock wait                                      |
| `FOUNDATION_MIGRATION_STATEMENT_TIMEOUT_SECONDS`        | `900`    | Maximum duration of a migration statement                  |
| `FOUNDATION_MIGRATION_IDLE_TRANSACTION_TIMEOUT_SECONDS` | `30`     | Maximum idle time in a migration transaction               |
| `FOUNDATION_LOG_FORMAT`                                 | `pretty` | Rich-backed `pretty` locally or `json` when deployed       |

Database and Redis URLs are intentionally omitted from the table because they may contain credentials; use `.env.example` for their local forms and secret-backed deployment configuration for real environments.

## Container Roles and Migrations

Build the shared image from the root `Dockerfile`:

```bash
make image-foundation-service
```

The image installs Debian's CA bundle, verifies it during build, and sets `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt` for `httpx2`. Keep that setting unless a replacement path provides the complete deployment trust set.

The image defaults to `FOUNDATION_AUTO_MIGRATE=true` for `all` and `control`; PostgreSQL advisory locking serializes concurrent rollout replicas. Set it to `false` when a dedicated migration job owns schema changes. Configure the container port through `FOUNDATION_PORT` so the service and healthcheck use the same value. Startup behavior is fail-closed:

- `execution` never changes schema and only checks that all Alembic heads are applied;
- `all` and `control` run `upgrade head` when auto migration is enabled;
- otherwise the process performs the same non-mutating head check;
- a failed migration or compatibility check prevents the service from starting.

A dedicated singleton migration job with auto migration disabled on normal replicas is preferred for distributed production deployments.
