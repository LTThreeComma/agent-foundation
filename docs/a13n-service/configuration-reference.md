# Service configuration reference

This field reference is generated from the same `Settings` definitions used by the Service loader. Run `uv run --locked python scripts/docs/references.py` after changing those definitions. Do not independently edit generated rows.

Use [Configure Service](configuration.md) for precedence, examples, role/storage requirements, and cross-field validation. Types and field constraints below do not replace those combined checks. Secret defaults are masked by the schema; this reference never reads deployment environment values. Defaults apply to the source version, not every historical release.

The complete machine-readable validation schema, including named enum/union definitions, is available as [Service settings JSON](../assets/reference/service-settings.json).

## `server`

| Setting                    | Environment variable             | Type / choices | Constraints and default                         |
| -------------------------- | -------------------------------- | -------------- | ----------------------------------------------- |
| `server.host`              | `A13N_SERVER__HOST`              | string         | default="127.0.0.1"                             |
| `server.port`              | `A13N_SERVER__PORT`              | integer        | minimum=1; maximum=65535; default=8000          |
| `server.request_bytes`     | `A13N_SERVER__REQUEST_BYTES`     | integer        | minimum=1024; maximum=33554432; default=2097152 |
| `server.request_timeout`   | `A13N_SERVER__REQUEST_TIMEOUT`   | number         | maximum=60; exclusiveMinimum=0; default=10      |
| `server.readiness_timeout` | `A13N_SERVER__READINESS_TIMEOUT` | number         | maximum=30; exclusiveMinimum=0; default=2       |
| `server.shutdown_timeout`  | `A13N_SERVER__SHUTDOWN_TIMEOUT`  | integer        | minimum=1; maximum=300; default=15              |
| `server.tls_certificate`   | `A13N_SERVER__TLS_CERTIFICATE`   | string or null | default=null                                    |
| `server.tls_key`           | `A13N_SERVER__TLS_KEY`           | string or null | default=null                                    |

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

## `auth`

| Setting                     | Environment variable              | Type / choices | Constraints and default                   |
| --------------------------- | --------------------------------- | -------------- | ----------------------------------------- |
| `auth.session_seconds`      | `A13N_AUTH__SESSION_SECONDS`      | integer        | minimum=60; maximum=604800; default=43200 |
| `auth.login_limit`          | `A13N_AUTH__LOGIN_LIMIT`          | integer        | minimum=1; maximum=1000; default=10       |
| `auth.login_window_seconds` | `A13N_AUTH__LOGIN_WINDOW_SECONDS` | integer        | minimum=1; maximum=3600; default=60       |

## `oauth`

| Setting                   | Environment variable            | Type / choices  | Constraints and default                   |
| ------------------------- | ------------------------------- | --------------- | ----------------------------------------- |
| `oauth.callback_url`      | `A13N_OAUTH__CALLBACK_URL`      | string or null  | default=null                              |
| `oauth.return_urls`       | `A13N_OAUTH__RETURN_URLS`       | array of string | default=[]                                |
| `oauth.flow_seconds`      | `A13N_OAUTH__FLOW_SECONDS`      | integer         | minimum=30; maximum=1800; default=600     |
| `oauth.operation_seconds` | `A13N_OAUTH__OPERATION_SECONDS` | number          | minimum=2; maximum=30; default=8          |
| `oauth.scan_seconds`      | `A13N_OAUTH__SCAN_SECONDS`      | number          | maximum=30; exclusiveMinimum=0; default=1 |

## `redis`

| Setting         | Environment variable  | Type / choices | Constraints and default                           |
| --------------- | --------------------- | -------------- | ------------------------------------------------- |
| `redis.url`     | `A13N_REDIS__URL`     | string         | format="password"; default="\*\*\*\*\*\*\*\*\*\*" |
| `redis.timeout` | `A13N_REDIS__TIMEOUT` | number         | maximum=30; exclusiveMinimum=0; default=2         |

## `encryption`

| Setting                    | Environment variable             | Type / choices | Constraints and default |
| -------------------------- | -------------------------------- | -------------- | ----------------------- |
| `encryption.active_key_id` | `A13N_ENCRYPTION__ACTIVE_KEY_ID` | string or null | default=null            |
| `encryption.keys`          | `A13N_ENCRYPTION__KEYS`          | object         | —                       |

## `providers`

| Setting                     | Environment variable              | Type / choices  | Constraints and default |
| --------------------------- | --------------------------------- | --------------- | ----------------------- |
| `providers.private_domains` | `A13N_PROVIDERS__PRIVATE_DOMAINS` | array of string | default=[]              |
| `providers.private_cidrs`   | `A13N_PROVIDERS__PRIVATE_CIDRS`   | array of string | default=[]              |
| `providers.http_origins`    | `A13N_PROVIDERS__HTTP_ORIGINS`    | array of string | default=[]              |
| `providers.require_https`   | `A13N_PROVIDERS__REQUIRE_HTTPS`   | boolean         | default=true            |

## `objects`

| Setting             | Environment variable      | Type / choices | Constraints and default                           |
| ------------------- | ------------------------- | -------------- | ------------------------------------------------- |
| `objects.root`      | `A13N_OBJECTS__ROOT`      | string         | format="path"; default="var/service/objects"      |
| `objects.max_bytes` | `A13N_OBJECTS__MAX_BYTES` | integer        | minimum=65536; maximum=67108864; default=16777216 |
| `objects.timeout`   | `A13N_OBJECTS__TIMEOUT`   | number         | maximum=60; exclusiveMinimum=0; default=5         |

## `control`

| Setting                | Environment variable         | Type / choices | Constraints and default                         |
| ---------------------- | ---------------------------- | -------------- | ----------------------------------------------- |
| `control.scan_seconds` | `A13N_CONTROL__SCAN_SECONDS` | number         | maximum=60; exclusiveMinimum=0; default=1       |
| `control.inbox_count`  | `A13N_CONTROL__INBOX_COUNT`  | integer        | minimum=1; maximum=10000; default=128           |
| `control.inbox_bytes`  | `A13N_CONTROL__INBOX_BYTES`  | integer        | minimum=1024; maximum=16777216; default=1048576 |

## `worker`

| Setting                        | Environment variable                 | Type / choices | Constraints and default                         |
| ------------------------------ | ------------------------------------ | -------------- | ----------------------------------------------- |
| `worker.attempt_seconds`       | `A13N_WORKER__ATTEMPT_SECONDS`       | number         | maximum=86400; exclusiveMinimum=0; default=3600 |
| `worker.stream_count`          | `A13N_WORKER__STREAM_COUNT`          | integer        | minimum=1; maximum=4096; default=512            |
| `worker.stream_bytes`          | `A13N_WORKER__STREAM_BYTES`          | integer        | minimum=1024; maximum=16777216; default=1048576 |
| `worker.stream_entry_bytes`    | `A13N_WORKER__STREAM_ENTRY_BYTES`    | integer        | minimum=1024; maximum=1048576; default=65536    |
| `worker.stream_ttl`            | `A13N_WORKER__STREAM_TTL`            | integer        | minimum=1; maximum=86400; default=600           |
| `worker.display_flush_seconds` | `A13N_WORKER__DISPLAY_FLUSH_SECONDS` | number         | maximum=10; exclusiveMinimum=0; default=0.5     |
| `worker.max_events`            | `A13N_WORKER__MAX_EVENTS`            | integer        | minimum=100; maximum=100000; default=10000      |
| `worker.slots`                 | `A13N_WORKER__SLOTS`                 | integer        | minimum=1; maximum=128; default=4               |
| `worker.max_attempts`          | `A13N_WORKER__MAX_ATTEMPTS`          | integer        | minimum=1; maximum=20; default=3                |
| `worker.lease_seconds`         | `A13N_WORKER__LEASE_SECONDS`         | integer        | minimum=3; maximum=300; default=30              |
| `worker.scan_seconds`          | `A13N_WORKER__SCAN_SECONDS`          | number         | maximum=30; exclusiveMinimum=0; default=1       |
| `worker.authority_seconds`     | `A13N_WORKER__AUTHORITY_SECONDS`     | number         | maximum=30; exclusiveMinimum=0; default=1       |
