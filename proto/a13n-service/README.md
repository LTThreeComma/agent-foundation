# Service contract exports

`make service-contract-generate` exports the current Service without opening process resources:

- `openapi.json`: the HTTP API.

Console HTTP types are regenerated from OpenAPI. Historical exports under `../a13n-service-legacy` are not generation inputs.

`make service-contract-check` checks drift without writes. Independent SDK and remote CLI repositories consume these exports separately; this repository does not build or release their clients. Current exports describe implemented behavior, while the owning specifications retain future rewrite obligations.
