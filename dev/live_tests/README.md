# Service execution journey

Run `make live-test` with Docker available. The journey owns disposable PostgreSQL, Redis, local objects, a deterministic HTTP model, a TLS certificate, one control process and two worker processes. It migrates and bootstraps through the installed CLI, logs in over HTTPS, configures an agent through public APIs, submits and replays input, observes durable completion, and submits a second input against the completed head. A slow model outlives the initial worker lease, exercising independent heartbeat renewal and competing claims. The HTTPS SSE journey deletes the active Redis replay stream and reconstructs from durable observations. Separate cases interrupt a running request and revoke its execution grant while the real HTTP model request remains outstanding.

The command runs `packages/a13n-service/tests/test_live_process.py`. Additional pytest arguments can be passed to `uv run --locked python -m dev.live_tests`. No configured application database, old Service store or shared deployment is used.

This journey covers the implemented execution spine. The Service suite separately exercises exact incorporation/checkpoint crash cuts and public reconstruction. Console recovery, broader resource capabilities and the remaining recovery matrix are still under implementation; this journey alone does not establish full rewrite acceptance.
