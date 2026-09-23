# Local Service development

This directory owns the local Service workflow. The root Makefile is its stable interface; people and agents use the same commands.

## Quick start

```sh
make dev
```

`make dev` synchronizes locked Python and frontend dependencies, starts this checkout's PostgreSQL and Redis, applies migrations, creates the administrator `admin@example.com` / `local-public-password-123` in an empty database, and starts three applications in the background: the local scripted model (`dev/fixtures/model.py`), the Service (`--role all`) and the Console dev server. It returns once all three accept connections and prints the Console URL. The applications run in their own OS session with no terminal input, so closing the terminal or ending an agent turn does not stop them. Their output goes to `var/dev/logs/`.

Sign in through the printed Console URL, `http://<instance>.localhost:<port>`. Every checkout gets its own host name, because browsers keep one cookie jar per host whatever the port: two checkouts' Consoles hold separate sessions in one browser. Browsers resolve every `*.localhost` name to loopback and treat it as a secure context, which the Service's `Secure` session cookie requires. The Service accepts browser changes only from that origin, so signing in through `http://127.0.0.1:<port>` fails.

A fresh database is empty apart from the administrator. For representative content, run `make dev-reset STATE=seeded` once.

| Command                       | Effect                                                                                      |
| ----------------------------- | ------------------------------------------------------------------------------------------- |
| `make dev`                    | Prepare, then run the model, Service and Console in the background                          |
| `make dev-foreground`         | The same, attached; Ctrl+C stops everything, a second Ctrl+C forces it                      |
| `make service-dev`            | Prepare, then run only the model and Service in the foreground                              |
| `make setup`                  | Prepare stores, schema and administrator without starting applications                      |
| `make dev-stop`               | Stop the running applications and wait until they exit                                      |
| `make dev-status`             | Print this checkout's instance, URLs, ports, listeners and lifecycle owner as JSON          |
| `make dev-down`               | Stop PostgreSQL and Redis, keeping their data                                               |
| `make dev-reset STATE=empty`  | Delete this checkout's data and rebuild the schema and administrator                        |
| `make dev-reset STATE=seeded` | The same, then seed and verify representative content                                       |
| `make dev-env-list`           | List this machine's checkouts, their instances, stores and running applications             |
| `make db-upgrade`, `db-check` | Migrate, or check the migration state of, this checkout's database (`var/dev/service.toml`) |

Do not assume ports: `make dev-status` reports them. It changes nothing and works in a fresh checkout, where it reports `configured: false`.

## Instance and settings

The first command that needs an instance reserves a block of loopback ports for the checkout (Service, Console, model, PostgreSQL, Redis) and records it in `var/dev/instance.json`. A machine lock serializes reservations, and a machine registry (`~/.local/state/agent-foundation/dev/checkouts.json`) keeps blocks disjoint across checkouts; ports that checkouts on the previous local workflow reserved in `instances.json` stay excluded. Assignments never move, because seeded model providers store the model's address. When an assigned port is taken, the command fails and names it; stop the process holding it.

One resolver, `checkout.py`, derives everything local from the instance and writes the Service's ordinary settings file, `var/dev/service.toml`: loopback listener, the Console origin as `server.public_url`, the database and Redis URLs, the object store under `var/dev/objects`, an encryption key generated once per checkout (`var/dev/encryption.key`), outbound access to loopback over plain HTTP for the scripted model, and telemetry. Nothing in `packages/` knows about checkouts. Applications start without inherited `A13N_*` and `OTEL_*` variables, so a deployment shell can neither redirect nor break the local instance.

Each checkout owns the Compose project `a13n-service-dev-<instance>` with its own PostgreSQL and Redis volumes. `make dev-down` stops them; `make dev-reset` deletes and recreates them. A lifecycle lock (`var/dev/lifecycle.lock`) admits one setup, reset, down or application run at a time, so a reset or down is refused while applications run; stop them first.

## Seeded state

`make dev-reset STATE=seeded` rebuilds the database, starts the model and Service temporarily, and creates through the public API, with real execution against the scripted model:

- the organization-wide model provider and model `local-scripted`;
- the `release-notes` skill, uploaded as a package;
- the agents `release-writer` (plain) and `release-reviewer` (with the `local_review` client tool), and the configuration assistant;
- a completed two-message conversation, a run waiting for the `local_review` client tool, and a failed run.

It reads the state back through the API and writes the verification to `var/dev/seed-report.md`; any failed check fails the reset. Seeded agents carry no skill, because skills mount into an environment and the local instance offers none. The scripted model's prompt markers, such as `[client]` and `[fail]`, select its behavior (see `dev/fixtures/model.py`).

## Private development resources

To use real providers in a seeded checkout, copy [dev-resources.example.toml](dev-resources.example.toml) to `~/.a13n/dev-resources.toml`, set its mode to `0600`, and fill in credentials. The file lives outside every checkout and is shared by all of them. It is read only as a regular file owned by the current user and not readable by others; errors name the offending location, never a value.

In a seeded checkout, the seeded reset and every application start (`make dev`, `dev-foreground`, `service-dev`) apply the file through the public API, signed in as the local administrator: model providers and their models, web providers, environment providers and their templates (in the default workspace), and connector providers. Providers are shared with the whole organization. Names identify providers and templates; keys identify models; `configuration` becomes the provider's `config` (a template's recipe). An entry with a blank credential is skipped, together with its models or templates. Removing an entry deletes nothing. Each checkout records a digest per applied provider in `var/dev/dev-resources.json`, so unchanged providers are not resent. A failure is reported and leaves the seeded state and the running applications usable; fix the file and run `make dev` again. Applying the file creates no environment or connection and makes no model call.

## Tracing

With `TRACES=auto` (the default), `make dev`, `make service-dev` and resets export traces to the machine-shared Langfuse (`make langfuse-up`, `http://127.0.0.1:3000`) when it is already running and its local project keys authenticate; otherwise tracing is off. Starting the shared stack takes minutes, so it never starts implicitly: run `make langfuse-up` and restart, or pass `TRACES=langfuse` to start it first. `TRACES=none` turns tracing off. Spans carry the deployment environment `local-<instance>`, which separates checkouts in Langfuse; the Console's trace views query the same backend. The public Langfuse account is `dev@agent-foundation.local` / `agent-foundation-local`.

## Other checkouts

`make dev-env-list` (or `python3 -m dev.service.envs list --json`) lists this repository's worktrees and every registered checkout with its instance, ports, store state and running applications, plus Compose projects of this workflow whose checkout is no longer registered.

```sh
python3 -m dev.service.envs rm ID1 ID2 --dry-run
python3 -m dev.service.envs rm ID1 ID2 --yes
```

Removal stops the checkout's applications, deletes its Compose containers and volumes and its `var/dev`, and releases its port reservation. It keeps the worktree itself and the shared Langfuse. Unregistered projects are listed for investigation only.

## Recovery and checks

- A migration failure after the Service rewrote its migration history: `make dev-reset STATE=empty` or `STATE=seeded` rebuilds this checkout's database.
- An application that exits: its log is in `var/dev/logs/`; the others are stopped with it.
- Docker unavailable: start it and retry; nothing is switched or started implicitly.

`make dev-state-check` lints and type-checks this directory and runs its tests, including one that seeds a disposable instance with its own Compose project (Docker required). `make db-migrate msg="..."` generates migrations against a separate disposable database (`db-migrate.sh`). `make live-test` and `make live-test-console` use their own stores and never touch this checkout's instance.
