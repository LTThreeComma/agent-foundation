# Agent Foundation

Agent Foundation is an open-source foundation by Converge AI for building agent products and operating agents as internal services.

## Status

The project is currently in its architecture and specification phase. Public APIs and implementation details are not yet stable.

## Planned Components

- `agent-harness`: a reusable process-local agent harness built on Pydantic AI 2, distributed as `converge-agent-harness`
- `logging`: shared pretty and structured logging, distributed as `converge-logging`
- `agent-envd`: an Environment Interaction Protocol provider distributed as the `converge-agent-envd` Rust package
- [`foundation-service`](packages/foundation-service/README.md): an optional hosted control and execution service distributed as `converge-foundation-service`

Applications will be able to embed the harness directly, use the hosted service, or replace providers through documented capability and protocol boundaries.

## Documentation

- Start with the [user documentation](docs/index.md).
- Read the [platform specification](spec/README.md) for the current architecture and design boundaries.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for issue workflow, local setup, testing, and pull-request guidelines. Service implementation, persistence, migration, streaming, logging, and container standards are defined in [DEVELOPMENT.md](DEVELOPMENT.md).

## Maintainers

Review ownership and semantic routing are defined in [MAINTAINERS.md](MAINTAINERS.md).

## License

Licensed under the [Apache License 2.0](LICENSE).

Copyright 2026 Converge AI.
