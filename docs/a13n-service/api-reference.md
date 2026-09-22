# Service HTTP reference

This reference is generated from the current new Service OpenAPI export. It currently covers operational probes only; product resource and execution APIs are not implemented. See [the foundation guide](index.md) for the implemented boundary.

Download [the complete OpenAPI JSON](../assets/reference/service-openapi.json). This contract does not advertise legacy API or event schemas.

## other

### `GET /healthz`

Health.

Responses:

- **200** — Successful Response (`application/json: object`).

### `GET /readyz`

Ready.

Responses:

- **200** — Successful Response (`application/json: schema-defined value`).
