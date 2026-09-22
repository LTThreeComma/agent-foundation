# a13n Service

The new Service currently implements strict TOML/environment configuration, composed migrations, initial administrator bootstrap, and role startup with liveness/readiness probes. Run execution, resource APIs, authentication endpoints and Console integration are not implemented yet. A ready foundation process is not an execution worker.

Use `a13n-service migrate`, `a13n-service bootstrap --email admin@example.com`, and `a13n-service run --role control`. Configuration uses `A13N_SETTINGS_FILE` and nested overrides such as `A13N_DATABASE__URL`. See [the development guide](../../dev/service/README.md) and [current contract](../../spec/a13n-service/README.md).

Legacy is neither installed nor imported. Use separate PostgreSQL, Redis and object namespaces from any old deployment.
