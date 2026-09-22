# Service configuration reference

This field reference is generated from the same `Settings` definitions used by the Service loader. Run `uv run --locked python scripts/docs/references.py` after changing those definitions. Do not independently edit generated rows.

Use [Configure Service](configuration.md) for precedence, examples, role/storage requirements, and cross-field validation. Types and field constraints below do not replace those combined checks. Secret defaults are masked by the schema; this reference never reads deployment environment values. Defaults apply to the source version, not every historical release.

The complete machine-readable validation schema, including named enum/union definitions, is available as [Service settings JSON](../assets/reference/service-settings.json).

## `server`

| Setting                    | Environment variable             | Type / choices | Constraints and default                   |
| -------------------------- | -------------------------------- | -------------- | ----------------------------------------- |
| `server.host`              | `A13N_SERVER__HOST`              | string         | default="127.0.0.1"                       |
| `server.port`              | `A13N_SERVER__PORT`              | integer        | minimum=1; maximum=65535; default=8000    |
| `server.readiness_timeout` | `A13N_SERVER__READINESS_TIMEOUT` | number         | maximum=30; exclusiveMinimum=0; default=2 |
| `server.shutdown_timeout`  | `A13N_SERVER__SHUTDOWN_TIMEOUT`  | integer        | minimum=1; maximum=300; default=15        |

## `database`

| Setting                                       | Environment variable                                | Type / choices | Constraints and default                           |
| --------------------------------------------- | --------------------------------------------------- | -------------- | ------------------------------------------------- |
| `database.url`                                | `A13N_DATABASE__URL`                                | string         | format="password"; default="\*\*\*\*\*\*\*\*\*\*" |
| `database.auto_migrate`                       | `A13N_DATABASE__AUTO_MIGRATE`                       | boolean        | default=true                                      |
| `database.pool_size`                          | `A13N_DATABASE__POOL_SIZE`                          | integer        | minimum=1; maximum=100; default=5                 |
| `database.connect_timeout`                    | `A13N_DATABASE__CONNECT_TIMEOUT`                    | integer        | minimum=1; maximum=60; default=5                  |
| `database.statement_timeout`                  | `A13N_DATABASE__STATEMENT_TIMEOUT`                  | integer        | minimum=1; maximum=300; default=10                |
| `database.migration_advisory_lock_timeout`    | `A13N_DATABASE__MIGRATION_ADVISORY_LOCK_TIMEOUT`    | integer        | minimum=1; maximum=3600; default=900              |
| `database.migration_lock_timeout`             | `A13N_DATABASE__MIGRATION_LOCK_TIMEOUT`             | integer        | minimum=1; maximum=60; default=3                  |
| `database.migration_statement_timeout`        | `A13N_DATABASE__MIGRATION_STATEMENT_TIMEOUT`        | integer        | minimum=1; maximum=3600; default=900              |
| `database.migration_idle_transaction_timeout` | `A13N_DATABASE__MIGRATION_IDLE_TRANSACTION_TIMEOUT` | integer        | minimum=1; maximum=300; default=30                |
