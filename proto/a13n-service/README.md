# Service contract exports

`make service-contract-generate` exports the current Service without opening process resources:

- `openapi.json`: identity, authorized resource selection/creation, immutable Agent revisions and ordinary execution/observation APIs.
- `run-stream.schema.json`: the actual data/control SSE payload models.
- `run-stream.examples.json`: frames serialized by the production SSE formatter, including data, reset, retry-later and closed.

Console HTTP types are regenerated from OpenAPI. Its one observation owner validates SSE framing and attempt/sequence coverage; reset and connection loss require a new durable `/items` boundary. `closed` is an observation signal, never manufactured execution success. Historical exports under `../a13n-service-legacy` are not generation inputs.

`make service-contract-check` checks drift without writes. Independent SDK and remote CLI repositories consume these exports separately; this repository does not build or release their clients. Current exports describe implemented behavior, while the owning specifications retain future rewrite obligations.
