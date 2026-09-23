# a13n Service

The hosted a13n Service: a managed-agent runtime with tenancy, configured resources, durable run execution on the Harness, and the HTTP API the Console and SDKs use. It is a workspace package with one executable, `a13n-service`.

```sh
a13n-service --config service.toml migrate
a13n-service --config service.toml bootstrap --email admin@example.com
a13n-service --config service.toml run --role all   # or: control, worker
a13n-service --config service.toml user disable --email someone@example.com
```

Configuration is a TOML file (`--config` or `A13N_SETTINGS_FILE`) with `A13N_<SECTION>__<FIELD>` environment overrides; see the [configuration guide](../../docs/a13n-service/configuration.md). The package layout, import rules and behavior are specified in the [Service contract](../../spec/a13n-service/README.md); local development uses [`make dev`](../../dev/service/README.md).
