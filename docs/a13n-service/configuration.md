# Configure Service

Configuration is loaded once from optional TOML selected by `--config` or `A13N_SETTINGS_FILE`. Explicit `A13N_SECTION__FIELD` environment variables override file values. Unknown sections and fields fail startup; no `.env` is loaded automatically.

```toml
[server]
host = "127.0.0.1"
port = 8000
readiness_timeout = 2
shutdown_timeout = 15

[database]
url = "postgresql+psycopg://service:password@localhost/service_rewrite"
auto_migrate = true
pool_size = 5
```

Use `A13N_DATABASE__URL` for deployment credentials. Never point this schema at an old Service store. With a dedicated migration job set `A13N_DATABASE__AUTO_MIGRATE=false` on replicas. Worker always checks schema without migrating.

Only server/database settings are implemented. Redis, objects, providers and execution settings will be added with their capabilities; unsupported settings are rejected. See the [generated reference](configuration-reference.md) for validated bounds.
