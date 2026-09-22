# Service foundation

The Service rewrite currently provides configuration, migrations, initial administrator bootstrap and process health/readiness. Agent execution, public resource management, login and Console flows are not available in this phase. Readiness reports the implemented foundation only.

Use a new PostgreSQL database, separate from every old Service deployment. Run:

```sh
a13n-service migrate
a13n-service bootstrap --email admin@example.com
a13n-service run --role control
```

Bootstrap prompts for a password of at least 12 characters. It creates one organization, default workspace and administrator; a second invocation refuses without changing data. The password is stored as Argon2id, never printed.

`run --role all`, `control` and `worker` start the foundation process. All/control optionally migrate on startup under a bounded PostgreSQL advisory lock. Worker never migrates and refuses an incompatible schema. No role executes runs yet. `/healthz` is liveness; `/readyz` probes database/schema within a configured deadline. `/api/openapi.json` describes the implemented surface.

See [configuration](configuration.md), [generated settings](configuration-reference.md) and [HTTP reference](api-reference.md). Independent SDKs and the remote CLI need new-contract updates in their owning repositories.
