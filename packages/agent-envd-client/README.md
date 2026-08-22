# Agent Envd Client

`converge-agent-envd-client` is the Python client for the Agent Environment Interaction Protocol (EIP). It belongs to the agent-envd release group and is versioned and published together with `converge-agent-envd`.

## Available surface

The package currently provides:

- generated EIP 1.0 Pydantic wire models, canonical codecs, method metadata, and typed `EIPClient` methods for the complete protocol surface;
- `RequestCoordinator` for bounded request IDs, concurrent response correlation, data-frame routing, typed errors, and no automatic ambiguous retry;
- `StdioTransport` for multiplexed content-length control and binary data frames over trusted parent-supplied asyncio process pipes;
- `EIPSession` for initialization, capability and generation validation, descriptor refresh, and session close;
- high-level `EIPFileReader` and `EIPFileWriter` async context managers, exposed by `EIPSession.open_reader()` and `EIPSession.open_writer()`, for bounded streaming file transfer.

Configured daemons can expose resource reads, atomic mutations, find, search, receipts, and port observation through the generated client. HTTP and WebSocket transports are not implemented yet. Provider process creation and lifecycle policy remain outside this package; a Host adapter or test fixture launches `agent-envd` and supplies its private pipes.

## Example

The process launch below is illustrative fixture code. Production launch configuration belongs to the provider/Host boundary.

```python
import asyncio
import os

from converge_agent_envd_client import EIPSession, StdioTransport


async def main() -> None:
    process = await asyncio.create_subprocess_exec(
        "agent-envd",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "PATH": os.environ["PATH"],
            "AGENT_ENVD_ENVIRONMENT_ID": "env-provider-owned-id",
            # Use this only when an outer sandbox owns containment.
            "AGENT_ENVD_EXECUTION_ISOLATION": "disabled",
        },
    )
    session = await EIPSession.initialize(
        StdioTransport.from_process(process),
        expected_environment_id="env-provider-owned-id",
        required_capabilities=("environment.describe", "session.close"),
    )
    descriptor = await session.describe()
    print(descriptor.generation)
    await session.close()
    await process.wait()


asyncio.run(main())
```

An `EIPMethodError` contains the generated typed `EIPError`. Transport timeouts and cancellation never claim that an already sent operation failed or was absent; the client does not retry a possibly dispatched mutation automatically. Mutation callers can use operation receipts and idempotency keys to reconcile an ambiguous outcome explicitly.

## Versioning

The Python package and daemon artifacts share one `X.Y.Z` agent-envd release identity. The negotiated EIP major and minor version remains an independent wire-compatibility identity.

The accepted protocol, generation, ownership, and compatibility design is documented in the [agent-envd specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/agent-envd/08-protocol-source-client-and-generation.md).

## License

Licensed under the [Apache License 2.0](LICENSE).
