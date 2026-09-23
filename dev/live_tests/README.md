# Service live journeys

`make live-test` runs the Service as operators deploy it and drives it as clients use it. It needs Docker. Every journey gets its own Service stack:

- a PostgreSQL database cloned from a session template that the installed `a13n-service` CLI migrated and bootstrapped, a cleared Redis and an object directory; the PostgreSQL and Redis containers belong to the session;
- a scripted model process (`dev/fixtures/scripted_model.py`) that answers each OpenAI-compatible request with the turn the journey scripted for it, can hold a request until a gate opens, and records every request with what became of it;
- one Control and two Worker processes started with `a13n-service run`, serving HTTPS with a session certificate, with a 3-second lease and sub-second scans.

Journeys use only the public HTTP API as the bootstrapped administrator or an API key. They signal processes and delete Redis keys to inject faults. Where the API does not show an invariant, such as which worker held an attempt or when its heartbeat ran, they read the database; they never write it. Package tests in `packages/a13n-service/tests` own business rules in-process; these journeys cover what only separate processes show.

| File                   | Journeys                                                                                                                                                                                                           |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `test_execution.py`    | CLI migrate and bootstrap, login, provider/model/agent setup, `Idempotency-Key` replay and conflict, concurrent retries, completion and continuation; the thread SSE stream, and reconnection after Redis loses it |
| `test_recovery.py`     | SIGKILL of the worker holding a run (recovery after lease expiry without repeating committed work), heartbeats through a model slower than the lease, SIGTERM drain with handoff to the other worker               |
| `test_control.py`      | Interrupt of a running run, authority revocation by deleting the grant, approval wait and resume with replay and stale-resume conflict, a steer joining a running run, messages queued behind a client-tool wait   |
| `test_subagents.py`    | An async subagent's child run on its own thread and its result delivered to the parent thread                                                                                                                      |
| `test_contention.py`   | Concurrent submissions to one thread and to many threads across both workers: one active run per thread, FIFO consumption, every input consumed once                                                               |
| `test_environments.py` | A shell tool writing in the run's environment, then stop, restart with the files intact, unmount and delete, for the `local` and native `docker` providers                                                         |

```sh
make live-test                                # every journey
make live-test LIVE_TEST_ARGS="-k recovery"   # a selection; any pytest arguments
make docker-provider-live-test                # build the native Docker image, then run its environment journey
make live-test-check                          # fixture, configuration and selection checks without Docker
```

The `docker` environment journey needs the native execution image (`make image-docker-environment`, or `DOCKER_ENVIRONMENT_IMAGE`) and uses the Engine the Docker CLI uses. Without them it is skipped with the reason in the report; `--require-all`, which CI passes, turns that skip into a failure. Process logs of a failed journey are printed with its report.

[Live Tests CI](../../.github/workflows/ci-live-tests.yml) builds the image, runs every journey with `--require-all` and reports the collected, executed and skipped counts. The [Console browser journey](console-journey.md) is launched separately with `make live-test-console`.
